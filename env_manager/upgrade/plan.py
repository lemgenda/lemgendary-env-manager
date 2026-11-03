"""Building the safety-checked, CUDA-aware ecosystem upgrade plan."""

from pathlib import Path

from env_manager._logging import get_logger
from env_manager.dependency_resolver import (
    OutdatedPackage,
    classify_safe_upgrades,
    find_outdated_packages,
)
from env_manager.upgrade.models import (
    EcosystemUpgradePlan,
    ProjectUpgradePlan,
    is_torch_family,
    normalize,
    upgrade_order,
    venv_package_map,
)
from env_manager.upgrade.npm import find_npm_outdated
from env_manager.upgrade.torch_cuda import check_torch_cuda_upgrades, detect_cuda, torch_installed
from env_manager.venv_manager import ProjectVenvInfo, discover_projects
from env_manager.workspace import workspace_root

_log = get_logger(__name__)


def _plan_node_project(p: ProjectVenvInfo) -> ProjectUpgradePlan:
    """Plan a Node workspace: every outdated npm package is a safe candidate."""
    p_dir = Path(p.project_dir)
    outdated = find_npm_outdated(p_dir)
    return ProjectUpgradePlan(
        name=p.name, project_dir=str(p_dir), is_node_project=True,
        outdated_packages=outdated, safe_packages=list(outdated),
        blocked_packages=[], has_updates=len(outdated) > 0,
        has_safe_updates=len(outdated) > 0,
    )


def _dedupe(packages: list[OutdatedPackage]) -> list[OutdatedPackage]:
    """Return ``packages`` with later duplicates of a normalized name removed."""
    seen: set[str] = set()
    deduped: list[OutdatedPackage] = []
    for pkg in packages:
        key = normalize(pkg.name)
        if key not in seen:
            seen.add(key)
            deduped.append(pkg)
    return deduped


def _plan_python_project(p: ProjectVenvInfo, cuda_index_url: str | None) -> ProjectUpgradePlan:
    """Vet a Python project's outdated packages; torch-family goes via the CUDA index."""
    p_dir = Path(p.project_dir)
    raw = find_outdated_packages(p_dir)
    torch_raw = [pkg for pkg in raw if is_torch_family(pkg.name)]
    non_torch_raw = [pkg for pkg in raw if not is_torch_family(pkg.name)]

    safe_pkgs, blocked = classify_safe_upgrades(
        p_dir, non_torch_raw, cuda_index_url=cuda_index_url,
    )

    torch_cuda_candidates: list[OutdatedPackage] = []
    if cuda_index_url is not None and torch_installed(p_dir):
        torch_cuda_candidates, _reason = check_torch_cuda_upgrades(
            p_dir, cuda_index_url, venv_package_map(p_dir),
        )
        safe_pkgs.extend(torch_cuda_candidates)
    elif torch_raw:
        torch_safe, torch_blocked = classify_safe_upgrades(p_dir, torch_raw)
        safe_pkgs.extend(torch_safe)
        blocked.extend(torch_blocked)

    deduped = _dedupe(list(safe_pkgs) + [pkg for pkg, _ in blocked])
    return ProjectUpgradePlan(
        name=p.name, project_dir=str(p_dir), is_node_project=False,
        outdated_packages=deduped, safe_packages=safe_pkgs,
        blocked_packages=blocked, torch_cuda_packages=torch_cuda_candidates,
        has_updates=len(deduped) > 0, has_safe_updates=len(safe_pkgs) > 0,
        cuda_available=cuda_index_url is not None, cuda_index_url=cuda_index_url,
    )


def _projects_in_order(base_dir: Path, project: str | None) -> list[ProjectVenvInfo]:
    """Return the managed projects (or just ``project``) in :func:`upgrade_order`."""
    projects = discover_projects(base_dir)
    if project is not None:
        projects = [p for p in projects if p.name == project]
        if not projects:
            raise ValueError(f"Project not found: {project}")
    order_map = {name: i for i, name in enumerate(upgrade_order())}
    return sorted(projects, key=lambda p: order_map.get(p.name, 999))


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

    cuda_index_url, cuda_desc = detect_cuda()
    _log.info("CUDA detection: %s (index=%s)", cuda_desc, cuda_index_url)

    plan_items = [
        _plan_node_project(p) if p.is_node_project else _plan_python_project(p, cuda_index_url)
        for p in _projects_in_order(base_dir, project)
    ]
    total_outdated = sum(len(item.outdated_packages) for item in plan_items)
    total_safe = sum(len(item.safe_packages) for item in plan_items)
    total_blocked = sum(len(item.blocked_packages) for item in plan_items)

    return EcosystemUpgradePlan(
        projects=plan_items, total_outdated=total_outdated,
        total_safe=total_safe, total_blocked=total_blocked,
        has_any_updates=total_outdated > 0, has_any_safe_updates=total_safe > 0,
        cuda_detected=cuda_index_url is not None, cuda_index_url=cuda_index_url,
    )
