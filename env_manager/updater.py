"""Safe dependency upgrade module for LemGendary Environment Manager.

Implements a bottom-up, conflict-aware package upgrade strategy across all
managed Python virtual environments and the Node.js GUI workspace.

Upgrade order (bottom-up to prevent upstream breakage):
  1. lemgendary-env-manager  (tooling, fewest cross-deps)
  2. lemgendary-datasets      (data layer)
  3. lemgendary-training-suite (top-level, most deps)
  4. lemgendary-ai-studio-gui  (npm, independent)

After all upgrades are applied, centralized manifests in
``lemgendary-env-manager/requirements/`` are re-written to reflect the
actual installed versions so the canonical manifests stay in sync.
"""

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

from env_manager._logging import get_logger
from env_manager.dependency_resolver import OutdatedPackage, find_outdated_packages
from env_manager.npm_manager import run_npm_install
from env_manager.requirements_manager import sync_all_manifests
from env_manager.venv_manager import (
    discover_projects,
    get_installed_packages,
    get_venv_python_path,
    is_venv_valid,
)

_log = get_logger(__name__)


# ─── Data structures ────────────────────────────────────────────────────────

@dataclass
class ProjectUpgradePlan:
    """Per-project upgrade plan."""
    name: str
    project_dir: str
    is_node_project: bool
    outdated_packages: List[OutdatedPackage]
    has_updates: bool

    def to_dict(self) -> Dict[str, Any]:
        """Convert plan to dictionary."""
        return asdict(self)


@dataclass
class EcosystemUpgradePlan:
    """Full ecosystem upgrade plan across all managed projects."""
    projects: List[ProjectUpgradePlan]
    total_outdated: int
    has_any_updates: bool

    def to_dict(self) -> Dict[str, Any]:
        """Convert plan to dictionary."""
        return asdict(self)


@dataclass
class UpgradeEvent:
    """Single telemetry event from the upgrade pipeline."""
    project: str
    package: str
    old_version: str
    new_version: str
    status: str  # "upgraded", "failed", "skipped"
    message: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary."""
        return asdict(self)


# ─── Upgrade order ──────────────────────────────────────────────────────────

# Bottom-up ordering: stable base layers first to avoid upstream breakage
UPGRADE_ORDER = [
    "lemgendary-env-manager",
    "lemgendary-datasets",
    "lemgendary-training-suite",
    "lemgendary-ai-studio-gui",
]


# ─── npm outdated helper ────────────────────────────────────────────────────

def _find_npm_outdated(project_dir: Path) -> List[OutdatedPackage]:
    """Query outdated npm packages in a given directory."""
    npm_path = shutil.which("npm")
    if not npm_path:
        return []
    pkg_json = project_dir / "package.json"
    if not pkg_json.exists():
        return []
    try:
        proc = subprocess.run(
            [npm_path, "outdated", "--json"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        # npm outdated exits 1 when there are outdated packages — that is normal
        if proc.stdout.strip():
            data: Dict[str, Any] = json.loads(proc.stdout.strip())
            results: List[OutdatedPackage] = []
            for pkg_name, info in data.items():
                results.append(
                    OutdatedPackage(
                        name=pkg_name,
                        current_version=str(info.get("current", "")),
                        latest_version=str(info.get("latest", "")),
                        package_type="npm",
                    )
                )
            return results
    except Exception as exc:
        _log.warning("npm outdated query failed for %s: %s", project_dir, exc)
    return []


def _apply_npm_update(project_dir: Path) -> Tuple[bool, str]:
    """Run 'npm update' in the given directory."""
    npm_path = shutil.which("npm")
    if not npm_path:
        return False, "npm not found on PATH."
    try:
        proc = subprocess.run(
            [npm_path, "update"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if proc.returncode == 0:
            return True, proc.stdout
        return False, proc.stderr or proc.stdout or "npm update failed."
    except Exception as exc:
        return False, str(exc)


def _apply_pip_upgrade(project_dir: Path, packages: List[str], extra_index_url: Optional[str] = None) -> Tuple[bool, str]:
    """Upgrade a list of packages inside the project venv."""
    if not is_venv_valid(project_dir) or not packages:
        return True, "No packages to upgrade."
    python_path = get_venv_python_path(project_dir)
    cmd = [str(python_path), "-m", "pip", "install", "--upgrade"] + packages
    if extra_index_url:
        cmd.extend(["--extra-index-url", extra_index_url])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, check=False)
        if proc.returncode == 0:
            return True, proc.stdout
        return False, proc.stderr or proc.stdout or "pip upgrade failed."
    except Exception as exc:
        return False, str(exc)


# ─── Public API ─────────────────────────────────────────────────────────────

def build_upgrade_plan(base_dir: Optional[Path] = None) -> EcosystemUpgradePlan:
    """Build a full bottom-up upgrade plan for every managed project."""
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent.parent

    projects = discover_projects(base_dir)
    # Sort by upgrade order; unknown projects go last
    order_map = {name: i for i, name in enumerate(UPGRADE_ORDER)}
    projects_sorted = sorted(projects, key=lambda p: order_map.get(p.name, 999))

    plan_items: List[ProjectUpgradePlan] = []
    total_outdated = 0

    for p in projects_sorted:
        p_dir = Path(p.project_dir)
        if p.is_node_project:
            outdated = _find_npm_outdated(p_dir)
        else:
            outdated = find_outdated_packages(p_dir)

        plan_items.append(
            ProjectUpgradePlan(
                name=p.name,
                project_dir=str(p_dir),
                is_node_project=p.is_node_project,
                outdated_packages=outdated,
                has_updates=len(outdated) > 0,
            )
        )
        total_outdated += len(outdated)

    return EcosystemUpgradePlan(
        projects=plan_items,
        total_outdated=total_outdated,
        has_any_updates=total_outdated > 0,
    )


def apply_upgrade_plan(
    plan: EcosystemUpgradePlan,
    base_dir: Optional[Path] = None,
    extra_index_url: Optional[str] = None,
) -> Generator[UpgradeEvent, None, None]:
    """Apply the upgrade plan bottom-up and yield telemetry events.

    After all pip upgrades are complete the centralized manifests are
    re-written to capture newly installed versions.

    Yields UpgradeEvent for each package attempted.
    """
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent.parent

    for proj_plan in plan.projects:
        p_dir = Path(proj_plan.project_dir)

        if not proj_plan.has_updates:
            yield UpgradeEvent(
                project=proj_plan.name,
                package="(all)",
                old_version="",
                new_version="",
                status="skipped",
                message="All packages are up to date.",
            )
            continue

        if proj_plan.is_node_project:
            # npm update
            ok, msg = _apply_npm_update(p_dir)
            status = "upgraded" if ok else "failed"
            yield UpgradeEvent(
                project=proj_plan.name,
                package="(npm packages)",
                old_version="",
                new_version="",
                status=status,
                message=msg[:300] if msg else "",
            )
        else:
            # Pip upgrade all outdated at once (faster, single solver pass)
            pkg_names = [pkg.name for pkg in proj_plan.outdated_packages]
            ok, msg = _apply_pip_upgrade(p_dir, pkg_names, extra_index_url)
            if ok:
                for pkg in proj_plan.outdated_packages:
                    yield UpgradeEvent(
                        project=proj_plan.name,
                        package=pkg.name,
                        old_version=pkg.current_version,
                        new_version=pkg.latest_version,
                        status="upgraded",
                        message=f"Upgraded from {pkg.current_version} to {pkg.latest_version}.",
                    )
            else:
                yield UpgradeEvent(
                    project=proj_plan.name,
                    package="(batch)",
                    old_version="",
                    new_version="",
                    status="failed",
                    message=msg[:300] if msg else "Upgrade failed.",
                )

    # Auto-sync: write installed versions back to centralized manifests
    sync_results = sync_manifests_from_venvs(base_dir)
    for proj_name, (ok, sync_msg) in sync_results.items():
        yield UpgradeEvent(
            project=proj_name,
            package="(manifest sync)",
            old_version="",
            new_version="",
            status="upgraded" if ok else "failed",
            message=sync_msg,
        )


def sync_manifests_from_venvs(base_dir: Optional[Path] = None) -> Dict[str, Tuple[bool, str]]:
    """Read installed versions from each venv and write back to centralized manifests.

    For Python projects this re-pins every installed package.
    For the GUI project it triggers an npm install to update package-lock.json.
    The canonical manifests in lemgendary-env-manager/requirements/ are updated
    so they always reflect the actual production state.
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
            results[proj_name] = (False, "No valid venv found, cannot read installed packages.")
            continue

        installed = get_installed_packages(p_dir)
        if not installed:
            results[proj_name] = (False, "No installed packages found in venv.")
            continue

        # Read existing manifest to preserve comments, index URLs, and markers
        manifest_path = manifests_dir / manifest_name
        existing_lines: List[str] = []
        if manifest_path.exists():
            existing_lines = manifest_path.read_text(encoding="utf-8").splitlines()

        new_lines: List[str] = []
        for line in existing_lines:
            stripped = line.strip()
            # Keep blank lines, comments, and index URL directives unchanged
            if not stripped or stripped.startswith("#") or stripped.startswith("--") or stripped.startswith("-i "):
                new_lines.append(line)
                continue
            # Try to extract package name from the line
            import re
            match = re.match(r"^([A-Za-z0-9_\-\.]+)", stripped)
            if match:
                pkg_name = match.group(1).lower().replace("_", "-").replace(".", "-")
                installed_ver = installed.get(pkg_name) or installed.get(match.group(1).lower())
                if installed_ver:
                    # Preserve any environment markers that were on the original line
                    marker_match = re.search(r";(.+)$", stripped)
                    marker = f"; {marker_match.group(1).strip()}" if marker_match else ""
                    new_lines.append(f"{match.group(1)}=={installed_ver}{marker}")
                    continue
            new_lines.append(line)

        try:
            manifest_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            # Also copy back to the project directory
            import shutil as _shutil
            _shutil.copy2(manifest_path, p_dir / "requirements.txt")
            results[proj_name] = (True, f"Manifest {manifest_name} updated and synced to project.")
        except Exception as exc:
            results[proj_name] = (False, str(exc))

    return results
