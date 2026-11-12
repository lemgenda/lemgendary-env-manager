"""Pipeline step 5: safe npm and pip upgrades, applied bottom-up.

The Python path is the same engine as ``lem-env update``:
:func:`plan_python_project` vets the candidates (torch-family through the
CUDA index as ``wheel-cuda``) and :func:`apply_python_project` installs
them. Manifests are written back by step 6, after validation passes.
"""

from collections.abc import Generator
from pathlib import Path
from typing import Any

from env_manager._logging import get_logger
from env_manager.dependency_resolver import venv_package_count
from env_manager.pipeline.events import Emit, PipelineEvent
from env_manager.system_probe import HardwareProfile
from env_manager.updater import (
    UpgradeEvent,
    apply_npm_update,
    find_npm_outdated,
    torch_upgrade_index,
)
from env_manager.upgrade.apply import apply_python_project
from env_manager.upgrade.plan import plan_python_project
from env_manager.venv_manager import ProjectVenvInfo

_log = get_logger(__name__)


UPGRADE_SUMMARY_SIZE = 5
"""Upgraded packages named in the step summary before "+N more"."""

_EVENT_STATUS = {
    "upgraded": "success",
    "verified": "success",
    "skipped": "info",
    "blocked": "warning",
    "failed": "warning",
}
"""Pipeline event status for each ``UpgradeEvent.status``."""


def execute_safe_upgrades(
    projects: list[ProjectVenvInfo],
    hw: HardwareProfile,
    emit: Emit,
) -> Generator[PipelineEvent, None, bool]:
    """Apply safe dependency upgrades bottom-up across managed projects.

    Each project is classified from its discovery metadata: every
    project that is not a pure Node project gets the Python safe-upgrade
    path, and every project with a ``package.json`` gets the npm path.
    A dual-stack project (for example a Python repo with npm lint
    tooling) therefore receives both. Returns True when anything was
    upgraded, so step 6 knows to write the manifests back.
    """
    applied = False
    for p in projects:
        if p.has_npm_manifest:
            applied = (yield from _upgrade_npm_dependencies(p, emit)) or applied
        if not p.is_node_project:
            applied = (yield from _upgrade_python_dependencies(p, hw, emit)) or applied
    return applied


def _upgrade_npm_dependencies(
    p: Any,
    emit: Emit,
) -> Generator[PipelineEvent, None, bool]:
    """Run ``npm update`` for a project with outdated npm packages; True if it ran."""
    p_dir = Path(p.project_dir)
    outdated = find_npm_outdated(p_dir)
    if not outdated:
        yield emit(
            "success",
            f"{p.name} npm dependencies are up to date.",
        )
        return False

    yield emit(
        "info",
        f"UPGRADING: {p.name} (npm) - updating {len(outdated)} package(s)...",
    )
    npm_ok, npm_msg = apply_npm_update(p_dir)
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
    return npm_ok


def _summary(name: str, upgraded: list[UpgradeEvent]) -> str:
    """``"<project>: Upgraded N package(s) safely [a (1.0), ...]."``"""
    listed = ", ".join(
        f"{ev.package} ({ev.new_version})" for ev in upgraded[:UPGRADE_SUMMARY_SIZE]
    )
    if len(upgraded) > UPGRADE_SUMMARY_SIZE:
        listed += f", +{len(upgraded) - UPGRADE_SUMMARY_SIZE} more"
    return f"{name}: Upgraded {len(upgraded)} package(s) safely [{listed}]."


def _upgrade_python_dependencies(
    p: Any,
    hw: HardwareProfile,
    emit: Emit,
) -> Generator[PipelineEvent, None, bool]:
    """Plan and apply one project's vetted pip upgrades; True if any was installed."""
    p_dir = Path(p.project_dir)

    pkg_count = venv_package_count(p_dir)
    if pkg_count == 0:
        yield emit(
            "warning",
            f"{p.name} environment has no installed packages. Skipping upgrades.",
        )
        return False

    cuda_index_url = torch_upgrade_index(hw)
    plan = plan_python_project(p, cuda_index_url)
    if not plan.has_updates:
        yield emit(
            "success",
            f"{p.name} dependencies are up to date ({pkg_count} packages).",
        )
        return False

    for pkg, reason in plan.blocked_packages:
        yield emit(
            "warning",
            f"  Blocked: {pkg.name} {pkg.current_version} -> {pkg.latest_version} ({reason})",
        )

    if not plan.safe_packages:
        yield emit(
            "info",
            f"{p.name}: No unconstrained safe upgrades available"
            f" (all {len(plan.blocked_packages)} candidate(s) blocked).",
        )
        return False

    yield emit(
        "info",
        f"UPGRADING: {p.name} - applying {len(plan.safe_packages)} safe upgrade(s)...",
    )
    upgraded: list[UpgradeEvent] = []
    for ev in apply_python_project(plan, cuda_index_url, cuda_index_url):
        if ev.status == "upgraded":
            upgraded.append(ev)
        else:
            yield emit(_EVENT_STATUS.get(ev.status, "info"),
                       f"{p.name}: {ev.package}: {ev.message}")
    if upgraded:
        yield emit("success", _summary(p.name, upgraded))
    return bool(upgraded)
