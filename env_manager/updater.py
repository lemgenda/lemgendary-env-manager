"""Safe dependency upgrade module with CUDA-aware torch handling.

Implements a bottom-up, conflict-aware package upgrade strategy across all
managed Python virtual environments and the Node.js GUI workspace.

Safety model
------------
1. Candidates are vetted by :func:`classify_safe_upgrades`, which combines
   reverse-dependency analysis (via ``pip inspect``) with constraint-
   preserving pip dry-runs.
2. Every batch is snapshotted via ``pip freeze`` before being applied.
3. After applying, ``pip check`` is run. If it fails, the batch is rolled
   back from the snapshot and reported as a failure with a reason.

CUDA handling
-------------
The PyTorch CUDA index URL is the single source of truth for CUDA
availability. When present, torch-family packages are always resolved
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
from typing import Any, Dict, Generator, List, Optional, Tuple

from env_manager._logging import get_logger
from env_manager.dependency_resolver import (
    OutdatedPackage,
    classify_safe_upgrades,
    find_outdated_packages,
)
from env_manager.utils import pip_env, run_pip_with_recovery
from env_manager.venv_manager import (
    discover_projects,
    get_installed_packages,
    get_venv_python_path,
    is_venv_valid,
)

_log = get_logger(__name__)


# ─── Constants ──────────────────────────────────────────────────────────────

TORCH_FAMILY: Tuple[str, ...] = ("torch", "torchvision", "torchaudio")

UPGRADE_ORDER: List[str] = [
    "lemgendary-env-manager",
    "lemgendary-datasets",
    "lemgendary-training-suite",
    "lemgendary-ai-studio-gui",
]

# Paired packages where manual curation matters more than "latest wins".
PROTECTED_FROM_SYNC: set = {
    "astroid",
    "pylint",
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
    outdated_packages: List[OutdatedPackage]
    safe_packages: List[OutdatedPackage] = field(default_factory=list)
    blocked_packages: List[Tuple[OutdatedPackage, str]] = field(default_factory=list)
    torch_cuda_packages: List[OutdatedPackage] = field(default_factory=list)
    has_updates: bool = False
    has_safe_updates: bool = False
    cuda_available: bool = False
    cuda_index_url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["blocked_packages"] = [
            {"package": asdict(pkg), "reason": reason}
            for pkg, reason in self.blocked_packages
        ]
        return d


@dataclass
class EcosystemUpgradePlan:
    """Full ecosystem upgrade plan across all managed projects."""
    projects: List[ProjectUpgradePlan]
    total_outdated: int
    total_safe: int
    total_blocked: int
    has_any_updates: bool
    has_any_safe_updates: bool
    cuda_detected: bool = False
    cuda_index_url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
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

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─── Normalization ──────────────────────────────────────────────────────────

def _normalize(name: str) -> str:
    return name.lower().replace("-", "_").replace(".", "_")


def _is_torch_family(name: str) -> bool:
    return _normalize(name) in {_normalize(t) for t in TORCH_FAMILY}


def _venv_package_map(project_dir: Path) -> Dict[str, str]:
    return {_normalize(k): v for k, v in get_installed_packages(project_dir).items()}


# ─── CUDA detection & verification ──────────────────────────────────────────

def _detect_cuda() -> Tuple[Optional[str], str]:
    """Return (torch_index_url_or_None, human_description).

    The index URL is the single source of truth for CUDA availability.
    """
    try:
        from env_manager.system_probe import probe_hardware
        hw = probe_hardware()
        backend = (getattr(hw, "primary_backend", "") or "").lower()
        index_url = getattr(hw, "recommended_torch_index", None)
        accel = ", ".join(a.name for a in getattr(hw, "accelerators", [])) or "none"
        if backend in ("cuda", "rocm") and index_url:
            return str(index_url), f"{backend.upper()} via {accel}"
        return None, f"no CUDA backend ({accel})"
    except Exception as exc:
        _log.debug("CUDA detection failed: %s", exc)
        return None, "detection error"


def _torch_installed(project_dir: Path) -> bool:
    return "torch" in _venv_package_map(project_dir)


def _verify_torch_cuda_runtime(project_dir: Path) -> Tuple[bool, str]:
    if not is_venv_valid(project_dir):
        return False, "invalid venv"
    python_path = get_venv_python_path(project_dir)
    try:
        proc = subprocess.run(
            [str(python_path), "-c",
             "import torch; print(torch.__version__); print(int(torch.cuda.is_available()))"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, check=False,
        )
        if proc.returncode != 0:
            return False, (proc.stderr or "torch import failed").strip()[:200]
        lines = (proc.stdout or "").strip().splitlines()
        version = lines[-2] if len(lines) >= 2 else "?"
        cuda_flag = lines[-1] if lines else "0"
        if cuda_flag == "1":
            return True, f"torch {version} CUDA runtime verified"
        return False, f"torch {version} reports cuda.is_available() == False"
    except Exception as exc:
        return False, str(exc)


def _pip_check(project_dir: Path) -> Tuple[bool, str]:
    if not is_venv_valid(project_dir):
        return True, "no venv"
    python_path = get_venv_python_path(project_dir)
    try:
        proc = run_pip_with_recovery(python_path, ["check"], timeout=60)
        if proc.returncode == 0:
            return True, "all dependency constraints satisfied"
        return False, (proc.stdout or proc.stderr or "").strip()[:400] or "pip check failed"
    except Exception as exc:
        return False, str(exc)


# ─── Snapshot / rollback ────────────────────────────────────────────────────

def _snapshot_freeze(project_dir: Path) -> Optional[str]:
    if not is_venv_valid(project_dir):
        return None
    python_path = get_venv_python_path(project_dir)
    try:
        proc = run_pip_with_recovery(python_path, ["freeze"], timeout=60)
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout
    except Exception as exc:
        _log.debug("pip freeze failed for %s: %s", project_dir, exc)
    return None


def _restore_freeze(project_dir: Path, freeze_text: str) -> Tuple[bool, str]:
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
    except Exception as exc:
        return False, str(exc)
    finally:
        try:
            os.unlink(path)
        except Exception:
            pass


# ─── CUDA-aware torch-family dry-run ────────────────────────────────────────

def _check_torch_cuda_upgrades(
    project_dir: Path,
    cuda_index_url: str,
    current: Dict[str, str],
) -> Tuple[List[OutdatedPackage], str]:
    """Return (candidates, reason) for torch-family CUDA upgrades.

    Only considers torch-family packages that are *already installed* in the
    project. Running the CUDA-index dry-run against all three names would
    cause pip to install packages the project never asked for (e.g.
    torchaudio into a project that only uses torch/torchvision).

    The returned candidates only include packages whose version would
    actually change. Packages already at the newest CUDA build are omitted.
    """
    from env_manager.dependency_resolver import constraint_preserving_dry_run

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

    candidates: List[OutdatedPackage] = []
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

def _find_npm_outdated(project_dir: Path) -> List[OutdatedPackage]:
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
        )
        if proc.stdout.strip():
            data = json.loads(proc.stdout.strip())
            return [
                OutdatedPackage(
                    name=name,
                    current_version=str(info.get("current", "")),
                    latest_version=str(info.get("latest", "")),
                    package_type="npm",
                )
                for name, info in data.items()
            ]
    except Exception as exc:
        _log.warning("npm outdated query failed for %s: %s", project_dir, exc)
    return []


def _apply_npm_update(project_dir: Path) -> Tuple[bool, str]:
    npm_path = shutil.which("npm")
    if not npm_path:
        return False, "npm not found on PATH."
    try:
        proc = subprocess.run(
            [npm_path, "update"],
            cwd=str(project_dir),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300, check=False,
        )
        if proc.returncode == 0:
            return True, proc.stdout
        return False, proc.stderr or proc.stdout or "npm update failed."
    except Exception as exc:
        return False, str(exc)


# ─── pip apply ──────────────────────────────────────────────────────────────

def _apply_pip_upgrade(
    project_dir: Path,
    packages: List[str],
    extra_index_url: Optional[str] = None,
) -> Tuple[bool, str]:
    if not is_venv_valid(project_dir) or not packages:
        return True, "No packages to upgrade."
    python_path = get_venv_python_path(project_dir)
    args = ["install", "--upgrade"] + packages
    if extra_index_url:
        args.extend(["--extra-index-url", extra_index_url])
    proc = run_pip_with_recovery(python_path, args, timeout=1800)
    if proc.returncode == 0:
        return True, proc.stdout or "ok"
    return False, (proc.stderr or proc.stdout or "pip upgrade failed.")[:500]


def _apply_torch_cuda_upgrade(
    project_dir: Path,
    cuda_index_url: str,
    installed_names: Optional[List[str]] = None,
) -> Tuple[bool, str]:
    """Reinstall the installed subset of torch-family from the CUDA index.

    ``installed_names`` limits the upgrade to packages already present in
    the venv. If None, all of TORCH_FAMILY is passed to pip (only safe
    when the caller has already verified which ones are installed).
    """
    if not is_venv_valid(project_dir):
        return False, "invalid venv"

    targets = installed_names or list(TORCH_FAMILY)
    if not targets:
        return True, "no torch-family packages to upgrade"

    python_path = get_venv_python_path(project_dir)
    args = [
        "install",
        "--upgrade",
        "--index-url", cuda_index_url,
        *targets,
    ]
    proc = run_pip_with_recovery(python_path, args, timeout=2400)
    if proc.returncode == 0:
        return True, proc.stdout or "ok"
    return False, (proc.stderr or proc.stdout or "torch CUDA upgrade failed.")[:500]


# ─── Plan builder ───────────────────────────────────────────────────────────

def build_upgrade_plan(base_dir: Optional[Path] = None) -> EcosystemUpgradePlan:
    """Build a safety-checked, CUDA-aware upgrade plan for every project."""
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent.parent

    cuda_index_url, cuda_desc = _detect_cuda()
    cuda_ok = cuda_index_url is not None
    _log.info("CUDA detection: %s (index=%s)", cuda_desc, cuda_index_url)

    projects = discover_projects(base_dir)
    order_map = {name: i for i, name in enumerate(UPGRADE_ORDER)}
    projects_sorted = sorted(projects, key=lambda p: order_map.get(p.name, 999))

    plan_items: List[ProjectUpgradePlan] = []
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

        torch_raw: List[OutdatedPackage] = []
        non_torch_raw: List[OutdatedPackage] = []
        for pkg in raw:
            if _is_torch_family(pkg.name):
                torch_raw.append(pkg)
            else:
                non_torch_raw.append(pkg)

        safe_pkgs, blocked = classify_safe_upgrades(
            p_dir, non_torch_raw, cuda_index_url=cuda_index_url,
        )

        torch_cuda_candidates: List[OutdatedPackage] = []
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
        deduped: List[OutdatedPackage] = []
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

def _apply_batch_with_rollback(
    project_dir: Path,
    packages: List[str],
    extra_index_url: Optional[str] = None,
) -> Tuple[bool, str, bool]:
    freeze = _snapshot_freeze(project_dir)

    ok, apply_msg = _apply_pip_upgrade(project_dir, packages, extra_index_url)
    if not ok:
        return False, apply_msg, False

    check_ok, check_msg = _pip_check(project_dir)
    if check_ok:
        return True, apply_msg, False

    if freeze is None:
        return False, f"pip check failed ({check_msg}); no snapshot available for rollback", False

    restore_ok, restore_msg = _restore_freeze(project_dir, freeze)
    if restore_ok:
        return False, f"pip check failed ({check_msg}); rolled back successfully", True
    return False, f"pip check failed ({check_msg}); rollback also failed: {restore_msg}", False


def apply_upgrade_plan(
    plan: EcosystemUpgradePlan,
    base_dir: Optional[Path] = None,
    extra_index_url: Optional[str] = None,
) -> Generator[UpgradeEvent, None, None]:
    """Apply the plan bottom-up and yield telemetry events."""
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent.parent

    cuda_index_url, _ = _detect_cuda()

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

        non_torch = [p for p in proj_plan.safe_packages if not _is_torch_family(p.name)]
        torch_safe = [p for p in proj_plan.safe_packages if _is_torch_family(p.name)]

        if non_torch:
            names = [p.name for p in non_torch]
            ok, msg, rolled_back = _apply_batch_with_rollback(
                p_dir, names, extra_index_url=extra_index_url,
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

        # ── Torch-family CUDA refresh ──
        # Only upgrade the subset actually installed in this project. Never
        # install a torch-family package the project never declared, and
        # never emit a spurious "upgraded" event when nothing changed.
        project_map = _venv_package_map(p_dir)
        installed_torch = [
            name for name in TORCH_FAMILY if _normalize(name) in project_map
        ]

        if cuda_index_url is not None and installed_torch:
            freeze = _snapshot_freeze(p_dir)
            ok, msg = _apply_torch_cuda_upgrade(
                p_dir, cuda_index_url, installed_names=installed_torch,
            )
            if ok:
                check_ok, check_msg = _pip_check(p_dir)
                if not check_ok and freeze is not None:
                    restore_ok, _restore_msg = _restore_freeze(p_dir, freeze)
                    yield UpgradeEvent(
                        project=proj_plan.name, package="torch-family",
                        old_version="", new_version="", status="failed",
                        message=f"torch CUDA upgrade broke pip check: {check_msg}; "
                                f"rollback {'ok' if restore_ok else 'FAILED'}",
                    )
                else:
                    emitted_any = False
                    for pkg in torch_safe:
                        emitted_any = True
                        yield UpgradeEvent(
                            project=proj_plan.name, package=pkg.name,
                            old_version=pkg.current_version,
                            new_version=pkg.latest_version,
                            status="upgraded",
                            message=(
                                f"CUDA upgrade {pkg.current_version} -> "
                                f"{pkg.latest_version}."
                            ),
                        )
                    if not emitted_any:
                        versions = ", ".join(
                            f"{name}={project_map.get(_normalize(name), '?')}"
                            for name in installed_torch
                        )
                        yield UpgradeEvent(
                            project=proj_plan.name, package="torch-family",
                            old_version="", new_version="",
                            status="verified",
                            message=(
                                f"torch-family already at newest CUDA builds "
                                f"({versions})."
                            ),
                        )
            else:
                yield UpgradeEvent(
                    project=proj_plan.name, package="torch-family",
                    old_version="", new_version="", status="failed",
                    message=f"CUDA upgrade failed: {msg[:300]}",
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
    base_dir: Optional[Path] = None,
) -> Dict[str, Tuple[bool, str]]:
    """Read installed versions from each venv and write back to manifests.

    Packages in :data:`PROTECTED_FROM_SYNC` are passed through untouched,
    regardless of the venv's current state. This prevents a transient
    install-time drift from permanently corrupting a manifest.
    """
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent.parent

    env_mgr_dir = base_dir / "lemgendary-env-manager"
    manifests_dir = env_mgr_dir / "requirements"
    manifests_dir.mkdir(parents=True, exist_ok=True)

    project_to_manifest = {
        "lemgendary-training-suite": "requirements-training.txt",
        "lemgendary-datasets": "requirements-datasets.txt",
        "lemgendary-env-manager": "requirements-env-manager.txt",
    }

    results: Dict[str, Tuple[bool, str]] = {}

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
        existing_lines: List[str] = []
        if manifest_path.exists():
            existing_lines = manifest_path.read_text(encoding="utf-8").splitlines()

        new_lines: List[str] = []
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
        except Exception as exc:
            results[proj_name] = (False, str(exc))

    return results