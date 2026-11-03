"""Per-project health: venv state, installed packages and classified upgrades."""

from pathlib import Path

from env_manager._logging import get_logger
from env_manager.dependency_resolver import (
    OutdatedPackage,
    classify_safe_upgrades,
    find_outdated_packages,
)
from env_manager.health.manifests import marker_matches
from env_manager.health.models import (
    OPENCV_VARIANTS_NORMALIZED,
    TORCH_FAMILY_NORMALIZED,
    ProjectHealth,
    UpgradeStatus,
    normalize_package_name,
)
from env_manager.health.npm import read_json_object, read_node_modules_versions
from env_manager.requirements_manager import parse_requirements_file
from env_manager.updater import _check_torch_cuda_upgrades
from env_manager.venv_manager import (
    ProjectVenvInfo,
    get_installed_packages,
)

_log = get_logger(__name__)


def _classify_torch_via_cuda_index(
    project_dir: Path,
    current_state: dict[str, str],
    cuda_index_url: str,
) -> dict[str, UpgradeStatus]:
    """Resolve torch-family upgrade candidates against the CUDA index."""
    candidates, reason = _check_torch_cuda_upgrades(
        project_dir, cuda_index_url, current_state,
    )
    if not candidates:
        _log.debug("No CUDA torch candidates for %s: %s", project_dir, reason)

    result: dict[str, UpgradeStatus] = {}
    for pkg in candidates:
        result[normalize_package_name(pkg.name)] = UpgradeStatus(
            current=pkg.current_version,
            latest=pkg.latest_version,
            safe=True,
            reason=None,
        )
    return result


def _classify_project_upgrades(
    project: ProjectHealth,
    cuda_index_url: str | None = None,
) -> dict[str, UpgradeStatus]:
    """Return {normalized_name: UpgradeStatus} for outdated packages."""
    p_dir = Path(project.project_dir)
    if not project.venv_exists:
        return {}

    raw: list[OutdatedPackage] = find_outdated_packages(p_dir)
    if not raw:
        return {}

    torch_raw = [
        p for p in raw
        if normalize_package_name(p.name) in TORCH_FAMILY_NORMALIZED
    ]
    non_torch_raw = [
        p for p in raw
        if normalize_package_name(p.name) not in TORCH_FAMILY_NORMALIZED
    ]

    result: dict[str, UpgradeStatus] = {}

    if non_torch_raw:
        safe, blocked = classify_safe_upgrades(
            p_dir, non_torch_raw, cuda_index_url=cuda_index_url,
        )
        for pkg in safe:
            result[normalize_package_name(pkg.name)] = UpgradeStatus(
                current=pkg.current_version,
                latest=pkg.latest_version,
                safe=True, reason=None,
            )
        for pkg, reason in blocked:
            result[normalize_package_name(pkg.name)] = UpgradeStatus(
                current=pkg.current_version,
                latest=pkg.latest_version,
                safe=False, reason=reason,
            )

    if torch_raw:
        if cuda_index_url:
            current_state = {
                normalize_package_name(k): v
                for k, v in get_installed_packages(p_dir).items()
            }
            cuda_result = _classify_torch_via_cuda_index(
                p_dir, current_state, cuda_index_url,
            )
            result.update(cuda_result)
        else:
            safe, blocked = classify_safe_upgrades(p_dir, torch_raw)
            for pkg in safe:
                result[normalize_package_name(pkg.name)] = UpgradeStatus(
                    current=pkg.current_version,
                    latest=pkg.latest_version,
                    safe=True, reason=None,
                )
            for pkg, reason in blocked:
                result[normalize_package_name(pkg.name)] = UpgradeStatus(
                    current=pkg.current_version,
                    latest=pkg.latest_version,
                    safe=False, reason=reason,
                )

    return result


def audit_project_health(project_info: ProjectVenvInfo) -> ProjectHealth:
    """Audit single project virtual environment and requirements."""
    p_dir = Path(project_info.project_dir)
    req_file = p_dir / "requirements.txt"
    installed = get_installed_packages(p_dir) if project_info.is_valid else {}
    canonical_installed = {
        normalize_package_name(k): v for k, v in installed.items()
    }

    missing: list[str] = []
    total_required = 0

    if project_info.is_node_project:
        pkg_json = p_dir / "package.json"
        total_req = 0
        if pkg_json.is_file():
            data = read_json_object(pkg_json)
            total_req = len(data.get("dependencies") or {}) + len(data.get("devDependencies") or {})
        node_mods = read_node_modules_versions(p_dir) if project_info.node_modules_present else {}
        return ProjectHealth(
            name=project_info.name,
            project_dir=project_info.project_dir,
            venv_exists=project_info.node_modules_present,
            python_version=None,
            total_required=total_req,
            total_installed=len(node_mods),
            missing_packages=[] if project_info.node_modules_present else ["node_modules"],
            installed_packages=node_mods,
            is_healthy=project_info.node_modules_present,
        )

    if req_file.exists():
        parsed = parse_requirements_file(req_file)
        has_installed_opencv = any(
            v in canonical_installed for v in OPENCV_VARIANTS_NORMALIZED
        )
        for entry in parsed:
            if entry.is_index_url:
                continue
            if entry.marker and not marker_matches(entry.marker):
                continue
            total_required += 1
            pkg_name = normalize_package_name(entry.name)
            if pkg_name in OPENCV_VARIANTS_NORMALIZED and has_installed_opencv:
                continue
            if pkg_name not in canonical_installed:
                missing.append(entry.name)

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
