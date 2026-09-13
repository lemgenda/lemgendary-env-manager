"""Bootstrap verification and global environment check module.

Ensures Python 3.10+ (preferably 3.12+), pip, git, and venv module
are properly installed and accessible across platforms.

Also checks for MetaTrader 5 installation and surfaces software update
availability via winget for Python and MetaTrader 5.
"""

import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from env_manager._logging import get_logger

_log = get_logger(__name__)

from env_manager.utils import run_command_simple


@dataclass
class SoftwareUpdateInfo:
    """Tracks available update for a software package."""
    name: str
    current_version: Optional[str]
    latest_version: Optional[str]
    update_available: bool
    install_command: Optional[str] = None


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
    mt5_installed: bool
    mt5_version: Optional[str]
    mt5_path: Optional[str]
    missing_prerequisites: List[str]
    remediation_instructions: List[str]
    software_updates: List[SoftwareUpdateInfo]

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
    except Exception as exc:
        _log.warning("git version check failed: %s", exc)
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
    except Exception as exc:
        _log.warning("npm version check failed: %s", exc)
    return False, None


def check_venv() -> bool:
    """Check if python -m venv works."""
    try:
        proc = run_command_simple([sys.executable, "-m", "venv", "--help"])
        return proc.returncode == 0
    except Exception:
        return False


def check_metatrader5() -> tuple[bool, Optional[str], Optional[str]]:
    """Detect MetaTrader 5 using Get-Package (returns: installed, version, path)."""
    import os
    if platform.system().lower() != "windows":
        return False, None, None

    # Primary: PowerShell Get-Package (preferred detection method)
    try:
        ps_cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "Get-Package -Name '*MetaTrader*' -ErrorAction SilentlyContinue "
                "| Select-Object -Property Name, Version "
                "| ConvertTo-Json"
            ),
        ]
        proc = subprocess.run(ps_cmd, capture_output=True, text=True, timeout=10, check=False)
        if proc.returncode == 0 and proc.stdout.strip():
            import json
            try:
                data = json.loads(proc.stdout.strip())
                if isinstance(data, dict):
                    data = [data]
                if data:
                    entry = data[0]
                    version = str(entry.get("Version") or "Unknown")
                    return True, version, None
            except (json.JSONDecodeError, KeyError, IndexError) as exc:
                _log.debug("Get-Package MT5 JSON parse failed: %s", exc)
    except Exception as exc:
        _log.warning("Get-Package MT5 check failed: %s", exc)



    prog_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    prog_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    known_paths = [
        os.path.join(prog_files, "MetaTrader 5", "terminal64.exe"),
        os.path.join(prog_files_x86, "MetaTrader 5", "terminal64.exe"),
    ]
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        known_paths.append(
            os.path.join(os.path.dirname(appdata), "Local", "Programs", "MetaTrader 5", "terminal64.exe")
        )

    for path in known_paths:
        if os.path.isfile(path):
            return True, None, path

    return False, None, None


def check_winget_updates() -> List[SoftwareUpdateInfo]:
    """Check for available software updates via winget for Python and MetaTrader 5.

    Uses 'winget upgrade --id <id>' to check if updates are available.
    """
    updates: List[SoftwareUpdateInfo] = []
    if platform.system().lower() != "windows":
        return updates

    winget_path = shutil.which("winget")
    if not winget_path:
        return updates

    # Packages to check: (winget_id, display_name, install_command)
    targets = [
        (
            "Python.Python.3.12",
            "Python 3.12",
            "winget install --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements",
        ),
        (
            "MetaQuotes.MetaTrader5",
            "MetaTrader 5",
            "winget install --id MetaQuotes.MetaTrader5 --silent --accept-source-agreements --accept-package-agreements",
        ),
    ]

    for pkg_id, display_name, install_cmd in targets:
        try:
            proc = subprocess.run(
                [winget_path, "upgrade", "--id", pkg_id, "--accept-source-agreements"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            output = proc.stdout + proc.stderr
            # winget returns 0 and lists the package if an upgrade is available
            # If no upgrade is available it typically says "No applicable update found"
            update_available = (
                proc.returncode == 0
                and "no applicable" not in output.lower()
                and pkg_id.lower() in output.lower()
            )
            current_ver: Optional[str] = None
            latest_ver: Optional[str] = None
            for line in output.splitlines():
                lower = line.lower()
                if pkg_id.lower() in lower and len(line.split()) >= 3:
                    parts = line.split()
                    # winget upgrade output: Name  Id  Version  Available  Source
                    if len(parts) >= 4:
                        current_ver = parts[2]
                        latest_ver = parts[3]
                    break

            updates.append(
                SoftwareUpdateInfo(
                    name=display_name,
                    current_version=current_ver,
                    latest_version=latest_ver,
                    update_available=update_available,
                    install_command=install_cmd if update_available else None,
                )
            )
        except Exception as exc:
            _log.warning("winget update check for '%s' failed: %s", pkg_id, exc)

    return updates


def install_software(name: str) -> tuple[bool, str]:
    """Download and silently install a software package.

    Supported names: 'python', 'metatrader5'
    Uses winget for all installations on Windows.
    """
    if platform.system().lower() != "windows":
        return False, "Automated software installation is only supported on Windows."

    winget_path = shutil.which("winget")
    if not winget_path:
        return False, "winget is not available. Please install from the Microsoft Store."

    name_lower = name.lower().replace(" ", "").replace("-", "").replace("_", "")
    pkg_map = {
        "python": "Python.Python.3.12",
        "python3": "Python.Python.3.12",
        "python312": "Python.Python.3.12",
        "metatrader5": "MetaQuotes.MetaTrader5",
        "mt5": "MetaQuotes.MetaTrader5",
        "metatrader": "MetaQuotes.MetaTrader5",
    }

    pkg_id = pkg_map.get(name_lower)
    if not pkg_id:
        return False, f"Unknown software package '{name}'. Supported: python, metatrader5"

    try:
        proc = subprocess.run(
            [
                winget_path,
                "install",
                "--id", pkg_id,
                "--silent",
                "--accept-source-agreements",
                "--accept-package-agreements",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if proc.returncode == 0:
            return True, f"Successfully installed {name} (winget id: {pkg_id})."
        return False, proc.stderr or proc.stdout or f"winget install failed (exit {proc.returncode})."
    except Exception as exc:
        return False, str(exc)


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
    mt5_installed, mt5_version, mt5_path = check_metatrader5()
    software_updates = check_winget_updates()

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

    if not mt5_installed and current_os == "windows":
        missing.append("metatrader5")
        instructions.append(
            "Install MetaTrader 5 using: "
            "winget install --id MetaQuotes.MetaTrader5 --silent --accept-source-agreements"
        )

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
        mt5_installed=mt5_installed,
        mt5_version=mt5_version,
        mt5_path=mt5_path,
        missing_prerequisites=missing,
        remediation_instructions=instructions,
        software_updates=software_updates,
    )
