"""Helpers for pipeline step 3: the running venv and node_modules purges."""

import shutil
import sys
from pathlib import Path

from env_manager._logging import get_logger

_log = get_logger(__name__)


def get_active_venv() -> Path | None:
    """Return the venv directory that the current process is running from.

    The answer comes from ``sys.prefix`` rather than from resolving
    ``sys.executable``: on Linux and macOS the venv interpreter is a symlink
    to the base Python, so resolving the leaf binary lands outside the venv.
    Returns None when the process does not run inside a venv.
    """
    if sys.prefix == sys.base_prefix:
        return None
    try:
        return Path(sys.prefix).resolve()
    except (OSError, RuntimeError) as exc:
        _log.warning("Cannot resolve the running venv prefix %s: %s", sys.prefix, exc)
        return None


def is_inside_active_venv(venv_dir: Path) -> bool:
    """Return True when ``venv_dir`` is the venv this process runs from."""
    return is_same_venv(venv_dir, get_active_venv())


def process_runs_from_venv(candidates: list[str], venv_dir: Path) -> bool:
    """Return True when any interpreter path in ``candidates`` lives in ``venv_dir``.

    Only the parent directory is resolved, never the leaf binary, because a
    POSIX venv interpreter is a symlink that resolves outside the venv.
    ``candidates`` holds a process ``exe`` and ``cmdline[0]``; on Linux the
    kernel already reports ``exe`` resolved, so ``cmdline[0]`` is what still
    names the venv path. Relative entries are skipped.
    """
    try:
        venv_root = venv_dir.resolve()
    except (OSError, RuntimeError) as exc:
        _log.warning("Cannot resolve venv path %s: %s", venv_dir, exc)
        return False
    for candidate in candidates:
        # A relative argv[0] would resolve against this process's cwd.
        if not candidate or not Path(candidate).is_absolute():
            continue
        try:
            parent = Path(candidate).parent.resolve()
        except (OSError, RuntimeError) as exc:
            _log.debug("Cannot resolve process path %s: %s", candidate, exc)
            continue
        if parent.is_relative_to(venv_root):
            return True
    return False


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
