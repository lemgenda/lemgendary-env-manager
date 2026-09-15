"""
LemGendary Environment Manager Shared Utilities
================================================
Common helpers for cache reclamation, process execution, and version inspection.
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from env_manager._logging import get_logger

_log = get_logger(__name__)


# ─── Environment for pip subprocesses ───────────────────────────────────────

def pip_env(no_cache: bool = False) -> Dict[str, str]:
    """Return an environment for pip subprocesses that is safe on Windows.

    Two Windows-specific pitfalls are handled here:

    1. pip's rich formatter emits unicode characters (unicorn emoji, non-breaking
       hyphens, arrows). On consoles with cp1252 code pages this crashes the
       subprocess mid-output with UnicodeEncodeError, which callers misread as
       a resolver failure. PYTHONIOENCODING / PYTHONUTF8 force UTF-8.

    2. Reading a corrupt wheel from pip's HTTP cache can crash the C-level
       wheel reader with an access violation. ``no_cache=True`` adds
       PIP_NO_CACHE_DIR=1 so pip reads nothing from the cache; used on the
       retry attempt after a corruption signature has been detected.
    """
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    env["PIP_NO_COLOR"] = "1"
    env["NO_COLOR"] = "1"
    if no_cache:
        env["PIP_NO_CACHE_DIR"] = "1"
    else:
        # Explicitly unset in case the parent shell has it set.
        env.pop("PIP_NO_CACHE_DIR", None)
    return env


# ─── Cache-corruption detection ─────────────────────────────────────────────

_CACHE_CORRUPTION_MARKERS: Tuple[str, ...] = (
    "access violation",
    "Retry(total=",
    "after connection broken",
    "OSError('exception:",
    "ReadTimeoutError",
    "ProtocolError",
    "IncompleteRead",
    "ChunkedEncodingError",
    "hash mismatch",
    "does not match the hash",
    "Wheel file is corrupt",
    "THESE PACKAGES DO NOT MATCH THE HASHES",
)


def _looks_like_cache_corruption(stderr: str, stdout: str) -> bool:
    """Detect the class of pip failures caused by a corrupt download or cache."""
    combined = (stderr or "") + "\n" + (stdout or "")
    return any(marker in combined for marker in _CACHE_CORRUPTION_MARKERS)


def _purge_pip_cache(python_path: Path) -> None:
    """Best-effort purge of pip's HTTP cache. Never raises."""
    try:
        subprocess.run(
            [str(python_path), "-m", "pip", "cache", "purge"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120, check=False, env=pip_env(),
        )
    except Exception as exc:
        _log.debug("pip cache purge failed: %s", exc)


# ─── Pip runner with recovery ───────────────────────────────────────────────

def run_pip_with_recovery(
    python_path: Path,
    args: List[str],
    timeout: int = 1800,
    max_attempts: int = 2,
) -> subprocess.CompletedProcess:
    """Run ``python -m pip <args>`` with cache-first, no-cache-on-retry logic.

    Attempt 1: run with the cache enabled. This is the fast path. Large
    wheels (torch, opencv-contrib-python, etc.) that were downloaded for a
    previous step are reused instead of re-fetched.

    Attempt 2 (only if attempt 1 fails with a corruption-class signature):
    purge the HTTP cache, then retry with ``--no-cache-dir`` so nothing
    corrupt can be read back. This handles the case where the cache itself
    holds a truncated or damaged wheel.

    Any other failure (resolver conflict, missing package, auth, timeout)
    returns immediately without retrying.

    Returns the CompletedProcess from the final attempt. Callers should
    check ``returncode`` and inspect ``stdout`` / ``stderr`` themselves.
    """
    cmd = [str(python_path), "-m", "pip"] + args

    last_proc: Optional[subprocess.CompletedProcess] = None
    for attempt in range(1, max_attempts + 1):
        use_no_cache = attempt > 1
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                env=pip_env(no_cache=use_no_cache),
            )
        except subprocess.TimeoutExpired as exc:
            _log.warning("pip command timed out after %ss: %s", timeout, cmd)
            return subprocess.CompletedProcess(
                args=cmd, returncode=124, stdout="",
                stderr=f"pip command timed out after {timeout}s: {exc}",
            )
        except Exception as exc:
            _log.warning("pip command crashed before completion: %s", exc)
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="", stderr=str(exc),
            )

        if proc.returncode == 0:
            return proc

        last_proc = proc
        corruption = _looks_like_cache_corruption(proc.stderr, proc.stdout)

        if attempt < max_attempts and corruption:
            _log.warning(
                "pip failed with a corruption-class error; purging HTTP cache "
                "and retrying with --no-cache-dir (attempt %d/%d). Tail: %s",
                attempt, max_attempts, (proc.stderr or proc.stdout or "")[-200:],
            )
            _purge_pip_cache(python_path)
            continue

        # Not a corruption signature, or out of attempts.
        return proc

    return last_proc if last_proc is not None else subprocess.CompletedProcess(
        args=cmd, returncode=1, stdout="", stderr="pip execution failed",
    )


# ─── Cache / bytecode cleaning ──────────────────────────────────────────────

def purge_project_cache(project_dir: Path) -> Tuple[int, int]:
    """Purge __pycache__ and bytecode artifacts from a project directory, ignoring .venv."""
    cleaned_count = 0
    total_reclaimed = 0

    for item in project_dir.rglob("__pycache__"):
        if ".venv" in item.parts:
            continue
        if item.is_dir():
            try:
                size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
                shutil.rmtree(item)
                total_reclaimed += size
                cleaned_count += 1
            except Exception as exc:
                _log.debug("Failed to remove __pycache__ dir %s: %s", item, exc)

    for pattern in ["*.pyc", "*.pyo", "*.pyd"]:
        for item in project_dir.rglob(pattern):
            if ".venv" in item.parts:
                continue
            if item.is_file():
                try:
                    size = item.stat().st_size
                    item.unlink()
                    total_reclaimed += size
                    cleaned_count += 1
                except Exception as exc:
                    _log.debug("Failed to remove bytecode file %s: %s", item, exc)

    return cleaned_count, total_reclaimed


# ─── Simple subprocess runner ───────────────────────────────────────────────

def run_command_simple(
    cmd: List[str],
    timeout: int = 5,
    cwd: Optional[Path] = None,
    use_pip_env: bool = False,
) -> subprocess.CompletedProcess:
    """Execute a simple subprocess with consistent timeout and capture defaults.

    Set ``use_pip_env=True`` when ``cmd`` invokes pip (directly or via
    ``python -m pip``) so the subprocess gets UTF-8 encoding and colour
    disabled.
    """
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=str(cwd) if cwd else None,
        check=False,
        env=pip_env() if use_pip_env else None,
    )