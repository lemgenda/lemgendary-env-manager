"""Helpers for pipeline step 3: the running venv and node_modules purges."""

import shutil
import sys
from pathlib import Path

from env_manager._logging import get_logger

_log = get_logger(__name__)


def get_active_venv() -> Path | None:
    """Return the .venv directory that the current process is running from."""
    try:
        exe = Path(sys.executable).resolve()
    except (OSError, RuntimeError) as exc:
        _log.warning("Cannot resolve the running interpreter %s: %s", sys.executable, exc)
        return None
    for parent in exe.parents:
        if parent.name == ".venv":
            return parent
    return None


def is_same_venv(venv_dir: Path, active_venv: Path | None) -> bool:
    """Return True when ``venv_dir`` is the venv this process runs from."""
    if active_venv is None:
        return False
    try:
        return venv_dir.resolve() == active_venv
    except (OSError, RuntimeError) as exc:
        _log.warning("Cannot resolve venv path %s: %s", venv_dir, exc)
        return False


def purge_node_modules(nm_dir: Path) -> str | None:
    """Delete a node_modules tree; return the error text if it fails."""
    try:
        shutil.rmtree(nm_dir)
    except OSError as exc:
        return str(exc)
    return None
