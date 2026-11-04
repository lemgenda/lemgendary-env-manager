"""MetaTrader 5 detection: registry, well-known paths, then ``Get-Package``.

MT5 detection prefers the Windows registry (fast, ~5ms) over
PackageManagement's ``Get-Package`` (slow, cold-start 10-40s on some
systems). The MT5 file version is extracted from terminal64.exe when
possible so the reported version is authoritative regardless of whether
winget tracks the install.
"""

import ctypes
import importlib
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from env_manager._logging import get_logger
from env_manager.probe.models import MetaTrader5Info
from env_manager.probe.shell import ps_quote, run_powershell

_log = get_logger(__name__)

MT5_REGISTRY_KEYS = (
    ("HKEY_CURRENT_USER", r"SOFTWARE\MetaQuotes\Terminal"),
    ("HKEY_LOCAL_MACHINE", r"SOFTWARE\MetaQuotes\Terminal"),
    ("HKEY_CURRENT_USER", r"SOFTWARE\WOW6432Node\MetaQuotes\Terminal"),
    ("HKEY_LOCAL_MACHINE", r"SOFTWARE\WOW6432Node\MetaQuotes\Terminal"),
)
"""Registry keys MT5 installers register under (hive name, key path)."""

GET_PACKAGE_QUERY = (
    "Get-Package -Name '*MetaTrader*' "
    "| Select-Object -Property Name, Version "
    "| ConvertTo-Json -Compress"
)
"""PowerShell PackageManagement query used as the slow last-resort MT5 probe."""


def _pe_file_version(exe_path: Path) -> str | None:
    """Read FileVersion from the PE version resource in-process (Windows only)."""
    # ctypes.wintypes and ctypes.windll exist only on Windows, so the
    # module is loaded by name inside the platform guard.
    wintypes = importlib.import_module("ctypes.wintypes")
    version_dll = ctypes.windll.version
    try:
        size = version_dll.GetFileVersionInfoSizeW(str(exe_path), None)
        if size <= 0:
            return None
        res = ctypes.create_string_buffer(size)
        if not version_dll.GetFileVersionInfoW(str(exe_path), 0, size, res):
            return None
        u_len = wintypes.UINT()
        lp_buffer = wintypes.LPVOID()
        if not version_dll.VerQueryValueW(
            res, "\\", ctypes.byref(lp_buffer), ctypes.byref(u_len)
        ) or lp_buffer.value is None:
            return None
        # VS_FIXEDFILEINFO starts with dwSignature, dwStrucVersion,
        # dwFileVersionMS, dwFileVersionLS.
        head = (wintypes.DWORD * 4).from_address(lp_buffer.value)
        v_ms, v_ls = head[2], head[3]
        return f"{v_ms >> 16}.{v_ms & 0xffff}.{v_ls >> 16}.{v_ls & 0xffff}"
    except (OSError, ValueError, ctypes.ArgumentError) as exc:
        _log.debug("In-process ctypes version extraction failed for %s: %s", exe_path, exc)
        return None


def _read_exe_version(exe_path: Path) -> str | None:
    """Extract FileVersion from a Windows PE executable in-process without spawning consoles.

    Falls back to PowerShell's ``VersionInfo`` when the in-process read
    yields nothing.
    """
    if not exe_path.is_file():
        return None
    if sys.platform == "win32":
        version = _pe_file_version(exe_path)
        if version is not None:
            return version
    command = f"(Get-Item -LiteralPath {ps_quote(str(exe_path))}).VersionInfo.FileVersion"
    try:
        proc = run_powershell(command, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("Version read failed for %s: %s", exe_path, exc)
        return None
    if proc.returncode == 0:
        return (proc.stdout or "").strip() or None
    return None


def _read_mt5_subkey(winreg: Any, hive: Any, key_path: str, subkey: str) -> MetaTrader5Info:
    """Read the install path and version from one registered MT5 terminal."""
    values: dict[str, str] = {}
    try:
        with winreg.OpenKey(hive, f"{key_path}\\{subkey}") as sub:
            # Some MT5 builds register a "Version" value directly.
            for value_name in ("Origin", "Version"):
                try:
                    value, _ = winreg.QueryValueEx(sub, value_name)
                except OSError as exc:
                    _log.debug("No %s key in MT5 subkey %s: %s", value_name, subkey, exc)
                    continue
                if value:
                    values[value_name] = str(value)
    except OSError as exc:
        _log.debug("Could not open MT5 subkey %s: %s", subkey, exc)

    origin_path = values.get("Origin")
    version = values.get("Version")
    # Fall back to reading terminal64.exe's file version.
    if version is None and origin_path:
        exe = Path(origin_path) / "terminal64.exe"
        if exe.is_file():
            version = _read_exe_version(exe)
    return MetaTrader5Info(installed=True, install_path=origin_path, version=version)


def _mt5_registry_probe() -> MetaTrader5Info | None:
    """Query Windows registry for MetaTrader 5. Fast (~5ms).

    MT5 registers under several possible keys depending on installer
    variant and 32/64-bit placement. Any of them implies an install.
    When a registered origin path is found, the version is read directly
    from terminal64.exe so callers get an authoritative version number.
    """
    if sys.platform != "win32":
        return None
    # winreg exists only on Windows, so it is loaded by name after the guard.
    winreg = importlib.import_module("winreg")

    for hive_name, key_path in MT5_REGISTRY_KEYS:
        hive = getattr(winreg, hive_name)
        try:
            with winreg.OpenKey(hive, key_path) as key:
                subkey = winreg.EnumKey(key, 0)
        except FileNotFoundError:
            continue
        except OSError as exc:
            _log.debug("MT5 registry probe: %s\\%s: %s", hive, key_path, exc)
            continue
        return _read_mt5_subkey(winreg, hive, key_path, subkey)
    return None


def mt5_known_paths_probe() -> MetaTrader5Info | None:
    """Check well-known install locations for the MT5 terminal binary."""
    # Install roots come only from the environment; there is no hardcoded
    # drive-letter fallback.
    candidates = [
        Path(root) / "MetaTrader 5" / "terminal64.exe"
        for root in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"))
        if root
    ]
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        candidates.append(Path(local_appdata) / "Programs" / "MetaTrader 5" / "terminal64.exe")
        candidates.append(Path(local_appdata) / "MetaQuotes" / "Terminal" / "terminal64.exe")

    for path in candidates:
        if path.is_file():
            return MetaTrader5Info(
                installed=True, install_path=str(path), version=_read_exe_version(path),
            )
    return None


def _mt5_get_package_probe(timeout: int = 45) -> MetaTrader5Info | None:
    """Fallback MT5 probe using PackageManagement's Get-Package."""
    try:
        proc = run_powershell(GET_PACKAGE_QUERY, timeout=timeout)
        if proc.returncode != 0 or not proc.stdout.strip():
            # Get-Package exits non-zero when no MetaTrader package exists,
            # which is the normal "not installed" answer.
            _log.debug("Get-Package found no MT5 (exit %s): %s",
                       proc.returncode, (proc.stderr or "").strip()[:200])
            return None
        data = json.loads(proc.stdout.strip())
    except subprocess.TimeoutExpired:
        _log.warning("Get-Package MT5 probe timed out after %ss", timeout)
        return None
    except json.JSONDecodeError as exc:
        _log.warning("Get-Package MT5 probe JSON parse failed: %s", exc)
        return None
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("Get-Package MT5 probe failed: %s", exc)
        return None

    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        _log.warning("Get-Package MT5 probe returned unexpected JSON: %r", data)
        return None
    version = data[0].get("Version") or None
    return MetaTrader5Info(installed=True, version=str(version) if version else None)


def probe_metatrader5() -> MetaTrader5Info:
    """Probe MetaTrader 5 installation on Windows.

    Detection order (fastest first):
    1. Windows registry (native Python ``winreg``, ~5ms)
    2. Well-known install paths (~1ms)
    3. PackageManagement ``Get-Package`` (slow, up to 45s, only as fallback)

    The version is extracted from terminal64.exe's FileVersion property
    when an install path is known. This makes the reported version
    authoritative even when winget doesn't track the installation.
    """
    if platform.system().lower() != "windows":
        return MetaTrader5Info(installed=False)

    for source, probe in (
        ("registry", _mt5_registry_probe),
        ("known path", mt5_known_paths_probe),
        ("Get-Package", _mt5_get_package_probe),
    ):
        info = probe()
        if info is not None:
            _log.debug("MT5 detected via %s: path=%s version=%s",
                       source, info.install_path, info.version)
            return info
    return MetaTrader5Info(installed=False)
