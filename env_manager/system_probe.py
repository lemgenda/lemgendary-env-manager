"""System and hardware probing module for LemGendary Environment Manager.

Detects operating system, CPU, memory, and accelerator backends:
- NVIDIA CUDA
- AMD ROCm
- DirectML (Windows)
- CPU Fallback

Also probes MetaTrader 5 terminal installation on Windows.
"""

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from env_manager._logging import get_logger

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
        """Convert profile to dictionary."""
        return asdict(self)


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
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
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
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
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

    try:
        ps_cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_VideoController | Select-Object -Property Name, AdapterRAM | ConvertTo-Json",
        ]
        proc = subprocess.run(
            ps_cmd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            import json
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
    except Exception as exc:
        _log.warning("DirectML WMI probe failed: %s", exc)
    return devices



def probe_metatrader5() -> MetaTrader5Info:
    """Probe MetaTrader 5 installation on Windows via Get-Package and registry fallback.

    Detection order:
    1. PowerShell Get-Package -Name '*MetaTrader*' (user-preferred)
    2. Well-known installation paths (Program Files, AppData)
    3. Registry key HKLM:\\SOFTWARE\\MetaQuotes Software Ltd\\MetaTrader 5
    """
    if platform.system().lower() != "windows":
        return MetaTrader5Info(installed=False)

    # Primary: PowerShell Get-Package (preferred)
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
                    version = entry.get("Version") or "Unknown"
                    return MetaTrader5Info(installed=True, version=str(version))
            except (json.JSONDecodeError, KeyError, IndexError) as exc:
                _log.debug("Get-Package JSON parse failed: %s", exc)
    except Exception as exc:
        _log.warning("Get-Package probe failed: %s", exc)



    # Fallback 1: well-known installation paths
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
            return MetaTrader5Info(installed=True, install_path=path)

    # Fallback 2: registry key
    try:
        ps_reg_cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "$key = Get-ItemProperty "
                "-Path 'HKLM:\\SOFTWARE\\MetaQuotes Software Ltd\\MetaTrader 5' "
                "-ErrorAction SilentlyContinue; "
                "if ($key) { $key | ConvertTo-Json } else { 'null' }"
            ),
        ]
        proc = subprocess.run(ps_reg_cmd, capture_output=True, text=True, timeout=5, check=False)
        if proc.returncode == 0 and proc.stdout.strip() not in ("null", ""):
            import json
            try:
                data = json.loads(proc.stdout.strip())
                if data and isinstance(data, dict):
                    install_path = data.get("InstallDir") or data.get("Path")
                    return MetaTrader5Info(installed=True, install_path=str(install_path) if install_path else None)
            except (json.JSONDecodeError, KeyError) as exc:
                _log.debug("MT5 registry JSON parse failed: %s", exc)
    except Exception as exc:
        _log.warning("MT5 registry probe failed: %s", exc)

    return MetaTrader5Info(installed=False)



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
