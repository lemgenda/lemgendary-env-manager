"""
LemGendary Environment Manager Shared Utilities
================================================
Common helpers for cache reclamation, process execution, and version inspection.
"""

import os
import queue
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import IO
from collections.abc import Callable, Generator

from env_manager._logging import get_logger

_log = get_logger(__name__)

WINDOWS_NO_WINDOW = (
    subprocess.CREATE_NO_WINDOW
    if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW")
    else 0x08000000 if sys.platform == "win32"
    else 0
)


def get_subprocess_creation_flags() -> int:
    """Return CREATE_NO_WINDOW flag on Windows to prevent console flashing, else 0."""
    return WINDOWS_NO_WINDOW


# ─── Environment for pip subprocesses ───────────────────────────────────────

def pip_env(no_cache: bool = False) -> dict[str, str]:
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

_CACHE_CORRUPTION_MARKERS: tuple[str, ...] = (
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
    """Best-effort purge of pip's HTTP cache.

    Launch failures and timeouts are logged and swallowed deliberately: the
    purge is an optimisation before a retry, and the retry reports its own
    errors.
    """
    try:
        subprocess.run(
            [str(python_path), "-m", "pip", "cache", "purge"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120, check=False, env=pip_env(),
            creationflags=WINDOWS_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("pip cache purge failed for %s: %s", python_path, exc)


# ─── Pip runner with recovery ───────────────────────────────────────────────

_STDOUT = "stdout"
_STDERR = "stderr"


def _pump_stream(
    stream: IO[str],
    tag: str,
    sink: queue.Queue[tuple[str, str | None]],
) -> None:
    """Push every line of ``stream`` into ``sink`` tagged with its origin.

    A final ``(tag, None)`` marks end of stream. Runs in a reader thread so
    stdout and stderr drain concurrently and neither pipe can fill up and
    block the child process.
    """
    try:
        for line in iter(stream.readline, ""):
            sink.put((tag, line))
    finally:
        stream.close()
        sink.put((tag, None))


def wait_or_kill(proc: subprocess.Popen, timeout: float) -> int:
    """Wait for ``proc``; on timeout kill it, reap it, and re-raise ``TimeoutExpired``.

    Killing first matters inside ``with Popen(...)``: the context manager
    waits for the child on exit, which would otherwise block forever.
    """
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _log.warning("Killing PID %d after %ss timeout", proc.pid, timeout)
        proc.kill()
        proc.wait()
        raise


def stream_pip_with_recovery(
    python_path: Path,
    args: list[str],
    timeout: int = 1800,
    max_attempts: int = 2,
) -> Generator[str, None, subprocess.CompletedProcess]:
    """Run ``python -m pip <args>`` with recovery logic, yielding output lines in real time.

    stdout and stderr are captured separately (review finding D-07). Both are
    yielded live for progress display, but the returned ``CompletedProcess``
    keeps them apart: ``stdout`` holds only what pip wrote to stdout, so
    callers can parse ``--format=json`` / ``--report -`` output without pip
    warnings (for example ``WARNING: Ignoring invalid distribution``)
    corrupting it, and ``stderr`` holds pip's warnings and errors.
    """
    cmd = [str(python_path), "-m", "pip", *args]

    last_proc: subprocess.CompletedProcess | None = None
    for attempt in range(1, max_attempts + 1):
        use_no_cache = attempt > 1
        output_lines: list[str] = []
        error_lines: list[str] = []
        try:
            cmd_env = pip_env(no_cache=use_no_cache)
            cmd_env["PYTHONUNBUFFERED"] = "1"
            with subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=cmd_env,
                creationflags=WINDOWS_NO_WINDOW,
            ) as proc:
                sink: queue.Queue[tuple[str, str | None]] = queue.Queue()
                readers = [
                    threading.Thread(target=_pump_stream, args=(stream, tag, sink), daemon=True)
                    for stream, tag in ((proc.stdout, _STDOUT), (proc.stderr, _STDERR))
                    if stream is not None
                ]
                for reader in readers:
                    reader.start()
                open_streams = len(readers)
                while open_streams:
                    tag, line = sink.get()
                    if line is None:
                        open_streams -= 1
                        continue
                    if tag == _STDOUT:
                        # Keep stdout byte-for-byte (minus the newline) so JSON
                        # documents stay intact.
                        output_lines.append(line.rstrip("\r\n"))
                    else:
                        error_lines.append(line.rstrip("\r\n"))
                    stripped = line.strip()
                    if stripped:
                        yield stripped
                for reader in readers:
                    reader.join()
                wait_or_kill(proc, timeout)
                completed = subprocess.CompletedProcess(
                    args=cmd,
                    returncode=proc.returncode,
                    stdout="\n".join(output_lines),
                    stderr="\n".join(error_lines),
                )
        except subprocess.TimeoutExpired as exc:
            _log.warning("pip command timed out after %ss: %s", timeout, cmd)
            return subprocess.CompletedProcess(
                args=cmd, returncode=124, stdout="\n".join(output_lines),
                stderr="\n".join([*error_lines, f"pip command timed out after {timeout}s: {exc}"]),
            )
        except (OSError, ValueError) as exc:
            _log.warning("pip command could not be run (%s): %s", cmd, exc)
            return subprocess.CompletedProcess(
                args=cmd, returncode=1, stdout="\n".join(output_lines),
                stderr="\n".join([*error_lines, str(exc)]),
            )

        if completed.returncode == 0:
            return completed

        last_proc = completed
        corruption = _looks_like_cache_corruption(completed.stderr, completed.stdout)

        if attempt < max_attempts and corruption:
            _log.warning(
                "pip failed with a corruption-class error; purging HTTP cache "
                "and retrying with --no-cache-dir (attempt %d/%d). Tail: %s",
                attempt, max_attempts, (completed.stderr or completed.stdout or "")[-200:],
            )
            _purge_pip_cache(python_path)
            yield (
                "Purged corrupt pip cache; retrying installation with --no-cache-dir "
                f"(attempt {attempt + 1}/{max_attempts})..."
            )
            continue

        # Not a corruption signature, or out of attempts.
        return completed

    return last_proc if last_proc is not None else subprocess.CompletedProcess(
        args=cmd, returncode=1, stdout="", stderr="pip execution failed",
    )


def run_pip_with_recovery(
    python_path: Path,
    args: list[str],
    timeout: int = 1800,
    max_attempts: int = 2,
    on_line: Callable[[str], None] | None = None,
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
    gen = stream_pip_with_recovery(python_path, args, timeout=timeout, max_attempts=max_attempts)
    try:
        while True:
            line = next(gen)
            if on_line:
                on_line(line)
    except StopIteration as stop:
        return stop.value


# ─── Cache / bytecode cleaning ──────────────────────────────────────────────

def purge_project_cache(project_dir: Path) -> tuple[int, int]:
    """Purge __pycache__ and bytecode artifacts from a project directory, ignoring .venv."""
    cleaned_count = 0
    total_reclaimed = 0

    ignored_dirs = {".venv", ".git", "node_modules", ".cache"}
    for root, dirs, files in os.walk(project_dir):
        dirs[:] = [d for d in dirs if d not in ignored_dirs]
        for d in list(dirs):
            if d == "__pycache__":
                cache_path = Path(root) / d
                try:
                    size = sum(f.stat().st_size for f in cache_path.rglob("*") if f.is_file())
                    shutil.rmtree(cache_path)
                    total_reclaimed += size
                    cleaned_count += 1
                except OSError as exc:
                    _log.warning("Failed to remove __pycache__ dir %s: %s", cache_path, exc)
                dirs.remove(d)

        for f in files:
            if f.endswith((".pyc", ".pyo", ".pyd")):
                item = Path(root) / f
                try:
                    size = item.stat().st_size
                    item.unlink()
                    total_reclaimed += size
                    cleaned_count += 1
                except OSError as exc:
                    _log.warning("Failed to remove bytecode file %s: %s", item, exc)

    return cleaned_count, total_reclaimed


# ─── Simple subprocess runner ───────────────────────────────────────────────

def run_command_simple(
    cmd: list[str],
    timeout: int = 5,
    cwd: Path | None = None,
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
        creationflags=WINDOWS_NO_WINDOW,
    )
