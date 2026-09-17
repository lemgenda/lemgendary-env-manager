"""Virtual environment lifecycle management module."""

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

from env_manager._logging import get_logger
from env_manager.utils import pip_env, run_command_simple, run_pip_with_recovery

_log = get_logger(__name__)


OPENCV_VARIANTS: Tuple[str, ...] = (
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
    python_path: Optional[str]
    is_valid: bool
    python_version: Optional[str] = None
    installed_packages_count: int = 0
    is_node_project: bool = False
    node_modules_present: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def get_venv_python_path(project_dir: Path) -> Path:
    venv_dir = project_dir / ".venv"
    is_windows = platform.system().lower() == "windows"
    python_exe_name = "python.exe" if is_windows else "python"
    python_subfolder = "Scripts" if is_windows else "bin"
    return venv_dir / python_subfolder / python_exe_name


def is_venv_valid(project_dir: Path) -> bool:
    venv_dir = project_dir / ".venv"
    python_path = get_venv_python_path(project_dir)

    cfg_path = venv_dir / "pyvenv.cfg"
    if not cfg_path.exists() or not python_path.exists() or not python_path.is_file():
        return False
    try:
        proc = run_command_simple([str(python_path), "--version"])
        return proc.returncode == 0
    except Exception as exc:
        _log.debug("venv python --version check failed for %s: %s", project_dir, exc)
        return False


def get_venv_python_version(project_dir: Path) -> Optional[str]:
    if not is_venv_valid(project_dir):
        return None
    python_path = get_venv_python_path(project_dir)
    try:
        proc = run_command_simple([str(python_path), "--version"])
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception as exc:
        _log.debug("venv python version query failed for %s: %s", project_dir, exc)
    return None


def _handle_remove_readonly(func, path, exc):
    import stat
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception as error:
        _log.debug("Failed to clear read-only file lock for %s: %s", path, error)


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


def create_venv(project_dir: Path) -> Tuple[bool, str]:
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
            shutil.rmtree(venv_dir, onexc=_handle_remove_readonly)

        base_python = getattr(sys, "_base_executable", sys.executable)

        proc = subprocess.run(
            [str(base_python), "-m", "venv", str(venv_dir)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, check=False,
        )
        if proc.returncode != 0:
            return False, proc.stderr or f"Failed to execute venv module via {base_python}."

        cfg_path = venv_dir / "pyvenv.cfg"
        if not cfg_path.exists():
            return False, "Failed to create virtual environment: no pyvenv.cfg file created."

        python_path = get_venv_python_path(project_dir)
        upgrade_proc = run_pip_with_recovery(
            python_path,
            ["install", "--upgrade", "pip", "wheel", "setuptools"],
            timeout=300,
        )
        if upgrade_proc.returncode != 0:
            return False, f"Venv spawned, but core upgrade failed: {upgrade_proc.stderr}"

        return True, "Virtual environment created and upgraded successfully."
    except Exception as exc:
        return False, str(exc)


def get_installed_packages(project_dir: Path) -> Dict[str, str]:
    if not is_venv_valid(project_dir):
        return {}
    python_path = get_venv_python_path(project_dir)

    try:
        proc = run_pip_with_recovery(
            python_path, ["list", "--format=json"], timeout=30,
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout.strip())
            return {item["name"].lower(): item["version"] for item in data}
    except Exception as exc:
        _log.warning("Failed to list installed packages in %s: %s", project_dir, exc)
    return {}


def install_requirements(
    project_dir: Path,
    requirements_file: Path,
    extra_index_url: Optional[str] = None,
    timeout: int = 1800,
) -> Tuple[bool, str]:
    """Install a requirements file into the project venv.

    Uses :func:`run_pip_with_recovery` so a corrupted download or stale
    cache entry triggers an automatic purge-and-retry rather than a
    hard failure.
    """
    if not is_venv_valid(project_dir):
        return False, f"Virtual environment at {project_dir} is invalid or missing."

    python_path = get_venv_python_path(project_dir)
    args = ["install", "-r", str(requirements_file)]
    if extra_index_url:
        args.extend(["--extra-index-url", extra_index_url])

    proc = run_pip_with_recovery(python_path, args, timeout=timeout)
    if proc.returncode == 0:
        return True, proc.stdout or "ok"
    return False, proc.stderr or proc.stdout or "Pip install failed."


def normalize_opencv_variant(
    project_dir: Path,
    desired_variant: str,
    timeout: int = 900,
) -> Tuple[bool, str]:
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


def discover_projects(base_dir: Optional[Path] = None) -> List[ProjectVenvInfo]:
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent.parent

    python_projects = [
        "lemgendary-env-manager",
        "lemgendary-datasets",
        "lemgendary-training-suite",
    ]
    node_projects = ["lemgendary-ai-studio-gui"]

    discovered: List[ProjectVenvInfo] = []

    for proj_name in python_projects:
        p_dir = base_dir / proj_name
        if p_dir.exists() and p_dir.is_dir():
            v_dir = p_dir / ".venv"
            valid = is_venv_valid(p_dir)
            py_path = str(get_venv_python_path(p_dir)) if valid else None
            py_ver = get_venv_python_version(p_dir) if valid else None
            pkgs = get_installed_packages(p_dir) if valid else {}

            discovered.append(
                ProjectVenvInfo(
                    name=proj_name, project_dir=str(p_dir), venv_dir=str(v_dir),
                    python_path=py_path, is_valid=valid, python_version=py_ver,
                    installed_packages_count=len(pkgs),
                    is_node_project=False, node_modules_present=False,
                )
            )

    for proj_name in node_projects:
        p_dir = base_dir / proj_name
        if p_dir.exists() and p_dir.is_dir():
            node_modules_present = (p_dir / "node_modules").exists()
            discovered.append(
                ProjectVenvInfo(
                    name=proj_name, project_dir=str(p_dir), venv_dir="",
                    python_path=None, is_valid=node_modules_present,
                    python_version=None, installed_packages_count=0,
                    is_node_project=True, node_modules_present=node_modules_present,
                )
            )

    return discovered