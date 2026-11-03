"""Health audit and cross-project consistency module.

Audits virtual environments, identifies missing packages, detects version
drift and pin-type mismatches across projects, and produces full-coverage
inventories that reconcile every manifest against every installed venv.
"""

from pathlib import Path

from env_manager._logging import get_logger
from env_manager.bootstrap import verify_prerequisites
from env_manager.health.drift import (
    compute_manifest_coverage,
    compute_single_manifest_packages,
    compute_version_drift,
)
from env_manager.health.models import (
    OPENCV_VARIANTS_NORMALIZED,
    HealthAuditReport,
    ManifestCoverageEntry,
    PinInfo,
    ProjectHealth,
    UpgradeStatus,
    VersionDriftEntry,
    normalize_package_name,
)
from env_manager.health.npm import compute_npm_drift
from env_manager.health.project import audit_project_health
from env_manager.health.render import (
    render_drift_matrix,
    render_manifest_coverage_table,
    render_npm_drift_matrix,
    render_single_manifest_table,
)
from env_manager.npm_manager import audit_npm_workspaces
from env_manager.system_probe import probe_hardware
from env_manager.updater import torch_upgrade_index
from env_manager.venv_manager import (
    discover_projects,
)

_log = get_logger(__name__)


def run_full_health_audit(
    base_dir: Path | None = None,
    include_safety: bool = True,
) -> HealthAuditReport:
    """Execute complete health audit across all projects and host system."""
    bootstrap = verify_prerequisites()
    hardware = probe_hardware()
    discovered = discover_projects(base_dir)

    projects_health: list[ProjectHealth] = []
    for d in discovered:
        health = audit_project_health(d)
        projects_health.append(health)

    cuda_index_url = torch_upgrade_index(hardware)
    drift = compute_version_drift(
        projects_health,
        cuda_index_url=cuda_index_url,
        include_safety=include_safety,
    )
    coverage = compute_manifest_coverage(projects_health)
    singles = compute_single_manifest_packages(projects_health)

    npm_packages = audit_npm_workspaces(base_dir)
    npm_drift = compute_npm_drift(base_dir) if base_dir else []

    all_proj_healthy = (
        all(p.is_healthy for p in projects_health) if projects_health else False
    )
    npm_healthy = (
        all(s.node_modules_present for s in npm_packages) if npm_packages else True
    )
    overall_healthy = (
        bootstrap.python_valid
        and bootstrap.git_installed
        and all_proj_healthy
        and npm_healthy
    )

    return HealthAuditReport(
        bootstrap=bootstrap,
        hardware=hardware,
        projects=projects_health,
        version_drift=drift,
        manifest_coverage=coverage,
        single_manifest_packages=singles,
        npm_packages=npm_packages,
        npm_drift=npm_drift,
        overall_healthy=overall_healthy,
    )


__all__ = [
    "OPENCV_VARIANTS_NORMALIZED",
    "HealthAuditReport",
    "ManifestCoverageEntry",
    "PinInfo",
    "ProjectHealth",
    "UpgradeStatus",
    "VersionDriftEntry",
    "compute_version_drift",
    "normalize_package_name",
    "render_drift_matrix",
    "render_manifest_coverage_table",
    "render_npm_drift_matrix",
    "render_single_manifest_table",
]
