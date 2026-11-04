"""Virtual environment lifecycle management module."""

import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from collections.abc import Callable, Generator

import psutil

from env_manager._logging import get_logger
from env_manager.ecosystem import ecosystem
from env_manager.utils import (
    drain_lines,
    run_command_simple,
    run_pip_with_recovery,
    stream_pip_with_recovery,
)
from env_manager.workspace import workspace_root

_log = get_logger(__name__)


OPENCV_VARIANTS: tuple[str, ...] = (
    "opencv-python",
    "opencv-python-headless",
    "opencv-contrib-python",
    "opencv-contrib-python-headless",
)


@dataclass
class ProjectVenvInfo:
    """Project virtual environment status metadata."""
    name: str
    project_dir: str
    venv_dir: str
    python_path: str | None
    is_valid: bool
    python_version: str | None = None
    installed_packages_count: int = 0
    is_node_project: bool = False
    node_modules_present: bool = False
    has_npm_manifest: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a plain dict for JSON output."""
        return asdict(self)


def get_venv_python_path(project_dir: Path) -> Path:
    """Path of the project's venv interpreter for this platform."""
    venv_dir = project_dir / ".venv"
    is_windows = platform.system().lower() == "windows"
    python_exe_name = "python.exe" if is_windows else "python"
    python_subfolder = "Scripts" if is_windows else "bin"
    return venv_dir / python_subfolder / python_exe_name


def _probe_venv_python(project_dir: Path) -> str | None:
    """Run the venv interpreter once; return its ``--version`` text, or None if invalid.

    A venv is valid when ``.venv`` has a ``pyvenv.cfg`` and an interpreter
    that runs.
    """
    python_path = get_venv_python_path(project_dir)
    cfg_path = project_dir / ".venv" / "pyvenv.cfg"
    if not cfg_path.exists() or not python_path.is_file():
        return None
    try:
        proc = run_command_simple([str(python_path), "--version"])
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("venv python --version check failed for %s: %s", project_dir, exc)
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.strip() or proc.stderr.strip()


def is_venv_valid(project_dir: Path) -> bool:
    """True when ``.venv`` has a ``pyvenv.cfg`` and an interpreter that runs."""
    return _probe_venv_python(project_dir) is not None


def get_venv_python_version(project_dir: Path) -> str | None:
    """Return the venv interpreter version, or None when the venv is invalid."""
    return _probe_venv_python(project_dir)


def _handle_remove_readonly(func, path, exc):
    """``shutil.rmtree`` onexc hook: clear the read-only bit and retry once.

    If the retry fails too, the error is logged and re-raised so rmtree
    reports it to the caller instead of leaving a half-deleted tree silently.
    """
    _log.debug("rmtree hit %s on %s; clearing read-only bit and retrying", exc, path)
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError as error:
        _log.warning("Failed to remove %s after clearing read-only bit: %s", path, error)
        raise


def _is_inside_active_venv(venv_dir: Path) -> bool:
    try:
        current_exe = Path(sys.executable).resolve()
        target_dir = venv_dir.resolve()
        if hasattr(current_exe, "is_relative_to"):
            return current_exe.is_relative_to(target_dir)
        try:
            current_exe.relative_to(target_dir)
            return True
        except ValueError:
            return False
    except OSError as exc:
        _log.debug("Failed to resolve executable path for venv check: %s", exc)
        return False


def find_processes_in_venv(venv_dir: Path) -> list[Any]:
    """Find all active processes whose executable or working directory is inside the venv."""
    procs = []
    try:
        venv_resolved = str(venv_dir.resolve()).lower()
        for p in psutil.process_iter(["pid", "name", "exe"]):
            try:
                exe = p.info.get("exe")
                if exe and str(Path(exe).resolve()).lower().startswith(venv_resolved):
                    procs.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
                _log.debug("Skipping process during venv lock scan: %s", exc)
                continue
    except (OSError, psutil.Error) as exc:
        _log.warning("Process discovery in venv %s failed: %s", venv_dir, exc)
    return procs


def terminate_processes_in_venv(venv_dir: Path, timeout: float = 3.0) -> tuple[bool, list[int]]:
    """Terminate any active processes locking files inside the virtual environment."""
    procs = find_processes_in_venv(venv_dir)
    if not procs:
        return True, []

    pids = [p.pid for p in procs]
    _log.info("Terminating %d process(es) locking %s (PIDs: %s)", len(procs), venv_dir, pids)
    for p in procs:
        try:
            p.terminate()
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            _log.warning("Could not terminate PID %d: %s", p.pid, exc)

    _gone, alive = psutil.wait_procs(procs, timeout=timeout)
    for p in alive:
        try:
            p.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied) as exc:
            _log.warning("Could not kill PID %d: %s", p.pid, exc)

    return len(alive) == 0, pids


def _remove_existing_venv(project_dir: Path, venv_dir: Path) -> str | None:
    """Delete ``venv_dir`` after stopping processes that lock it.

    Returns an error message when Windows file locks prevent the removal.
    On Windows the tree is renamed first so a locked file fails fast.
    """
    terminate_processes_in_venv(venv_dir)
    if platform.system().lower() != "windows":
        shutil.rmtree(venv_dir, onexc=_handle_remove_readonly)
        return None
    temp_trash = project_dir / f".venv_trash_{int(time.time())}"
    try:
        venv_dir.rename(temp_trash)
        shutil.rmtree(temp_trash, onexc=_handle_remove_readonly)
    except OSError as lock_exc:
        remaining = find_processes_in_venv(venv_dir)
        rem_info = [f"PID {p.pid} ({p.name()})" for p in remaining]
        _log.warning("Cannot remove %s: %s", venv_dir, lock_exc)
        return (
            f"Cannot recreate virtual environment for {project_dir.name}: "
            f"files are locked by active process(es): {', '.join(rem_info) or str(lock_exc)}. "
            f"Stop all running background services, workers, or terminals using this environment."
        )
    return None


def _build_venv(project_dir: Path, venv_dir: Path) -> tuple[bool, str]:
    """Create ``venv_dir`` with the base interpreter and upgrade pip, wheel and setuptools."""
    base_python = getattr(sys, "_base_executable", sys.executable)
    proc = run_command_simple([str(base_python), "-m", "venv", str(venv_dir)], timeout=120)
    if proc.returncode != 0:
        return False, proc.stderr or f"Failed to execute venv module via {base_python}."
    if not (venv_dir / "pyvenv.cfg").exists():
        return False, "Failed to create virtual environment: no pyvenv.cfg file created."
    upgrade_proc = run_pip_with_recovery(
        get_venv_python_path(project_dir),
        ["install", "--upgrade", "pip", "wheel", "setuptools"],
        timeout=300,
    )
    if upgrade_proc.returncode != 0:
        return False, f"Venv spawned, but core upgrade failed: {upgrade_proc.stderr}"
    return True, "Virtual environment created and upgraded successfully."


def create_venv(project_dir: Path) -> tuple[bool, str]:
    """(Re)create ``project_dir/.venv``; refuses to replace the running interpreter's venv."""
    venv_dir = project_dir / ".venv"

    if _is_inside_active_venv(venv_dir):
        if is_venv_valid(project_dir):
            _log.info("Refusing to recreate active orchestrator venv at '%s'.", venv_dir)
            return True, "Active execution environment preserved intact."
        return False, (
            "Refusing to recreate the currently executing venv, and it is "
            "not valid. Run this command from outside the target venv."
        )

    try:
        if venv_dir.exists():
            removal_error = _remove_existing_venv(project_dir, venv_dir)
            if removal_error:
                return False, removal_error
        return _build_venv(project_dir, venv_dir)
    except (OSError, subprocess.SubprocessError) as exc:
        _log.error("Creating the venv for %s failed: %s", project_dir, exc)
        return False, str(exc)


def get_installed_packages(project_dir: Path) -> dict[str, str]:
    """Return ``{name: version}`` for the venv's packages, or ``{}`` when invalid."""
    if not is_venv_valid(project_dir):
        return {}
    return _pip_list(project_dir)


def _pip_list(project_dir: Path) -> dict[str, str]:
    """Return ``{name: version}`` from ``pip list`` in a venv already known to be valid."""
    python_path = get_venv_python_path(project_dir)
    proc = run_pip_with_recovery(
        python_path, ["list", "--format=json"], timeout=30,
    )
    # stderr is captured separately from stdout (D-07): log it, parse stdout only.
    for line in (proc.stderr or "").splitlines():
        if line.strip():
            _log.warning("pip list (%s): %s", project_dir.name, line.strip())
    if proc.returncode != 0:
        _log.error("pip list failed in %s with code %s", project_dir, proc.returncode)
        return {}
    try:
        data = json.loads(proc.stdout.strip())
        return {item["name"].lower(): item["version"] for item in data}
    except (json.JSONDecodeError, TypeError, KeyError) as exc:
        _log.error("Failed to parse installed packages in %s: %s", project_dir, exc)
    return {}


def stream_install_requirements(
    project_dir: Path,
    requirements_file: Path,
    extra_index_url: str | None = None,
    timeout: int = 1800,
) -> Generator[str, None, tuple[bool, str]]:
    """Install a requirements file into the project venv, yielding output lines in real time."""
    if not is_venv_valid(project_dir):
        return False, f"Virtual environment at {project_dir} is invalid or missing."

    python_path = get_venv_python_path(project_dir)
    args = ["install", "-r", str(requirements_file), "--progress-bar", "on"]
    if extra_index_url:
        args.extend(["--extra-index-url", extra_index_url])

    proc = yield from stream_pip_with_recovery(python_path, args, timeout=timeout)
    if proc.returncode == 0:
        return True, proc.stdout or "ok"
    return False, proc.stderr or proc.stdout or "Pip install failed."


def install_requirements(
    project_dir: Path,
    requirements_file: Path,
    extra_index_url: str | None = None,
    timeout: int = 1800,
    on_line: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """Install a requirements file into the project venv.

    Uses :func:`stream_install_requirements` so a corrupted download or stale
    cache entry triggers an automatic purge-and-retry rather than a
    hard failure.
    """
    return drain_lines(
        stream_install_requirements(
            project_dir,
            requirements_file,
            extra_index_url=extra_index_url,
            timeout=timeout,
        ),
        on_line,
    )


def normalize_opencv_variant(
    project_dir: Path,
    desired_variant: str,
    timeout: int = 900,
) -> tuple[bool, str]:
    """Force a single OpenCV provider inside a project venv.

    The uninstall + force-reinstall cycle on Windows is slow because each
    file is deleted and rewritten one at a time, with real-time antivirus
    scanning every operation. The ``opencv-contrib-python`` wheel is ~54 MB
    with thousands of files, so the default timeout is 900s rather than the
    smaller default used elsewhere. If this still times out, the bottleneck
    is almost certainly antivirus; add the project tree to your AV
    exclusion list.
    """
    if desired_variant not in OPENCV_VARIANTS:
        return False, f"Unknown OpenCV variant: {desired_variant}"
    if not is_venv_valid(project_dir):
        return False, "Invalid or missing venv"

    installed = get_installed_packages(project_dir)
    installed_norm = {k.lower().replace("_", "-"): v for k, v in installed.items()}

    undesired = [
        variant for variant in OPENCV_VARIANTS
        if variant != desired_variant and variant in installed_norm
    ]

    python_path = get_venv_python_path(project_dir)

    if undesired:
        proc = run_pip_with_recovery(
            python_path, ["uninstall", "-y", *undesired], timeout=timeout,
        )
        if proc.returncode != 0:
            return False, (
                f"Failed to uninstall {undesired}: "
                f"{(proc.stderr or proc.stdout)[:200]}"
            )

    proc = run_pip_with_recovery(
        python_path,
        ["install", "--force-reinstall", "--no-deps", desired_variant],
        timeout=timeout,
    )
    if proc.returncode == 0:
        removed = ", ".join(undesired) if undesired else "none"
        return True, f"OpenCV normalized to {desired_variant} (removed: {removed})"
    return False, (
        f"Failed to install {desired_variant}: "
        f"{(proc.stderr or proc.stdout)[:200]}"
    )


def discover_projects(base_dir: Path | None = None) -> list[ProjectVenvInfo]:
    """Find the managed projects under ``base_dir`` and describe each one's environment.

    The projects and their order come from ``ecosystem.json``
    (``projects[].managed``, ``projects[].upgrade_order``, ``projects[].kind``).
    """
    if base_dir is None:
        base_dir = workspace_root()

    discovered: list[ProjectVenvInfo] = []
    for project in ecosystem().managed_projects():
        p_dir = base_dir / project.folder
        if p_dir.is_dir():
            describe = _describe_node_project if project.is_node else _describe_python_project
            discovered.append(describe(project.folder, p_dir))
    return discovered


def _describe_python_project(name: str, p_dir: Path) -> ProjectVenvInfo:
    # One interpreter run per project: validity and version together.
    py_ver = _probe_venv_python(p_dir)
    valid = py_ver is not None
    py_path = str(get_venv_python_path(p_dir)) if valid else None
    pkgs = _pip_list(p_dir) if valid else {}
    return ProjectVenvInfo(
        name=name, project_dir=str(p_dir), venv_dir=str(p_dir / ".venv"),
        python_path=py_path, is_valid=valid, python_version=py_ver,
        installed_packages_count=len(pkgs),
        is_node_project=False, node_modules_present=False,
        has_npm_manifest=(p_dir / "package.json").exists(),
    )


def _describe_node_project(name: str, p_dir: Path) -> ProjectVenvInfo:
    node_modules_present = (p_dir / "node_modules").exists()
    return ProjectVenvInfo(
        name=name, project_dir=str(p_dir), venv_dir="",
        python_path=None, is_valid=node_modules_present,
        python_version=None, installed_packages_count=0,
        is_node_project=True, node_modules_present=node_modules_present,
        has_npm_manifest=(p_dir / "package.json").exists(),
    )
