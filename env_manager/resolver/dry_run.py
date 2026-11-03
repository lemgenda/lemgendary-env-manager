"""Constraint-preserving ``pip install --dry-run --report`` of upgrade targets."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from env_manager._logging import get_logger
from env_manager.resolver.models import normalize
from env_manager.resolver.pip_json import PipJsonError, installed_package_versions, run_pip
from env_manager.venv_manager import is_venv_valid

_log = get_logger(__name__)

DRY_RUN_TIMEOUT = 300
"""Seconds allowed for one constraint-preserving pip dry-run."""


def _parse_pip_report(stdout: str) -> dict[str, Any] | None:
    """Parse ``pip install --report -`` stdout. Returns None (logged) on failure.

    stdout no longer contains pip's stderr warnings, so it must be exactly one
    JSON document. Callers treat None as a failed dry-run (fail closed).
    """
    text = stdout.strip()
    if not text:
        _log.error("pip --report produced no JSON on stdout")
        return None
    try:
        report = json.loads(text)
    except json.JSONDecodeError as exc:
        _log.error("pip --report stdout is not valid JSON: %s", exc)
        return None
    if not isinstance(report, dict):
        _log.error("pip --report JSON is not an object: %s", type(report).__name__)
        return None
    return report


def _would_install_from_report(report: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in report.get("install", []):
        meta = item.get("metadata") or {}
        name = meta.get("name")
        version = meta.get("version")
        if name and version:
            result[normalize(name)] = version
    return result


def _write_constraints_file(pins: dict[str, str]) -> str:
    fd, path = tempfile.mkstemp(prefix="pip_constraints_", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for name, version in pins.items():
                f.write(f"{name}=={version}\n")
    except (OSError, ValueError):
        try:
            os.unlink(path)
        except OSError as exc:
            _log.debug("Failed unlinking temp constraints file %s: %s", path, exc)
        raise
    return path


def _run_dry_run_report(
    project_dir: Path, args: list[str],
) -> tuple[dict[str, str] | None, str]:
    """Run a ``pip install --dry-run --report -`` and return ``(would_install, error)``.

    ``would_install`` is None on any failure, including an unparseable report.
    """
    proc = run_pip(project_dir, args, DRY_RUN_TIMEOUT, "install --dry-run")
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-8:]
        return None, " | ".join(tail) or "pip dry-run failed"
    report = _parse_pip_report(proc.stdout)
    if report is None:
        return None, "pip dry-run report could not be parsed"
    return _would_install_from_report(report), ""


def _require_installed_state(
    project_dir: Path, current_state: dict[str, str] | None,
) -> tuple[dict[str, str], str]:
    """Return ``(installed_state, error)``; ``error`` is non-empty when unknown.

    Without the installed set the constraints file would be empty and the
    "constraint-preserving" dry-run would run unconstrained, so callers must
    abort (fail closed) on an error.
    """
    if current_state is None:
        try:
            current_state = installed_package_versions(project_dir)
        except PipJsonError as exc:
            _log.error("Dry-run aborted, installed state unknown: %s", exc)
            return {}, f"installed package state unavailable: {exc}"
    if not current_state:
        _log.error("Dry-run aborted for %s: installed package state is empty", project_dir)
        return {}, "installed package state unavailable"
    return current_state, ""


def constraint_preserving_dry_run(
    project_dir: Path,
    targets: list[str],
    *,
    current_state: dict[str, str] | None = None,
    index_url: str | None = None,
    extra_index_url: str | None = None,
) -> tuple[bool, dict[str, str], str]:
    """Dry-run upgrading ``targets`` while pinning everything else."""
    if not is_venv_valid(project_dir) or not targets:
        return True, {}, "nothing to do"

    current_state, state_error = _require_installed_state(project_dir, current_state)
    if state_error:
        return False, {}, state_error

    target_norm = {normalize(t) for t in targets}
    constraint_pins = {
        name: version
        for name, version in current_state.items()
        if name not in target_norm
    }

    try:
        constraint_path = _write_constraints_file(constraint_pins)
    except OSError as exc:
        _log.error("Dry-run aborted for %s: cannot write constraints: %s", project_dir, exc)
        return False, {}, f"failed to write constraints: {exc}"

    args = [
        "install",
        "--dry-run",
        "--upgrade",
        "--upgrade-strategy", "only-if-needed",
        "--constraint", constraint_path,
        "--report", "-",
        "--quiet",
    ]
    if index_url:
        args.extend(["--index-url", index_url])
    if extra_index_url:
        args.extend(["--extra-index-url", extra_index_url])
    args.extend(targets)

    try:
        would_install, run_error = _run_dry_run_report(project_dir, args)
        if would_install is None:
            return False, {}, run_error

        changed = {
            name: new_ver
            for name, new_ver in would_install.items()
            if current_state.get(name) != new_ver
        }
        return True, changed, "ok"
    finally:
        try:
            os.unlink(constraint_path)
        except OSError as exc:
            _log.debug("Failed unlinking constraint file %s: %s", constraint_path, exc)
