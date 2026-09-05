"""Health audit and cross-project consistency module.

Audits virtual environments, identifies missing packages, detects version drift,
and validates accelerator alignment across the LemGendary ecosystem.
"""

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from env_manager.bootstrap import BootstrapStatus, verify_prerequisites
from env_manager.requirements_manager import parse_requirements_file
from env_manager.system_probe import HardwareProfile, probe_hardware
from env_manager.venv_manager import (
    ProjectVenvInfo,
    discover_projects,
    get_installed_packages,
)


@dataclass
class ProjectHealth:
    """Detailed health status for a single project."""
    name: str
    project_dir: str
    venv_exists: bool
    python_version: Optional[str]
    total_required: int
    total_installed: int
    missing_packages: List[str]
    installed_packages: Dict[str, str]
    is_healthy: bool

    def to_dict(self) -> Dict[str, Any]:
        """Convert project health to dictionary."""
        return asdict(self)


@dataclass
class VersionDriftEntry:
    """Package version comparison across projects."""
    package_name: str
    versions: Dict[str, Optional[str]]
    has_drift: bool

    def to_dict(self) -> Dict[str, Any]:
        """Convert entry to dictionary."""
        return asdict(self)


@dataclass
class HealthAuditReport:
    """Comprehensive ecosystem health audit report."""
    bootstrap: BootstrapStatus
    hardware: HardwareProfile
    projects: List[ProjectHealth]
    version_drift: List[VersionDriftEntry]
    overall_healthy: bool

    def to_dict(self) -> Dict[str, Any]:
        """Convert report to dictionary."""
        return asdict(self)


def audit_project_health(project_info: ProjectVenvInfo) -> ProjectHealth:
    """Audit single project virtual environment and requirements."""
    p_dir = Path(project_info.project_dir)
    req_file = p_dir / "requirements.txt"
    installed = get_installed_packages(p_dir) if project_info.is_valid else {}

    missing: List[str] = []
    total_required = 0

    if req_file.exists():
        parsed = parse_requirements_file(req_file)
        for entry in parsed:
            if entry.is_index_url:
                continue
            total_required += 1
            pkg_name = entry.name.lower()
            if pkg_name not in installed:
                missing.append(pkg_name)

    is_healthy = project_info.is_valid and len(missing) == 0

    return ProjectHealth(
        name=project_info.name,
        project_dir=project_info.project_dir,
        venv_exists=project_info.is_valid,
        python_version=project_info.python_version,
        total_required=total_required,
        total_installed=len(installed),
        missing_packages=missing,
        installed_packages=installed,
        is_healthy=is_healthy,
    )


def compute_version_drift(projects: List[ProjectHealth]) -> List[VersionDriftEntry]:
    """Identify version discrepancies for shared packages across projects."""
    shared_candidate_keys = [
        "torch",
        "torchvision",
        "transformers",
        "pillow",
        "numpy",
        "scipy",
        "tqdm",
        "pyyaml",
        "pandas",
        "pyarrow",
        "ultralytics",
    ]

    drift_entries: List[VersionDriftEntry] = []
    for pkg in shared_candidate_keys:
        versions: Dict[str, Optional[str]] = {}
        observed_versions = set()
        for p in projects:
            ver = p.installed_packages.get(pkg)
            versions[p.name] = ver
            if ver:
                observed_versions.add(ver)

        has_drift = len(observed_versions) > 1
        drift_entries.append(
            VersionDriftEntry(
                package_name=pkg,
                versions=versions,
                has_drift=has_drift,
            )
        )
    return drift_entries


def run_full_health_audit(base_dir: Optional[Path] = None) -> HealthAuditReport:
    """Execute complete health audit across all projects and host system."""
    bootstrap = verify_prerequisites()
    hardware = probe_hardware()
    discovered = discover_projects(base_dir)

    projects_health: List[ProjectHealth] = []
    for d in discovered:
        health = audit_project_health(d)
        projects_health.append(health)

    drift = compute_version_drift(projects_health)

    all_proj_healthy = all(p.is_healthy for p in projects_health) if projects_health else False
    overall_healthy = bootstrap.python_valid and bootstrap.git_installed and all_proj_healthy

    return HealthAuditReport(
        bootstrap=bootstrap,
        hardware=hardware,
        projects=projects_health,
        version_drift=drift,
        overall_healthy=overall_healthy,
    )
