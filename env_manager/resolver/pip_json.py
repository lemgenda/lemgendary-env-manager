"""Running pip in a project venv and reading its JSON output; fails closed on bad output."""

import json
import subprocess
from pathlib import Path
from typing import Any

from env_manager._logging import get_logger
from env_manager.resolver.models import OutdatedPackage, normalize
from env_manager.utils import run_pip_with_recovery
from env_manager.venv_manager import get_venv_python_path, is_venv_valid

_log = get_logger(__name__)


BOOTSTRAP_PACKAGES = {"pip", "wheel", "setuptools", "pkg_resources"}


class PipJsonError(RuntimeError):
    """pip failed or its stdout could not be read as the expected JSON.

    Raised instead of returning an empty result so that callers which make
    safety decisions (the upgrade classifier) fail closed rather than treat
    "nothing installed" as "nothing to protect" (review finding D-07).
    """


def log_pip_stderr(stderr: str, label: str, project_dir: Path) -> None:
    """Log every non-empty pip stderr line as a warning."""
    for line in (stderr or "").splitlines():
        if line.strip():
            _log.warning("pip %s (%s): %s", label, project_dir.name, line.strip())


def run_pip(
    project_dir: Path, args: list[str], timeout: int, label: str,
) -> subprocess.CompletedProcess[str]:
    """Run ``pip <args>`` with the project's venv interpreter and log its stderr.

    This is the resolver's only call into pip, so the dry-run and the JSON
    queries share one interpreter lookup and one stderr policy.
    """
    python_path = get_venv_python_path(project_dir)
    proc = run_pip_with_recovery(python_path, args, timeout=timeout)
    log_pip_stderr(proc.stderr, label, project_dir)
    return proc


def run_pip_json(project_dir: Path, args: list[str], timeout: int) -> Any:
    """Run ``pip <args>`` in the project venv and parse stdout as JSON.

    stderr is captured separately by ``run_pip_with_recovery`` and only
    logged; only stdout is parsed. Any failure raises ``PipJsonError``.
    """
    label = " ".join(args[:2])
    proc = run_pip(project_dir, args, timeout, label)
    if proc.returncode != 0:
        tail = " | ".join((proc.stderr or proc.stdout or "").strip().splitlines()[-5:])
        raise PipJsonError(
            f"pip {label} exited with code {proc.returncode} for {project_dir.name}: {tail}"
        )
    text = proc.stdout.strip()
    if not text:
        raise PipJsonError(f"pip {label} returned no output for {project_dir.name}")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise PipJsonError(
            f"pip {label} returned invalid JSON for {project_dir.name}: {exc}"
        ) from exc


def venv_package_count(project_dir: Path) -> int:
    """Return number of non-bootstrap packages installed in the venv.

    Returns 0 (callers then skip the project) when pip output is unreadable;
    the failure is logged as an error.
    """
    if not is_venv_valid(project_dir):
        return 0
    try:
        data = run_pip_json(project_dir, ["list", "--format=json"], timeout=30)
    except PipJsonError as exc:
        _log.error("Cannot count installed packages: %s", exc)
        return 0
    names = {item.get("name", "").lower() for item in data}
    return len(names - BOOTSTRAP_PACKAGES)


def venv_has_real_packages(project_dir: Path) -> bool:
    """True when the venv has at least one non-bootstrap package."""
    return venv_package_count(project_dir) > 0


def installed_package_versions(project_dir: Path) -> dict[str, str]:
    """Return {normalized_name: version} for every installed package.

    Returns ``{}`` only for a missing/invalid venv. For a valid venv, an
    unreadable or empty ``pip list`` raises ``PipJsonError``: a real venv
    always contains at least pip, so an empty set means the query failed.
    """
    if not is_venv_valid(project_dir):
        return {}
    data = run_pip_json(project_dir, ["list", "--format=json"], timeout=30)
    try:
        versions = {normalize(item["name"]): item["version"] for item in data}
    except (TypeError, KeyError) as exc:
        raise PipJsonError(
            f"pip list returned unexpected JSON for {project_dir.name}: {exc}"
        ) from exc
    if not versions:
        raise PipJsonError(f"pip list reported no packages for {project_dir.name}")
    return versions


def find_outdated_packages(project_dir: Path) -> list[OutdatedPackage]:
    """Query outdated packages. Returns raw results (no safety filtering)."""
    if not is_venv_valid(project_dir):
        return []

    try:
        data = run_pip_json(
            project_dir, ["list", "--outdated", "--format=json"], timeout=90,
        )
    except PipJsonError as exc:
        # No candidates means nothing is upgraded: a safe outcome, but logged.
        _log.error("Cannot list outdated packages: %s", exc)
        return []
    return [
        OutdatedPackage(
            name=item.get("name", ""),
            current_version=item.get("version", ""),
            latest_version=item.get("latest_version", ""),
            package_type=item.get("type", "wheel"),
        )
        for item in data
    ]
