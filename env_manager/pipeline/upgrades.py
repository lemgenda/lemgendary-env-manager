"""Pipeline step 5: safe npm and pip upgrades, applied bottom-up."""

from collections.abc import Generator
from pathlib import Path
from typing import Any

from env_manager._logging import get_logger
from env_manager.dependency_resolver import (
    classify_safe_upgrades,
    find_outdated_packages,
    venv_package_count,
)
from env_manager.pipeline.events import Emit, PipelineEvent
from env_manager.system_probe import HardwareProfile
from env_manager.updater import (
    _apply_batch_with_rollback,
    _apply_npm_update,
    _find_npm_outdated,
    pinned_specs,
    sync_manifests_from_venvs,
    torch_upgrade_index,
)
from env_manager.venv_manager import ProjectVenvInfo

_log = get_logger(__name__)


UPGRADE_SUMMARY_SIZE = 5
"""Upgraded packages named in the step summary before "+N more"."""


def execute_safe_upgrades(
    base_dir: Path,
    projects: list[ProjectVenvInfo],
    hw: HardwareProfile,
    emit: Emit,
) -> Generator[PipelineEvent, None, None]:
    """Apply safe dependency upgrades bottom-up across managed projects.

    Each project is classified from its discovery metadata: every
    project that is not a pure Node project gets the Python safe-upgrade
    path, and every project with a ``package.json`` gets the npm path.
    A dual-stack project (for example a Python repo with npm lint
    tooling) therefore receives both.
    """
    for p in projects:
        if p.has_npm_manifest:
            yield from _upgrade_npm_dependencies(p, emit)
        if not p.is_node_project:
            yield from _upgrade_python_dependencies(p, hw, emit)

    try:
        sync_manifests_from_venvs(base_dir)
    except OSError as sync_err:
        _log.warning("Manifest sync after upgrades failed: %s", sync_err)
        yield emit(
            "info",
            f"Manifest sync notice: {sync_err}",
        )


def _upgrade_npm_dependencies(
    p: Any,
    emit: Emit,
) -> Generator[PipelineEvent, None, None]:
    """Run ``npm update`` for a project with outdated npm packages."""
    p_dir = Path(p.project_dir)
    outdated = _find_npm_outdated(p_dir)
    if not outdated:
        yield emit(
            "success",
            f"{p.name} npm dependencies are up to date.",
        )
        return

    yield emit(
        "info",
        f"UPGRADING: {p.name} (npm) - updating {len(outdated)} package(s)...",
    )
    npm_ok, npm_msg = _apply_npm_update(p_dir)
    if npm_ok:
        yield emit(
            "success",
            f"{p.name}: Successfully updated npm packages.",
        )
    else:
        yield emit(
            "warning",
            f"{p.name}: npm update encountered issue: {npm_msg[:200]}",
        )


def _upgrade_python_dependencies(
    p: Any,
    hw: Any,
    emit: Emit,
) -> Generator[PipelineEvent, None, None]:
    """Apply classified safe pip upgrades to one project's venv."""
    p_dir = Path(p.project_dir)

    pkg_count = venv_package_count(p_dir)
    if pkg_count == 0:
        yield emit(
            "warning",
            f"{p.name} environment has no installed packages. Skipping upgrades.",
        )
        return

    raw = find_outdated_packages(p_dir)
    if not raw:
        yield emit(
            "success",
            f"{p.name} dependencies are up to date ({pkg_count} packages).",
        )
        return

    safe, blocked = classify_safe_upgrades(
        p_dir,
        raw,
        cuda_index_url=torch_upgrade_index(hw),
    )

    for pkg, reason in blocked:
        yield emit(
            "warning",
            f"  Blocked: {pkg.name} {pkg.current_version} -> {pkg.latest_version} ({reason})",
        )

    if not safe:
        yield emit(
            "info",
            f"{p.name}: No unconstrained safe upgrades available"
            f" (all {len(blocked)} candidate(s) blocked).",
        )
        return

    yield emit(
        "info",
        f"UPGRADING: {p.name} - applying {len(safe)} safe upgrade(s)...",
    )

    upgraded_ok, upgrade_msg, rolled_back = _apply_batch_with_rollback(
        p_dir,
        pinned_specs(safe),
        extra_index_url=torch_upgrade_index(hw),
    )

    if upgraded_ok:
        upgraded_list = ", ".join(
            f"{pkg.name} ({pkg.latest_version})" for pkg in safe[:UPGRADE_SUMMARY_SIZE]
        )
        if len(safe) > UPGRADE_SUMMARY_SIZE:
            upgraded_list += f", +{len(safe) - UPGRADE_SUMMARY_SIZE} more"
        yield emit(
            "success",
            f"{p.name}: Upgraded {len(safe)} package(s) safely [{upgraded_list}].",
        )
    else:
        rollback_note = " (safely rolled back to previous snapshot)" if rolled_back else ""
        yield emit(
            "warning",
            f"{p.name}: Upgrade batch failed{rollback_note}: {upgrade_msg[:200]}",
        )
