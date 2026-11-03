"""Safe dependency upgrade module with CUDA-aware torch handling.

Implements a bottom-up, conflict-aware package upgrade strategy across all
managed Python virtual environments and the Node.js GUI workspace.

Safety model
------------
1. Candidates are vetted by :func:`classify_safe_upgrades`, which combines
   reverse-dependency analysis (via ``pip inspect``) with constraint-
   preserving pip dry-runs.
2. Every batch is snapshotted via ``pip freeze`` before being applied.
3. Each candidate is installed pinned to the vetted version
   (``name==latest_version``), so the apply phase cannot pick up a newer
   release than the one the dry-run checked.
4. After applying, ``pip check`` is run. If the install or the check fails,
   the batch is rolled back from the snapshot and reported as a failure.

CUDA handling
-------------
The PyTorch CUDA index URL returned by :func:`torch_upgrade_index` is the
single source of truth for CUDA availability; the audit, the plan and the
pipeline all use it, and :func:`apply_upgrade_plan` reuses the index stored
in the plan. When present, torch-family packages are always resolved
against it using ``--index-url`` (not ``--extra-index-url``). After
upgrade, ``torch.cuda.is_available()`` is verified.

Only torch-family packages that are *already installed* in a project are
considered for upgrade. This prevents the CUDA index dry-run from
silently adding packages the project never declared (e.g. torchaudio
into a project that only uses torch/torchvision).
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from collections.abc import Generator

from env_manager._logging import get_logger
from env_manager.dependency_resolver import (
    OutdatedPackage,
    classify_safe_upgrades,
    constraint_preserving_dry_run,
    find_outdated_packages,
)
from env_manager.system_probe import HardwareProfile, probe_hardware
from env_manager.utils import WINDOWS_NO_WINDOW, run_pip_with_recovery
from env_manager.venv_manager import (
    discover_projects,
    get_installed_packages,
    get_venv_python_path,
    is_venv_valid,
)
from env_manager.workspace import workspace_root

_log = get_logger(__name__)


# ─── Constants ──────────────────────────────────────────────────────────────

TORCH_FAMILY: tuple[str, ...] = ("torch", "torchvision", "torchaudio")

UPGRADE_ORDER: list[str] = [
    "lemgendary-env-manager",
    "lemgendary-datasets",
    "lemgendary-training-suite",
    "lemgendary-docs",
    "lemgendary-ai-studio-gui",
]

# Paired packages where manual curation matters more than "latest wins".
PROTECTED_FROM_SYNC: set = {
    "ruff",
    "pydantic",
    "pydantic-core",
    "pydantic_core",
}


# ─── Data structures ────────────────────────────────────────────────────────

@dataclass
class ProjectUpgradePlan:
    """Per-project upgrade plan."""
    name: str
    project_dir: str
    is_node_project: bool
    outdated_packages: list[OutdatedPackage]
    safe_packages: list[OutdatedPackage] = field(default_factory=list)
    blocked_packages: list[tuple[OutdatedPackage, str]] = field(default_factory=list)
    torch_cuda_packages: list[OutdatedPackage] = field(default_factory=list)
    has_updates: bool = False
    has_safe_updates: bool = False
    cuda_available: bool = False
    cuda_index_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a dict, pairing each blocked package with its reason."""
        d = asdict(self)
        d["blocked_packages"] = [
            {"package": asdict(pkg), "reason": reason}
            for pkg, reason in self.blocked_packages
        ]
        return d


@dataclass
class EcosystemUpgradePlan:
    """Full ecosystem upgrade plan across all managed projects."""
    projects: list[ProjectUpgradePlan]
    total_outdated: int
    total_safe: int
    total_blocked: int
    has_any_updates: bool
    has_any_safe_updates: bool
    cuda_detected: bool = False
    cuda_index_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a plain dict for JSON output."""
        return asdict(self)


@dataclass
class UpgradeEvent:
    """Single telemetry event from the upgrade pipeline."""
    project: str
    package: str
    old_version: str
    new_version: str
    status: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a plain dict for JSON output."""
        return asdict(self)


# ─── Normalization ──────────────────────────────────────────────────────────

def _normalize(name: str) -> str:
    return name.lower().replace("-", "_").replace(".", "_")


def _is_torch_family(name: str) -> bool:
    return _normalize(name) in {_normalize(t) for t in TORCH_FAMILY}


def _venv_package_map(project_dir: Path) -> dict[str, str]:
    return {_normalize(k): v for k, v in get_installed_packages(project_dir).items()}


# ─── CUDA detection & verification ──────────────────────────────────────────

def torch_upgrade_index(hw: HardwareProfile) -> str | None:
    """Return the GPU torch index to vet and apply upgrades against, or None.

    None means the host has no CUDA or ROCm backend, so torch-family
    packages are vetted like any other PyPI package. ``recommended_torch_index``
    is always set (it falls back to the CPU index), so it must not be used to
    decide whether a GPU upgrade path exists.
    """
    backend = (hw.primary_backend or "").lower()
    if backend in ("cuda", "rocm") and hw.recommended_torch_index:
        return str(hw.recommended_torch_index)
    return None


def _detect_cuda() -> tuple[str | None, str]:
    """Return (torch_index_url_or_None, human_description)."""
    hw = probe_hardware()
    index_url = torch_upgrade_index(hw)
    accel = ", ".join(a.name for a in hw.accelerators) or "none"
    if index_url:
        return index_url, f"{(hw.primary_backend or '').upper()} via {accel}"
    return None, f"no CUDA backend ({accel})"


def pinned_specs(packages: list[OutdatedPackage]) -> list[str]:
    """Return ``name==latest_version`` requirement specs for vetted candidates."""
    return [f"{pkg.name}=={pkg.latest_version}" for pkg in packages]


def _torch_installed(project_dir: Path) -> bool:
    return "torch" in _venv_package_map(project_dir)


def _verify_torch_cuda_runtime(project_dir: Path) -> tuple[bool, str]:
    if not is_venv_valid(project_dir):
        return False, "invalid venv"
    python_path = get_venv_python_path(project_dir)
    try:
        proc = subprocess.run(
            [str(python_path), "-c",
             "import torch; print(torch.__version__); print(int(torch.cuda.is_available()))"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, check=False,
            creationflags=WINDOWS_NO_WINDOW,
        )
        if proc.returncode != 0:
            return False, (proc.stderr or "torch import failed").strip()[:200]
        lines = (proc.stdout or "").strip().splitlines()
        *_, version, cuda_flag = ["?", "?", *lines]
        if cuda_flag == "1":
            return True, f"torch {version} CUDA runtime verified"
        return False, f"torch {version} reports cuda.is_available() == False"
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("torch CUDA runtime check failed for %s: %s", project_dir, exc)
        return False, str(exc)


def _pip_check(project_dir: Path) -> tuple[bool, str]:
    if not is_venv_valid(project_dir):
        return True, "no venv"
    python_path = get_venv_python_path(project_dir)
    proc = run_pip_with_recovery(python_path, ["check"], timeout=60)
    if proc.returncode == 0:
        return True, "all dependency constraints satisfied"
    return False, (proc.stdout or proc.stderr or "").strip()[:400] or "pip check failed"


# ─── Snapshot / rollback ────────────────────────────────────────────────────

def _snapshot_freeze(project_dir: Path) -> str | None:
    if not is_venv_valid(project_dir):
        return None
    python_path = get_venv_python_path(project_dir)
    proc = run_pip_with_recovery(python_path, ["freeze"], timeout=60)
    if proc.returncode == 0 and proc.stdout.strip():
        return proc.stdout
    _log.warning("pip freeze failed for %s (exit %s): %s",
                 project_dir, proc.returncode, (proc.stderr or "").strip()[-300:])
    return None


def _restore_freeze(project_dir: Path, freeze_text: str) -> tuple[bool, str]:
    if not is_venv_valid(project_dir):
        return False, "invalid venv"

    fd, path = tempfile.mkstemp(prefix="pip_restore_", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(freeze_text)

        python_path = get_venv_python_path(project_dir)
        proc = run_pip_with_recovery(
            python_path,
            ["install", "--force-reinstall", "--no-deps", "-r", path],
            timeout=1800,
        )
        if proc.returncode == 0:
            return True, "restored from snapshot"
        return False, (proc.stderr or proc.stdout)[:300]
    except OSError as exc:
        _log.error("Cannot write restore snapshot for %s: %s", project_dir, exc)
        return False, str(exc)
    finally:
        try:
            os.unlink(path)
        except OSError as exc:
            _log.debug("Failed to unlink temporary snapshot file %s: %s", path, exc)


# ─── CUDA-aware torch-family dry-run ────────────────────────────────────────

def _check_torch_cuda_upgrades(
    project_dir: Path,
    cuda_index_url: str,
    current: dict[str, str],
) -> tuple[list[OutdatedPackage], str]:
    """Return (candidates, reason) for torch-family CUDA upgrades.

    Only considers torch-family packages that are *already installed* in the
    project. Running the CUDA-index dry-run against all three names would
    cause pip to install packages the project never asked for (e.g.
    torchaudio into a project that only uses torch/torchvision).

    The returned candidates only include packages whose version would
    actually change. Packages already at the newest CUDA build are omitted.
    """
    installed_torch = [
        name for name in TORCH_FAMILY if _normalize(name) in current
    ]
    if not installed_torch:
        return [], "no torch-family packages installed"

    ok, changed, msg = constraint_preserving_dry_run(
        project_dir,
        installed_torch,
        current_state=current,
        index_url=cuda_index_url,
    )
    if not ok:
        return [], f"CUDA dry-run failed: {msg}"

    candidates: list[OutdatedPackage] = []
    for pkg_name in installed_torch:
        key = _normalize(pkg_name)
        new_ver = changed.get(key)
        if new_ver is None:
            continue
        old_ver = current.get(key, "")
        if not old_ver or old_ver == new_ver:
            # Not an upgrade: either not previously installed, or already
            # at the newest available CUDA build. Skip.
            continue
        candidates.append(OutdatedPackage(
            name=pkg_name,
            current_version=old_ver,
            latest_version=new_ver,
            package_type="wheel-cuda",
        ))

    reason = f"CUDA index checked ({len(installed_torch)} installed)"
    return candidates, reason


# ─── npm helpers ────────────────────────────────────────────────────────────

def _find_npm_outdated(project_dir: Path) -> list[OutdatedPackage]:
    npm_path = shutil.which("npm")
    if not npm_path:
        return []
    if not (project_dir / "package.json").exists():
        return []
    try:
        proc = subprocess.run(
            [npm_path, "outdated", "--json"],
            cwd=str(project_dir),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=90, check=False,
            creationflags=WINDOWS_NO_WINDOW,
        )
        if proc.stdout.strip():
            data = json.loads(proc.stdout.strip())
            if not isinstance(data, dict):
                _log.warning("npm outdated for %s returned non-object JSON", project_dir)
                return []
            return [
                OutdatedPackage(
                    name=name,
                    current_version=str(info.get("current", "")),
                    latest_version=str(info.get("latest", "")),
                    package_type="npm",
                )
                for name, info in data.items()
                if isinstance(info, dict)
            ]
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        _log.warning("npm outdated query failed for %s: %s", project_dir, exc)
    return []


def _apply_npm_update(project_dir: Path) -> tuple[bool, str]:
    npm_path = shutil.which("npm")
    if not npm_path:
        return False, "npm not found on PATH."
    try:
        proc = subprocess.run(
            [npm_path, "update"],
            cwd=str(project_dir),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300, check=False,
            creationflags=WINDOWS_NO_WINDOW,
        )
        if proc.returncode == 0:
            return True, proc.stdout
        return False, proc.stderr or proc.stdout or "npm update failed."
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("npm update failed for %s: %s", project_dir, exc)
        return False, str(exc)


# ─── pip apply ──────────────────────────────────────────────────────────────

def _apply_pip_upgrade(
    project_dir: Path,
    specs: list[str],
    extra_index_url: str | None = None,
) -> tuple[bool, str]:
    """Install the given requirement specs (``name==version``) into the venv."""
    if not is_venv_valid(project_dir) or not specs:
        return True, "No packages to upgrade."
    python_path = get_venv_python_path(project_dir)
    args = ["install", *specs]
    if extra_index_url:
        args.extend(["--extra-index-url", extra_index_url])
    proc = run_pip_with_recovery(python_path, args, timeout=1800)
    if proc.returncode == 0:
        return True, proc.stdout or "ok"
    return False, (proc.stderr or proc.stdout or "pip upgrade failed.")[:500]


def _apply_torch_cuda_upgrade(
    project_dir: Path,
    cuda_index_url: str,
    specs: list[str],
) -> tuple[bool, str]:
    """Install the vetted torch-family specs (``name==version``) from the CUDA index."""
    if not is_venv_valid(project_dir):
        return False, "invalid venv"
    if not specs:
        return True, "no torch-family packages to upgrade"

    python_path = get_venv_python_path(project_dir)
    args = ["install", "--index-url", cuda_index_url, *specs]
    proc = run_pip_with_recovery(python_path, args, timeout=2400)
    if proc.returncode == 0:
        return True, proc.stdout or "ok"
    return False, (proc.stderr or proc.stdout or "torch CUDA upgrade failed.")[:500]


# ─── Plan builder ───────────────────────────────────────────────────────────

def build_upgrade_plan(
    base_dir: Path | None = None,
    project: str | None = None,
) -> EcosystemUpgradePlan:
    """Build a safety-checked, CUDA-aware upgrade plan.

    With ``project`` set, the plan covers only that project. Raises
    ValueError if that project is not found under ``base_dir``.
    """
    if base_dir is None:
        base_dir = workspace_root()

    cuda_index_url, cuda_desc = _detect_cuda()
    cuda_ok = cuda_index_url is not None
    _log.info("CUDA detection: %s (index=%s)", cuda_desc, cuda_index_url)

    projects = discover_projects(base_dir)
    if project is not None:
        projects = [p for p in projects if p.name == project]
        if not projects:
            raise ValueError(f"Project not found: {project}")
    order_map = {name: i for i, name in enumerate(UPGRADE_ORDER)}
    projects_sorted = sorted(projects, key=lambda p: order_map.get(p.name, 999))

    plan_items: list[ProjectUpgradePlan] = []
    total_outdated = 0
    total_safe = 0
    total_blocked = 0

    for p in projects_sorted:
        p_dir = Path(p.project_dir)

        if p.is_node_project:
            outdated = _find_npm_outdated(p_dir)
            plan_items.append(ProjectUpgradePlan(
                name=p.name, project_dir=str(p_dir), is_node_project=True,
                outdated_packages=outdated, safe_packages=list(outdated),
                blocked_packages=[], has_updates=len(outdated) > 0,
                has_safe_updates=len(outdated) > 0,
            ))
            total_outdated += len(outdated)
            total_safe += len(outdated)
            continue

        current = _venv_package_map(p_dir)
        raw = find_outdated_packages(p_dir)

        torch_raw: list[OutdatedPackage] = []
        non_torch_raw: list[OutdatedPackage] = []
        for pkg in raw:
            if _is_torch_family(pkg.name):
                torch_raw.append(pkg)
            else:
                non_torch_raw.append(pkg)

        safe_pkgs, blocked = classify_safe_upgrades(
            p_dir, non_torch_raw, cuda_index_url=cuda_index_url,
        )

        torch_cuda_candidates: list[OutdatedPackage] = []
        if cuda_index_url is not None and _torch_installed(p_dir):
            torch_cuda_candidates, _reason = _check_torch_cuda_upgrades(
                p_dir, cuda_index_url, current,
            )
            safe_pkgs.extend(torch_cuda_candidates)
        elif torch_raw:
            torch_safe, torch_blocked = classify_safe_upgrades(p_dir, torch_raw)
            safe_pkgs.extend(torch_safe)
            blocked.extend(torch_blocked)

        outdated_all = list(safe_pkgs) + [pkg for pkg, _ in blocked]
        seen: set = set()
        deduped: list[OutdatedPackage] = []
        for pkg in outdated_all:
            key = _normalize(pkg.name)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(pkg)

        plan_items.append(ProjectUpgradePlan(
            name=p.name, project_dir=str(p_dir), is_node_project=False,
            outdated_packages=deduped, safe_packages=safe_pkgs,
            blocked_packages=blocked, torch_cuda_packages=torch_cuda_candidates,
            has_updates=len(deduped) > 0, has_safe_updates=len(safe_pkgs) > 0,
            cuda_available=cuda_ok, cuda_index_url=cuda_index_url,
        ))
        total_outdated += len(deduped)
        total_safe += len(safe_pkgs)
        total_blocked += len(blocked)

    return EcosystemUpgradePlan(
        projects=plan_items, total_outdated=total_outdated,
        total_safe=total_safe, total_blocked=total_blocked,
        has_any_updates=total_outdated > 0, has_any_safe_updates=total_safe > 0,
        cuda_detected=cuda_ok, cuda_index_url=cuda_index_url,
    )


# ─── Applier ────────────────────────────────────────────────────────────────

def _rollback(project_dir: Path, freeze: str | None, failure: str) -> tuple[bool, str, bool]:
    """Restore ``freeze`` after ``failure``; return the failed batch result."""
    if freeze is None:
        return False, f"{failure}; no snapshot available for rollback", False
    restore_ok, restore_msg = _restore_freeze(project_dir, freeze)
    if restore_ok:
        return False, f"{failure}; rolled back successfully", True
    return False, f"{failure}; rollback also failed: {restore_msg}", False


def _apply_batch_with_rollback(
    project_dir: Path,
    specs: list[str],
    extra_index_url: str | None = None,
) -> tuple[bool, str, bool]:
    """Apply ``specs`` and roll back if the install or ``pip check`` fails.

    Returns ``(ok, message, rolled_back)``.
    """
    freeze = _snapshot_freeze(project_dir)

    ok, apply_msg = _apply_pip_upgrade(project_dir, specs, extra_index_url)
    if not ok:
        return _rollback(project_dir, freeze, f"pip install failed ({apply_msg})")

    check_ok, check_msg = _pip_check(project_dir)
    if check_ok:
        return True, apply_msg, False
    return _rollback(project_dir, freeze, f"pip check failed ({check_msg})")


def _apply_torch_candidates(
    project: str,
    p_dir: Path,
    cuda_index_url: str,
    torch_safe: list[OutdatedPackage],
) -> Generator[UpgradeEvent, None, None]:
    """Install the vetted torch-family versions from the CUDA index, with rollback."""
    freeze = _snapshot_freeze(p_dir)
    ok, msg = _apply_torch_cuda_upgrade(p_dir, cuda_index_url, pinned_specs(torch_safe))
    if ok:
        check_ok, check_msg = _pip_check(p_dir)
        if check_ok:
            for pkg in torch_safe:
                yield UpgradeEvent(
                    project=project, package=pkg.name,
                    old_version=pkg.current_version, new_version=pkg.latest_version,
                    status="upgraded",
                    message=f"CUDA upgrade {pkg.current_version} -> {pkg.latest_version}.",
                )
            return
        failure = f"torch CUDA upgrade broke pip check ({check_msg})"
    else:
        failure = f"CUDA upgrade failed ({msg[:300]})"
    _, message, _ = _rollback(p_dir, freeze, failure)
    yield UpgradeEvent(
        project=project, package="torch-family",
        old_version="", new_version="", status="failed", message=message[:400],
    )


def apply_upgrade_plan(
    plan: EcosystemUpgradePlan,
    base_dir: Path | None = None,
    extra_index_url: str | None = None,
) -> Generator[UpgradeEvent, None, None]:
    """Apply the plan bottom-up and yield telemetry events.

    Only the vetted candidates are installed, each pinned to the version the
    plan checked. The GPU torch index is the one stored in the plan;
    ``extra_index_url`` defaults to it.
    """
    if base_dir is None:
        base_dir = workspace_root()

    cuda_index_url = plan.cuda_index_url
    if extra_index_url is None:
        extra_index_url = cuda_index_url

    for proj_plan in plan.projects:
        p_dir = Path(proj_plan.project_dir)

        for pkg, reason in proj_plan.blocked_packages:
            yield UpgradeEvent(
                project=proj_plan.name, package=pkg.name,
                old_version=pkg.current_version, new_version=pkg.latest_version,
                status="blocked", message=reason,
            )

        if proj_plan.is_node_project:
            if not proj_plan.safe_packages:
                yield UpgradeEvent(
                    project=proj_plan.name, package="(all)", old_version="",
                    new_version="", status="skipped",
                    message="All npm packages are up to date.",
                )
                continue
            ok, msg = _apply_npm_update(p_dir)
            yield UpgradeEvent(
                project=proj_plan.name, package="(npm packages)",
                old_version="", new_version="",
                status="upgraded" if ok else "failed",
                message=(msg or "")[:300],
            )
            continue

        # On a GPU host torch-family candidates come from the CUDA index and
        # are applied separately; otherwise they were vetted against PyPI
        # like any other package and go in the regular batch.
        if cuda_index_url is None:
            non_torch = list(proj_plan.safe_packages)
            torch_safe: list[OutdatedPackage] = []
        else:
            non_torch = [p for p in proj_plan.safe_packages if not _is_torch_family(p.name)]
            torch_safe = [p for p in proj_plan.safe_packages if _is_torch_family(p.name)]

        if non_torch:
            ok, msg, rolled_back = _apply_batch_with_rollback(
                p_dir, pinned_specs(non_torch), extra_index_url=extra_index_url,
            )
            if ok:
                for pkg in non_torch:
                    yield UpgradeEvent(
                        project=proj_plan.name, package=pkg.name,
                        old_version=pkg.current_version, new_version=pkg.latest_version,
                        status="upgraded",
                        message=f"Upgraded {pkg.current_version} -> {pkg.latest_version}.",
                    )
            else:
                yield UpgradeEvent(
                    project=proj_plan.name, package="(batch)",
                    old_version="", new_version="", status="failed",
                    message=msg[:400],
                )
                if rolled_back:
                    continue
        elif not torch_safe and not proj_plan.has_updates:
            yield UpgradeEvent(
                project=proj_plan.name, package="(all)", old_version="",
                new_version="", status="skipped",
                message="All packages are up to date.",
            )

        # ── Torch-family CUDA upgrade ──
        # Runs only when the plan vetted torch-family candidates against the
        # CUDA index, and installs exactly those versions.
        if cuda_index_url is not None and torch_safe:
            yield from _apply_torch_candidates(
                proj_plan.name, p_dir, cuda_index_url, torch_safe,
            )

        check_ok, check_msg = _pip_check(p_dir)
        yield UpgradeEvent(
            project=proj_plan.name, package="(pip check)",
            old_version="", new_version="",
            status="verified" if check_ok else "failed",
            message=check_msg[:300],
        )

        if cuda_index_url is not None and _torch_installed(p_dir):
            torch_ok, torch_msg = _verify_torch_cuda_runtime(p_dir)
            yield UpgradeEvent(
                project=proj_plan.name, package="torch-cuda-verify",
                old_version="", new_version="",
                status="verified" if torch_ok else "failed",
                message=torch_msg,
            )

    sync_results = sync_manifests_from_venvs(base_dir)
    for proj_name, (ok, sync_msg) in sync_results.items():
        yield UpgradeEvent(
            project=proj_name, package="(manifest sync)",
            old_version="", new_version="",
            status="upgraded" if ok else "failed",
            message=sync_msg,
        )


# ─── Manifest sync ──────────────────────────────────────────────────────────

def sync_manifests_from_venvs(
    base_dir: Path | None = None,
) -> dict[str, tuple[bool, str]]:
    """Read installed versions from each venv and write back to manifests.

    Packages in :data:`PROTECTED_FROM_SYNC` are passed through untouched,
    regardless of the venv's current state. This prevents a transient
    install-time drift from permanently corrupting a manifest.
    """
    if base_dir is None:
        base_dir = workspace_root()

    env_mgr_dir = base_dir / "lemgendary-env-manager"
    manifests_dir = env_mgr_dir / "requirements"
    manifests_dir.mkdir(parents=True, exist_ok=True)

    project_to_manifest = {
        "lemgendary-training-suite": "requirements-training.txt",
        "lemgendary-datasets": "requirements-datasets.txt",
        "lemgendary-env-manager": "requirements-env-manager.txt",
        "lemgendary-docs": "requirements-documentation.txt",
    }

    results: dict[str, tuple[bool, str]] = {}

    for proj_name, manifest_name in project_to_manifest.items():
        p_dir = base_dir / proj_name
        if not p_dir.exists():
            results[proj_name] = (False, f"Project directory not found: {p_dir}")
            continue
        if not is_venv_valid(p_dir):
            results[proj_name] = (False, "No valid venv found.")
            continue

        installed = get_installed_packages(p_dir)
        if not installed:
            results[proj_name] = (False, "No installed packages.")
            continue

        manifest_path = manifests_dir / manifest_name
        existing_lines: list[str] = []
        if manifest_path.exists():
            existing_lines = manifest_path.read_text(encoding="utf-8").splitlines()

        new_lines: list[str] = []
        for line in existing_lines:
            stripped = line.strip()
            if (
                not stripped
                or stripped.startswith("#")
                or stripped.startswith("--extra-index-url")
                or stripped.startswith("--index-url")
                or stripped.startswith("-i ")
            ):
                new_lines.append(line)
                continue

            match = re.match(r"^([A-Za-z0-9_\-\.]+)", stripped)
            if not match:
                new_lines.append(line)
                continue

            raw_name = match.group(1)
            pkg_name = raw_name.lower().replace("_", "-").replace(".", "-")

            if pkg_name in PROTECTED_FROM_SYNC:
                new_lines.append(line)
                continue

            installed_ver = (
                installed.get(pkg_name)
                or installed.get(raw_name.lower())
            )
            if installed_ver:
                marker_match = re.search(r";(.+)$", stripped)
                marker = f"; {marker_match.group(1).strip()}" if marker_match else ""
                new_lines.append(f"{raw_name}=={installed_ver}{marker}")
                continue

            new_lines.append(line)

        try:
            manifest_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            shutil.copy2(manifest_path, p_dir / "requirements.txt")
            results[proj_name] = (True, f"Manifest {manifest_name} updated and synced.")
        except OSError as exc:
            _log.error("Manifest sync for %s failed: %s", proj_name, exc)
            results[proj_name] = (False, str(exc))

    return results
