"""Applying an upgrade plan: pinned installs, pip check, freeze snapshots and rollback."""

import os
import tempfile
from collections.abc import Generator
from pathlib import Path

from env_manager._logging import get_logger
from env_manager.dependency_resolver import (
    OutdatedPackage,
)
from env_manager.upgrade.models import (
    EcosystemUpgradePlan,
    ProjectUpgradePlan,
    UpgradeEvent,
    is_torch_family,
    pinned_specs,
)
from env_manager.upgrade.npm import apply_npm_update
from env_manager.upgrade.torch_cuda import torch_installed, verify_torch_cuda_runtime
from env_manager.utils import run_pip_with_recovery
from env_manager.venv_manager import (
    get_venv_python_path,
    is_venv_valid,
)

_log = get_logger(__name__)


def _pip_check(project_dir: Path) -> tuple[bool, str]:
    if not is_venv_valid(project_dir):
        return True, "no venv"
    python_path = get_venv_python_path(project_dir)
    proc = run_pip_with_recovery(python_path, ["check"], timeout=60)
    if proc.returncode == 0:
        return True, "all dependency constraints satisfied"
    return False, (proc.stdout or proc.stderr or "").strip()[:400] or "pip check failed"


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


def _rollback(project_dir: Path, freeze: str | None, failure: str) -> tuple[bool, str, bool]:
    """Restore ``freeze`` after ``failure``; return the failed batch result."""
    if freeze is None:
        return False, f"{failure}; no snapshot available for rollback", False
    restore_ok, restore_msg = _restore_freeze(project_dir, freeze)
    if restore_ok:
        return False, f"{failure}; rolled back successfully", True
    return False, f"{failure}; rollback also failed: {restore_msg}", False


def apply_batch_with_rollback(
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
            yield from _upgraded_events(project, torch_safe, "CUDA upgrade")
            return
        failure = f"torch CUDA upgrade broke pip check ({check_msg})"
    else:
        failure = f"CUDA upgrade failed ({msg[:300]})"
    _, message, _ = _rollback(p_dir, freeze, failure)
    yield UpgradeEvent(
        project=project, package="torch-family",
        old_version="", new_version="", status="failed", message=message[:400],
    )


def _event(project: str, package: str, ok: bool, message: str, success: str) -> UpgradeEvent:
    """Return a version-less event whose status is ``success`` or ``failed``."""
    return UpgradeEvent(
        project=project, package=package, old_version="", new_version="",
        status=success if ok else "failed", message=message,
    )


def _upgraded_events(
    project: str, packages: list[OutdatedPackage], verb: str,
) -> Generator[UpgradeEvent, None, None]:
    """Yield one ``upgraded`` event per applied package."""
    for pkg in packages:
        yield UpgradeEvent(
            project=project, package=pkg.name,
            old_version=pkg.current_version, new_version=pkg.latest_version,
            status="upgraded",
            message=f"{verb} {pkg.current_version} -> {pkg.latest_version}.",
        )


def _apply_node_project(proj_plan: ProjectUpgradePlan) -> Generator[UpgradeEvent, None, None]:
    """Run ``npm update`` when the plan found outdated npm packages."""
    if not proj_plan.safe_packages:
        yield UpgradeEvent(
            project=proj_plan.name, package="(all)", old_version="",
            new_version="", status="skipped",
            message="All npm packages are up to date.",
        )
        return
    ok, msg = apply_npm_update(Path(proj_plan.project_dir))
    yield _event(proj_plan.name, "(npm packages)", ok, (msg or "")[:300], "upgraded")


def _split_torch(
    proj_plan: ProjectUpgradePlan, cuda_index_url: str | None,
) -> tuple[list[OutdatedPackage], list[OutdatedPackage]]:
    """Return ``(regular, torch_family)`` candidates.

    On a GPU host torch-family candidates come from the CUDA index and are
    applied separately; otherwise they were vetted against PyPI like any
    other package and go in the regular batch.
    """
    if cuda_index_url is None:
        return list(proj_plan.safe_packages), []
    regular = [p for p in proj_plan.safe_packages if not is_torch_family(p.name)]
    torch_safe = [p for p in proj_plan.safe_packages if is_torch_family(p.name)]
    return regular, torch_safe


def apply_python_project(
    proj_plan: ProjectUpgradePlan,
    cuda_index_url: str | None,
    extra_index_url: str | None,
) -> Generator[UpgradeEvent, None, None]:
    """Apply one Python project's vetted candidates, then verify the venv.

    Shared by :func:`apply_upgrade_plan` and pipeline step 5.
    """
    p_dir = Path(proj_plan.project_dir)
    non_torch, torch_safe = _split_torch(proj_plan, cuda_index_url)

    if non_torch:
        ok, msg, rolled_back = apply_batch_with_rollback(
            p_dir, pinned_specs(non_torch), extra_index_url=extra_index_url,
        )
        if ok:
            yield from _upgraded_events(proj_plan.name, non_torch, "Upgraded")
        else:
            yield _event(proj_plan.name, "(batch)", False, msg[:400], "upgraded")
            if rolled_back:
                return
    elif not torch_safe and not proj_plan.has_updates:
        yield UpgradeEvent(
            project=proj_plan.name, package="(all)", old_version="",
            new_version="", status="skipped",
            message="All packages are up to date.",
        )

    # Runs only when the plan vetted torch-family candidates against the
    # CUDA index, and installs exactly those versions.
    if cuda_index_url is not None and torch_safe:
        yield from _apply_torch_candidates(proj_plan.name, p_dir, cuda_index_url, torch_safe)

    check_ok, check_msg = _pip_check(p_dir)
    yield _event(proj_plan.name, "(pip check)", check_ok, check_msg[:300], "verified")

    if cuda_index_url is not None and torch_installed(p_dir):
        torch_ok, torch_msg = verify_torch_cuda_runtime(p_dir)
        yield _event(proj_plan.name, "torch-cuda-verify", torch_ok, torch_msg, "verified")


def apply_upgrade_plan(
    plan: EcosystemUpgradePlan,
    extra_index_url: str | None = None,
) -> Generator[UpgradeEvent, None, None]:
    """Apply the plan bottom-up and yield telemetry events.

    Only the vetted candidates are installed, each pinned to the version the
    plan checked. The GPU torch index is the one stored in the plan;
    ``extra_index_url`` defaults to it. Manifests are not touched here:
    :func:`env_manager.upgrade.finalize.finalize_upgrade` writes them back
    only after the upgraded projects pass validation.
    """
    cuda_index_url = plan.cuda_index_url
    if extra_index_url is None:
        extra_index_url = cuda_index_url

    for proj_plan in plan.projects:
        for pkg, reason in proj_plan.blocked_packages:
            yield UpgradeEvent(
                project=proj_plan.name, package=pkg.name,
                old_version=pkg.current_version, new_version=pkg.latest_version,
                status="blocked", message=reason,
            )
        if proj_plan.is_node_project:
            yield from _apply_node_project(proj_plan)
        else:
            yield from apply_python_project(proj_plan, cuda_index_url, extra_index_url)
