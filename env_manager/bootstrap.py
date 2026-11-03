"""Bootstrap verification and global environment check module.

Ensures Python 3.12+, pip, git, and venv module
are properly installed and accessible across platforms.

Also checks for MetaTrader 5 installation and surfaces software update
availability via winget for Python and MetaTrader 5.
"""

import platform
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Any

from env_manager._logging import get_logger
from env_manager.system_probe import probe_metatrader5
from env_manager.utils import run_command_simple

_log = get_logger(__name__)

MIN_PYTHON = (3, 12)
"""Oldest Python the ecosystem supports (``Path.walk``, ``rmtree(onexc=...)``, ``datetime.UTC``).

Keep in sync with ``requires-python`` in ``pyproject.toml``.
"""

WINERROR_ALIAS_RESOLUTION = 1920
"""WinError raised when an App Execution Alias such as winget cannot be resolved."""


@dataclass
class SoftwareUpdateInfo:
    """Tracks available update for a software package.

    ``source_tracked`` is False when winget has no record of the package
    (typically because it was installed outside winget's package database,
    or because the package ID has been delisted from the winget source).
    In that case ``latest_version`` will normally be None and callers
    should display the package as "installed, not tracked by winget"
    rather than "unknown".
    """
    name: str
    current_version: str | None
    latest_version: str | None
    update_available: bool
    install_command: str | None = None
    source_tracked: bool = True


@dataclass
class BootstrapStatus:
    """Status report for host system toolchain prerequisites."""
    python_valid: bool
    python_version: str
    python_executable: str
    pip_installed: bool
    venv_available: bool
    git_installed: bool
    git_version: str | None
    npm_installed: bool
    npm_version: str | None
    mt5_installed: bool
    mt5_version: str | None
    mt5_path: str | None
    missing_prerequisites: list[str]
    remediation_instructions: list[str]
    software_updates: list[SoftwareUpdateInfo]

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a plain dict for JSON output."""
        return asdict(self)


# ─── Toolchain probes ───────────────────────────────────────────────────────

def check_git() -> tuple[bool, str | None]:
    """Check if git is installed and return version."""
    git_path = shutil.which("git")
    if not git_path:
        return False, None
    try:
        proc = run_command_simple([git_path, "--version"], timeout=5)
        if proc.returncode == 0:
            return True, proc.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("git version check failed: %s", exc)
    return False, None


def check_npm() -> tuple[bool, str | None]:
    """Check if npm is installed and return version."""
    npm_path = shutil.which("npm")
    if not npm_path:
        return False, None
    try:
        proc = run_command_simple([npm_path, "--version"], timeout=5)
        if proc.returncode == 0:
            return True, proc.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("npm version check failed: %s", exc)
    return False, None


def check_venv() -> bool:
    """Check if python -m venv works."""
    try:
        proc = run_command_simple([sys.executable, "-m", "venv", "--help"])
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("venv module check failed: %s", exc)
        return False


def check_metatrader5() -> tuple[bool, str | None, str | None]:
    """Detect MetaTrader 5 (returns: installed, version, path).

    Delegates to :func:`env_manager.system_probe.probe_metatrader5`, which
    uses a registry-first strategy and extracts the file version from
    terminal64.exe when an install path is known. This makes the version
    authoritative even when winget doesn't track the install.
    """
    info = probe_metatrader5()
    return info.installed, info.version, info.install_path


# ─── winget invocation ─────────────────────────────────────────────────────

def _winget_command_prefix() -> list[str] | None:
    """Return the command prefix for invoking winget, or None if unavailable.

    On Windows, ``shutil.which("winget")`` returns the App Execution Alias
    stub in ``%LOCALAPPDATA%\\Microsoft\\WindowsApps\\``. Passing that stub
    path directly to ``subprocess.run`` triggers ``WinError 1920``
    (``ERROR_CANT_ACCESS_FILE``) because ``CreateProcess`` cannot resolve
    the reparse point to the real ``winget.exe`` inside the ACL-protected
    ``WindowsApps`` directory.

    Routing through ``cmd.exe`` fixes this. cmd uses different file
    resolution semantics that correctly follow the alias.
    """
    winget_path = shutil.which("winget")
    if not winget_path:
        return None

    if "windowsapps" in winget_path.lower():
        return ["cmd", "/c", "winget"]

    return [winget_path]


def _run_winget(
    args: list[str],
    timeout: int = 30,
) -> subprocess.CompletedProcess | None:
    """Invoke winget with the correct prefix and encoding.

    Returns the CompletedProcess on success, or None if winget could not
    be invoked at all.
    """
    prefix = _winget_command_prefix()
    if prefix is None:
        return None

    try:
        return run_command_simple(prefix + args, timeout=timeout)
    except OSError as exc:
        winerror = getattr(exc, "winerror", None)
        if winerror == WINERROR_ALIAS_RESOLUTION:
            _log.debug(
                "winget subprocess returned WinError 1920 (alias resolution "
                "failure) for args=%s.",
                args,
            )
        else:
            _log.warning("winget subprocess failed (winerror=%s): %s", winerror, exc)
    except subprocess.SubprocessError as exc:
        _log.warning("winget subprocess failed for args=%s (timeout=%ss): %s", args, timeout, exc)

    return None


# ─── winget output parsers ─────────────────────────────────────────────────

def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from winget output."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text or "")


def _parse_winget_list_version(stdout: str, pkg_id: str) -> str | None:
    """Extract the installed version of ``pkg_id`` from ``winget list`` output."""
    text = _strip_ansi(stdout)
    pkg_lower = pkg_id.lower()

    for line in text.splitlines():
        if pkg_lower not in line.lower():
            continue

        if re.match(r"^\s*Name\s+Id\s+", line, flags=re.IGNORECASE):
            continue
        stripped = line.strip()
        if stripped and set(stripped) <= set("- "):
            continue

        cells = [c.strip() for c in re.split(r"\s{2,}", stripped) if c.strip()]
        for i, cell in enumerate(cells):
            if cell.lower() == pkg_lower:
                if i + 1 < len(cells):
                    candidate = cells[i + 1]
                    if re.match(r"^\d", candidate):
                        return candidate
                break

        for token in stripped.split():
            if re.match(r"^\d+(\.\d+){1,}", token):
                return token

    return None


def _parse_winget_show_version(stdout: str) -> str | None:
    """Extract the ``Version: X.Y.Z`` line from ``winget show`` output."""
    text = _strip_ansi(stdout)
    for line in text.splitlines():
        m = re.match(r"^\s*Version:\s*(\S+)", line)
        if m:
            return m.group(1)
    return None


def _winget_query_versions(pkg_id: str) -> tuple[str | None, str | None]:
    """Return (installed_version, available_version) for a winget package."""
    installed: str | None = None
    available: str | None = None

    list_proc = _run_winget(
        ["list", "--id", pkg_id, "--exact", "--accept-source-agreements"],
        timeout=60,
    )
    if list_proc is not None and list_proc.returncode == 0:
        installed = _parse_winget_list_version(list_proc.stdout, pkg_id)

    show_proc = _run_winget(
        ["show", "--id", pkg_id, "--exact", "--accept-source-agreements"],
        timeout=60,
    )
    if show_proc is not None and show_proc.returncode == 0:
        available = _parse_winget_show_version(show_proc.stdout)

    return installed, available


# ─── winget update check ───────────────────────────────────────────────────

def check_winget_updates() -> list[SoftwareUpdateInfo]:
    """Check for available software updates via winget for Python and MetaTrader 5.

    For each target package:

    - Installed version comes from ``winget list`` when winget tracks the
      package. For MetaTrader 5, when winget has no record, the pipeline's
      own registry/filesystem probe is used as a fallback so the version
      is still reported.
    - Latest version comes from ``winget show``. ``None`` when the package
      is not in the winget source.
    - ``source_tracked`` is True if either winget list or winget show
      returned data for the package. When False, callers should display
      the package as "installed, not tracked by winget" rather than
      collapsing it to "unknown".
    """
    updates: list[SoftwareUpdateInfo] = []
    if platform.system().lower() != "windows":
        return updates

    if _winget_command_prefix() is None:
        _log.debug("winget not found on PATH; skipping software update check.")
        return updates

    targets = [
        (
            "Python.Python.3.12",
            "Python 3.12",
            "winget install --id Python.Python.3.12 --silent "
            "--accept-source-agreements --accept-package-agreements",
        ),
        (
            "MetaQuotes.MetaTrader5",
            "MetaTrader 5",
            "winget install --id MetaQuotes.MetaTrader5 --silent "
            "--accept-source-agreements --accept-package-agreements",
        ),
    ]

    for pkg_id, display_name, install_cmd in targets:
        installed_ver, available_ver = _winget_query_versions(pkg_id)

        # Fallback: for MT5, when winget doesn't know about the install,
        # try the pipeline's own detection (registry + terminal64.exe
        # file version). Keeps the display honest when the package was
        # installed manually.
        if installed_ver is None and pkg_id == "MetaQuotes.MetaTrader5":
            mt5_info = probe_metatrader5()
            if mt5_info.installed:
                installed_ver = mt5_info.version

        source_tracked = installed_ver is not None or available_ver is not None

        update_available = False
        if installed_ver and available_ver:
            update_available = installed_ver != available_ver
        elif source_tracked:
            upgrade_proc = _run_winget(
                ["upgrade", "--id", pkg_id, "--accept-source-agreements"],
                timeout=30,
            )
            if upgrade_proc is not None:
                output = (upgrade_proc.stdout or "") + (upgrade_proc.stderr or "")
                update_available = (
                    upgrade_proc.returncode == 0
                    and "no applicable" not in output.lower()
                    and pkg_id.lower() in output.lower()
                )

        updates.append(
            SoftwareUpdateInfo(
                name=display_name,
                current_version=installed_ver,
                latest_version=available_ver,
                update_available=update_available,
                install_command=install_cmd if update_available else None,
                source_tracked=source_tracked,
            )
        )

    return updates


# ─── winget install ────────────────────────────────────────────────────────

def install_software(name: str) -> tuple[bool, str]:
    """Download and silently install a software package via winget."""
    if platform.system().lower() != "windows":
        return False, "Automated software installation is only supported on Windows."

    prefix = _winget_command_prefix()
    if prefix is None:
        return False, "winget is not available. Install it from the Microsoft Store."

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
        proc = run_command_simple(
            [
                *prefix, "install", "--id", pkg_id, "--silent",
                "--accept-source-agreements", "--accept-package-agreements",
            ],
            timeout=900,
        )
        if proc.returncode == 0:
            return True, f"Successfully installed {name} (winget id: {pkg_id})."
        return False, (
            proc.stderr or proc.stdout
            or f"winget install failed (exit {proc.returncode})."
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("winget install of %s failed: %s", pkg_id, exc)
        return False, str(exc)


# ─── Full verification ─────────────────────────────────────────────────────

REMEDIATION: dict[str, dict[str, str]] = {
    "python": {
        "windows": "Install Python 3.12 using: winget install Python.Python.3.12",
        "linux": "Install Python 3.12 using: sudo apt-get install python3.12 python3.12-venv",
        "": "Install Python 3.12 using: brew install python@3.12",
    },
    "python-venv": {"linux": "Install venv using: sudo apt-get install python3-venv"},
    "git": {
        "windows": "Install Git using: winget install Git.Git",
        "linux": "Install Git using: sudo apt-get install git",
        "": "Install Git using: brew install git",
    },
    "npm": {
        "windows": "Install Node.js & npm using: winget install OpenJS.NodeJS.LTS",
        "linux": "Install Node.js & npm using: sudo apt-get install nodejs npm",
        "": "Install Node.js & npm using: brew install node",
    },
    "metatrader5": {
        "windows": "Install MetaTrader 5 using: "
                   "winget install --id MetaQuotes.MetaTrader5 --silent --accept-source-agreements",
    },
}
"""Install instruction per prerequisite and OS; the ``""`` entry covers other systems (macOS)."""


def remediation_for(missing_flags: dict[str, bool], current_os: str) -> tuple[list[str], list[str]]:
    """Return ``(missing, instructions)`` for the prerequisites flagged as missing.

    A prerequisite with no instruction for ``current_os`` (and no ``""``
    fallback) is still listed as missing.
    """
    missing = [name for name, is_missing in missing_flags.items() if is_missing]
    instructions: list[str] = []
    for name in missing:
        by_os = REMEDIATION[name.split(">=", maxsplit=1)[0]]
        instruction = by_os.get(current_os, by_os.get(""))
        if instruction:
            instructions.append(instruction)
    return missing, instructions


def verify_prerequisites() -> BootstrapStatus:
    """Verify all host toolchain prerequisites."""
    major = sys.version_info.major
    minor = sys.version_info.minor
    patch = sys.version_info.micro
    v_str = f"{major}.{minor}.{patch}"
    python_valid = sys.version_info[:2] >= MIN_PYTHON

    pip_installed = shutil.which("pip") is not None
    venv_available = check_venv()
    git_installed, git_version = check_git()
    npm_installed, npm_version = check_npm()
    mt5_installed, mt5_version, mt5_path = check_metatrader5()
    software_updates = check_winget_updates()

    missing, instructions = remediation_for(
        {
            f"python>={MIN_PYTHON[0]}.{MIN_PYTHON[1]}": not python_valid,
            "python-venv": not venv_available,
            "git": not git_installed,
            "npm": not npm_installed,
            "metatrader5": not mt5_installed and platform.system().lower() == "windows",
        },
        platform.system().lower(),
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
