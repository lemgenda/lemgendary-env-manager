"""Bootstrap verification and global environment check module.

Ensures Python 3.10+ (preferably 3.12+), pip, git, and venv module
are properly installed and accessible across platforms.
"""

import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


@dataclass
class BootstrapStatus:
    """Status report for host system toolchain prerequisites."""
    python_valid: bool
    python_version: str
    python_executable: str
    pip_installed: bool
    venv_available: bool
    git_installed: bool
    git_version: Optional[str]
    npm_installed: bool
    npm_version: Optional[str]
    missing_prerequisites: List[str]
    remediation_instructions: List[str]

    def to_dict(self) -> Dict[str, Any]:
        """Convert status to dictionary."""
        return asdict(self)


def check_git() -> tuple[bool, Optional[str]]:
    """Check if git is installed and return version."""
    git_path = shutil.which("git")
    if not git_path:
        return False, None
    try:
        proc = subprocess.run([git_path, "--version"], capture_output=True, text=True, timeout=5, check=False)
        if proc.returncode == 0:
            return True, proc.stdout.strip()
    except Exception:
        pass
    return False, None


def check_npm() -> tuple[bool, Optional[str]]:
    """Check if npm is installed and return version."""
    npm_path = shutil.which("npm")
    if not npm_path:
        return False, None
    try:
        proc = subprocess.run([npm_path, "--version"], capture_output=True, text=True, timeout=5, check=False)
        if proc.returncode == 0:
            return True, proc.stdout.strip()
    except Exception:
        pass
    return False, None


def check_venv() -> bool:
    """Check if python -m venv works."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "venv", "--help"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return proc.returncode == 0
    except Exception:
        return False


def verify_prerequisites() -> BootstrapStatus:
    """Verify all host toolchain prerequisites."""
    major = sys.version_info.major
    minor = sys.version_info.minor
    patch = sys.version_info.micro
    v_str = f"{major}.{minor}.{patch}"
    python_valid = (major == 3 and minor >= 10)

    pip_installed = shutil.which("pip") is not None
    venv_available = check_venv()
    git_installed, git_version = check_git()
    npm_installed, npm_version = check_npm()

    missing: List[str] = []
    instructions: List[str] = []
    current_os = platform.system().lower()

    if not python_valid:
        missing.append("python>=3.10")
        if current_os == "windows":
            instructions.append("Install Python 3.12 using: winget install Python.Python.3.12")
        elif current_os == "linux":
            instructions.append("Install Python 3.12 using: sudo apt-get install python3.12 python3.12-venv")
        else:
            instructions.append("Install Python 3.12 using: brew install python@3.12")

    if not venv_available:
        missing.append("python-venv")
        if current_os == "linux":
            instructions.append("Install venv using: sudo apt-get install python3-venv")

    if not git_installed:
        missing.append("git")
        if current_os == "windows":
            instructions.append("Install Git using: winget install Git.Git")
        elif current_os == "linux":
            instructions.append("Install Git using: sudo apt-get install git")
        else:
            instructions.append("Install Git using: brew install git")

    if not npm_installed:
        missing.append("npm")
        if current_os == "windows":
            instructions.append("Install Node.js & npm using: winget install OpenJS.NodeJS.LTS")
        elif current_os == "linux":
            instructions.append("Install Node.js & npm using: sudo apt-get install nodejs npm")
        else:
            instructions.append("Install Node.js & npm using: brew install node")

    return BootstrapStatus(
        python_valid=python_valid,
        python_version=v_str,
        python_executable=sys.executable,
        pip_installed=pip_installed,
        venv_available=venv_available,
        git_installed=git_installed,
        git_version=git_version,
        npm_installed=npm_installed,
        npm_version=npm_version,
        missing_prerequisites=missing,
        remediation_instructions=instructions,
    )
