"""System and hardware probing module for LemGendary Environment Manager.

Detects operating system, CPU, memory, and accelerator backends:
- NVIDIA CUDA
- AMD ROCm
- DirectML (Windows)
- CPU Fallback

Also probes MetaTrader 5 terminal installation on Windows. MT5 detection
prefers the Windows registry (fast, ~5ms) over PackageManagement's
``Get-Package`` (slow, cold-start 10-40s on some systems). The MT5 file
version is extracted from terminal64.exe when possible so the reported
version is authoritative regardless of whether winget tracks the install.
"""

import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from env_manager._logging import get_logger
from env_manager.utils import pip_env

_log = get_logger(__name__)

try:
    import psutil
except ImportError:
    psutil = None


@dataclass
class AcceleratorDevice:
    """Represents an accelerated computing device (GPU/NPU)."""
    index: int
    name: str
    total_memory_mb: int
    backend: str
    driver_version: Optional[str] = None
    compute_capability: Optional[str] = None


@dataclass
class MetaTrader5Info:
    """MetaTrader 5 terminal installation details."""
    installed: bool
    version: Optional[str] = None
    install_path: Optional[str] = None
    update_available: bool = False


@dataclass
class HardwareProfile:
    """Comprehensive system hardware and runtime profile."""
    os_name: str
    os_version: str
    os_release: str
    architecture: str
    python_version: str
    python_executable: str
    cpu_count_logical: int
    cpu_count_physical: int
    total_ram_mb: int
    primary_backend: str
    accelerators: List[AcceleratorDevice]
    recommended_torch_index: str
    metatrader5: Optional[MetaTrader5Info] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─── Shell selection helper ─────────────────────────────────────────────────

def _preferred_shell() -> str:
    """Return 'pwsh' if PowerShell 7 is available, else 'powershell'."""
    if shutil.which("pwsh"):
        return "pwsh"
    return "powershell"


# ─── GPU probes ─────────────────────────────────────────────────────────────

def probe_nvidia_smi() -> List[AcceleratorDevice]:
    """Probe NVIDIA GPUs using nvidia-smi if available."""
    devices: List[AcceleratorDevice] = []
    nvidia_smi_path = shutil.which("nvidia-smi")
    if not nvidia_smi_path:
        return devices

    query_cmd = [
        nvidia_smi_path,
        "--query-gpu=index,name,memory.total,driver_version",
        "--format=csv,noheader,nounits",
    ]
    try:
        proc = subprocess.run(
            query_cmd,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=5, check=False,
        )
        if proc.returncode == 0:
            for line in proc.stdout.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 4:
                    try:
                        idx = int(parts[0])
                        name = parts[1]
                        mem = int(parts[2])
                        driver = parts[3]
                        devices.append(
                            AcceleratorDevice(
                                index=idx,
                                name=name,
                                total_memory_mb=mem,
                                backend="cuda",
                                driver_version=driver,
                            )
                        )
                    except (ValueError, IndexError):
                        continue
    except Exception as exc:
        _log.warning("nvidia-smi probe failed: %s", exc)
    return devices


def probe_rocm_smi() -> List[AcceleratorDevice]:
    """Probe AMD GPUs using rocm-smi if available."""
    devices: List[AcceleratorDevice] = []
    rocm_smi_path = shutil.which("rocm-smi")
    if not rocm_smi_path:
        return devices

    query_cmd = [rocm_smi_path, "--showid", "--showproductname", "--showmeminfo", "vram"]
    try:
        proc = subprocess.run(
            query_cmd,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=5, check=False,
        )
        if proc.returncode == 0:
            lines = proc.stdout.strip().splitlines()
            for i, line in enumerate(lines):
                if "GPU[" in line:
                    devices.append(
                        AcceleratorDevice(
                            index=i,
                            name="AMD ROCm Device",
                            total_memory_mb=0,
                            backend="rocm",
                        )
                    )
    except Exception as exc:
        _log.warning("rocm-smi probe failed: %s", exc)
    return devices


def probe_directml() -> List[AcceleratorDevice]:
    """Probe DirectML compatible devices on Windows."""
    devices: List[AcceleratorDevice] = []
    if platform.system().lower() != "windows":
        return devices

    shell = _preferred_shell()
    try:
        ps_cmd = [
            shell,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-CimInstance Win32_VideoController "
            "| Select-Object -Property Name, AdapterRAM "
            "| ConvertTo-Json -Compress",
        ]
        proc = subprocess.run(
            ps_cmd,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=20, check=False, env=pip_env(),
        )
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout.strip())
            if isinstance(data, dict):
                data = [data]
            for idx, item in enumerate(data):
                name = item.get("Name", "DirectX Video Controller")
                ram_bytes = item.get("AdapterRAM", 0) or 0
                ram_mb = int(ram_bytes / (1024 * 1024))
                devices.append(
                    AcceleratorDevice(
                        index=idx,
                        name=name,
                        total_memory_mb=ram_mb,
                        backend="directml",
                    )
                )
    except subprocess.TimeoutExpired:
        _log.debug("DirectML WMI probe timed out after 20s")
    except Exception as exc:
        _log.warning("DirectML WMI probe failed: %s", exc)
    return devices


# ─── MetaTrader 5 detection ─────────────────────────────────────────────────

def _read_exe_version(exe_path: Path) -> Optional[str]:
    """Extract FileVersion from a Windows PE executable.

    Uses PowerShell's VersionInfo property, which reads the same metadata
    the file's Properties dialog shows in Explorer. Returns None on any
    failure — this is best-effort.
    """
    if not exe_path.is_file():
        return None

    shell = _preferred_shell()
    ps_cmd = [
        shell,
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        f"(Get-Item -LiteralPath '{exe_path}').VersionInfo.FileVersion",
    ]
    try:
        proc = subprocess.run(
            ps_cmd,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, check=False, env=pip_env(),
        )
        if proc.returncode == 0:
            version = (proc.stdout or "").strip()
            if version:
                return version
    except Exception as exc:
        _log.debug("Version read failed for %s: %s", exe_path, exc)
    return None


def _mt5_registry_probe() -> Optional[MetaTrader5Info]:
    """Query Windows registry for MetaTrader 5. Fast (~5ms).

    MT5 registers under several possible keys depending on installer
    variant and 32/64-bit placement. Any of them implies an install.
    When a registered origin path is found, the version is read directly
    from terminal64.exe so callers get an authoritative version number.
    """
    if sys.platform != "win32":
        return None
    try:
        import winreg
    except ImportError:
        return None

    candidates = [
        (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\MetaQuotes\Terminal"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\MetaQuotes\Terminal"),
        (winreg.HKEY_CURRENT_USER,  r"SOFTWARE\WOW6432Node\MetaQuotes\Terminal"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\MetaQuotes\Terminal"),
    ]

    for hive, key_path in candidates:
        try:
            with winreg.OpenKey(hive, key_path) as key:
                subkey = winreg.EnumKey(key, 0)
                version: Optional[str] = None
                origin_path: Optional[str] = None

                try:
                    with winreg.OpenKey(hive, f"{key_path}\\{subkey}") as sub:
                        try:
                            origin, _ = winreg.QueryValueEx(sub, "Origin")
                            origin_path = str(origin)
                        except (FileNotFoundError, OSError) as exc:
                            _log.debug("No Origin key in MT5 subkey %s: %s", subkey, exc)
                        # Some MT5 builds register a "Version" value directly.
                        try:
                            ver_val, _ = winreg.QueryValueEx(sub, "Version")
                            if ver_val:
                                version = str(ver_val)
                        except (FileNotFoundError, OSError) as exc:
                            _log.debug("No Version key in MT5 subkey %s: %s", subkey, exc)
                except OSError as exc:
                    _log.debug("Could not open MT5 subkey %s: %s", subkey, exc)

                # Fall back to reading terminal64.exe's file version.
                if version is None and origin_path:
                    exe = Path(origin_path) / "terminal64.exe"
                    if exe.is_file():
                        version = _read_exe_version(exe)

                return MetaTrader5Info(
                    installed=True,
                    install_path=origin_path,
                    version=version,
                )
        except FileNotFoundError:
            continue
        except OSError as exc:
            _log.debug("MT5 registry probe: %s\\%s: %s", hive, key_path, exc)
            continue

    return None


def _mt5_known_paths_probe() -> Optional[MetaTrader5Info]:
    """Check well-known install locations for the MT5 terminal binary."""
    prog_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    prog_files_x86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local_appdata = os.environ.get("LOCALAPPDATA", "")

    candidates = [
        Path(prog_files) / "MetaTrader 5" / "terminal64.exe",
        Path(prog_files_x86) / "MetaTrader 5" / "terminal64.exe",
    ]
    if local_appdata:
        candidates.append(Path(local_appdata) / "Programs" / "MetaTrader 5" / "terminal64.exe")
        candidates.append(Path(local_appdata) / "MetaQuotes" / "Terminal" / "terminal64.exe")

    for path in candidates:
        if path.is_file():
            version = _read_exe_version(path)
            return MetaTrader5Info(
                installed=True,
                install_path=str(path),
                version=version,
            )

    return None


def _mt5_get_package_probe(timeout: int = 45) -> Optional[MetaTrader5Info]:
    """Fallback MT5 probe using PackageManagement's Get-Package."""
    shell = _preferred_shell()
    cmd = [
        shell,
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        "Get-Package -Name '*MetaTrader*' -ErrorAction SilentlyContinue "
        "| Select-Object -Property Name, Version "
        "| ConvertTo-Json -Compress",
    ]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, check=False, env=pip_env(),
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return None

        data = json.loads(proc.stdout.strip())
        if isinstance(data, dict):
            data = [data]
        if not data:
            return None

        entry = data[0]
        version = entry.get("Version") or None
        return MetaTrader5Info(installed=True, version=str(version) if version else None)
    except subprocess.TimeoutExpired:
        _log.debug("Get-Package MT5 probe timed out after %ss", timeout)
        return None
    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        _log.debug("Get-Package MT5 probe JSON parse failed: %s", exc)
        return None
    except Exception as exc:
        _log.debug("Get-Package MT5 probe failed: %s", exc)
        return None


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

    info = _mt5_registry_probe()
    if info is not None:
        _log.debug(
            "MT5 detected via registry: path=%s version=%s",
            info.install_path, info.version,
        )
        return info

    info = _mt5_known_paths_probe()
    if info is not None:
        _log.debug(
            "MT5 detected via known path: %s version=%s",
            info.install_path, info.version,
        )
        return info

    info = _mt5_get_package_probe()
    if info is not None:
        _log.debug("MT5 detected via Get-Package: version %s", info.version)
        return info

    return MetaTrader5Info(installed=False)


# ─── Full profile ───────────────────────────────────────────────────────────

def probe_hardware() -> HardwareProfile:
    """Execute comprehensive hardware discovery across all supported backends."""
    os_name = platform.system()
    os_version = platform.version()
    os_release = platform.release()
    architecture = platform.machine()
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    python_executable = sys.executable

    if psutil:
        cpu_logical = psutil.cpu_count(logical=True) or os.cpu_count() or 1
        cpu_physical = psutil.cpu_count(logical=False) or 1
        ram_mb = int(psutil.virtual_memory().total / (1024 * 1024))
    else:
        cpu_logical = os.cpu_count() or 1
        cpu_physical = cpu_logical
        ram_mb = 0

    accelerators: List[AcceleratorDevice] = []

    # Priority 1: CUDA
    cuda_devices = probe_nvidia_smi()
    if cuda_devices:
        accelerators.extend(cuda_devices)
        primary_backend = "cuda"
        torch_index = "https://download.pytorch.org/whl/cu121"
    else:
        # Priority 2: ROCm
        rocm_devices = probe_rocm_smi()
        if rocm_devices:
            accelerators.extend(rocm_devices)
            primary_backend = "rocm"
            torch_index = "https://download.pytorch.org/whl/rocm6.0"
        else:
            # Priority 3: DirectML (Windows)
            directml_devices = probe_directml()
            if directml_devices and os_name.lower() == "windows":
                accelerators.extend(directml_devices)
                primary_backend = "directml"
                torch_index = "https://download.pytorch.org/whl/cpu"
            else:
                primary_backend = "cpu"
                torch_index = "https://download.pytorch.org/whl/cpu"

    mt5_info = probe_metatrader5() if os_name.lower() == "windows" else None

    return HardwareProfile(
        os_name=os_name,
        os_version=os_version,
        os_release=os_release,
        architecture=architecture,
        python_version=python_version,
        python_executable=python_executable,
        cpu_count_logical=cpu_logical,
        cpu_count_physical=cpu_physical,
        total_ram_mb=ram_mb,
        primary_backend=primary_backend,
        accelerators=accelerators,
        recommended_torch_index=torch_index,
        metatrader5=mt5_info,
    )