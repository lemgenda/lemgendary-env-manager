"""Virtual environment lifecycle management module.

Handles creation, validation, package querying, and pip execution
across Windows, Linux, and macOS platforms.
"""

import json
import os
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from env_manager.utils import run_command_simple


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

    def to_dict(self) -> Dict[str, Any]:
        """Convert info to dictionary."""
        return asdict(self)


def get_venv_python_path(project_dir: Path) -> Path:
    """Resolve OS-specific path to virtual environment python executable."""
    venv_dir = project_dir / ".venv"
    if platform.system().lower() == "windows":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def is_venv_valid(project_dir: Path) -> bool:
    """Check if project virtual environment exists and is operational."""
    python_path = get_venv_python_path(project_dir)
    if not python_path.exists() or not python_path.is_file():
        return False
    try:
        proc = run_command_simple([str(python_path), "--version"])
        return proc.returncode == 0
    except Exception:
        return False


def get_venv_python_version(project_dir: Path) -> Optional[str]:
    """Retrieve Python version of the project virtual environment."""
    python_path = get_venv_python_path(project_dir)
    if not is_venv_valid(project_dir):
        return None
    try:
        proc = run_command_simple([str(python_path), "--version"])
        if proc.returncode == 0:
            return proc.stdout.strip()
    except Exception:
        pass
    return None



def create_venv(project_dir: Path) -> tuple[bool, str]:
    """Create a virtual environment in project_dir/.venv."""
    venv_dir = project_dir / ".venv"
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            return False, proc.stderr or "Failed to create virtual environment."

        # Upgrade pip and wheel in new environment
        python_path = get_venv_python_path(project_dir)
        upgrade_proc = subprocess.run(
            [str(python_path), "-m", "pip", "install", "--upgrade", "pip", "wheel", "setuptools"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        return True, "Virtual environment created and upgraded successfully."
    except Exception as exc:
        return False, str(exc)


def get_installed_packages(project_dir: Path) -> Dict[str, str]:
    """Query list of installed packages as {name: version} mapping."""
    python_path = get_venv_python_path(project_dir)
    if not is_venv_valid(project_dir):
        return {}

    try:
        proc = subprocess.run(
            [str(python_path), "-m", "pip", "list", "--format=json"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if proc.returncode == 0:
            data = json.loads(proc.stdout.strip())
            return {item["name"].lower(): item["version"] for item in data}
    except Exception:
        pass
    return {}


def install_requirements(
    project_dir: Path,
    requirements_file: Path,
    extra_index_url: Optional[str] = None,
    timeout: int = 600,
) -> tuple[bool, str]:
    """Install requirements file into project virtual environment."""
    python_path = get_venv_python_path(project_dir)
    if not is_venv_valid(project_dir):
        return False, f"Virtual environment at {project_dir} is invalid or missing."

    cmd = [str(python_path), "-m", "pip", "install", "-r", str(requirements_file)]
    if extra_index_url:
        cmd.extend(["--extra-index-url", extra_index_url])

    try:
        proc = run_command_simple(cmd, timeout=timeout)
        if proc.returncode == 0:
            return True, proc.stdout
        return False, proc.stderr or proc.stdout or "Pip install failed."

    except Exception as exc:
        return False, str(exc)


def discover_projects(base_dir: Optional[Path] = None) -> List[ProjectVenvInfo]:
    """Discover standard LemGendary sibling projects."""
    if base_dir is None:
        # Default to parent directory of lemgendary-env-manager
        base_dir = Path(__file__).resolve().parent.parent.parent

    target_projects = [
        "lemgendary-training-suite",
        "lemgendary-datasets",
        "lemgendary-env-manager",
    ]

    discovered: List[ProjectVenvInfo] = []
    for proj_name in target_projects:
        p_dir = base_dir / proj_name
        if p_dir.exists() and p_dir.is_dir():
            v_dir = p_dir / ".venv"
            valid = is_venv_valid(p_dir)
            py_path = str(get_venv_python_path(p_dir)) if valid else None
            py_ver = get_venv_python_version(p_dir) if valid else None
            pkgs = get_installed_packages(p_dir) if valid else {}

            discovered.append(
                ProjectVenvInfo(
                    name=proj_name,
                    project_dir=str(p_dir),
                    venv_dir=str(v_dir),
                    python_path=py_path,
                    is_valid=valid,
                    python_version=py_ver,
                    installed_packages_count=len(pkgs),
                )
            )
    return discovered
