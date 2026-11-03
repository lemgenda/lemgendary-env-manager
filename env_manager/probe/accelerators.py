"""Accelerator probes (NVIDIA CUDA, AMD ROCm, DirectML) and backend selection."""

import json
import platform
import shutil
import subprocess

from env_manager._logging import get_logger
from env_manager.probe.models import (
    CPU_INDEX,
    NVIDIA_SMI_FIELDS,
    ROCM_INDEX,
    AcceleratorDevice,
    select_cuda_torch_index,
)
from env_manager.probe.shell import run_powershell
from env_manager.utils import run_command_simple

_log = get_logger(__name__)

DIRECTML_QUERY = (
    "Get-CimInstance Win32_VideoController "
    "| Select-Object -Property Name, AdapterRAM "
    "| ConvertTo-Json -Compress"
)
"""PowerShell query listing the video controllers DirectML can use."""


def _nvidia_device(line: str) -> AcceleratorDevice | None:
    """Parse one ``nvidia-smi`` CSV line; None (logged) when it is unparsable."""
    parts = [p.strip() for p in line.split(",")]
    if len(parts) < len(NVIDIA_SMI_FIELDS):
        return None
    try:
        return AcceleratorDevice(
            index=int(parts[0]),
            name=parts[1],
            total_memory_mb=int(parts[2]),
            backend="cuda",
            driver_version=parts[3],
        )
    except ValueError as exc:
        _log.warning("Unparsable nvidia-smi line %r: %s", line, exc)
        return None


def probe_nvidia_smi() -> list[AcceleratorDevice]:
    """Probe NVIDIA GPUs using nvidia-smi if available."""
    nvidia_smi_path = shutil.which("nvidia-smi")
    if not nvidia_smi_path:
        return []
    query_cmd = [
        nvidia_smi_path,
        "--query-gpu=" + ",".join(NVIDIA_SMI_FIELDS),
        "--format=csv,noheader,nounits",
    ]
    try:
        proc = run_command_simple(query_cmd, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("nvidia-smi probe failed: %s", exc)
        return []
    if proc.returncode != 0:
        return []
    devices = (_nvidia_device(line) for line in proc.stdout.strip().splitlines())
    return [device for device in devices if device is not None]


def probe_rocm_smi() -> list[AcceleratorDevice]:
    """Probe AMD GPUs using rocm-smi if available."""
    rocm_smi_path = shutil.which("rocm-smi")
    if not rocm_smi_path:
        return []
    query_cmd = [rocm_smi_path, "--showid", "--showproductname", "--showmeminfo", "vram"]
    try:
        proc = run_command_simple(query_cmd, timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("rocm-smi probe failed: %s", exc)
        return []
    if proc.returncode != 0:
        return []
    return [
        AcceleratorDevice(index=i, name="AMD ROCm Device", total_memory_mb=0, backend="rocm")
        for i, line in enumerate(proc.stdout.strip().splitlines())
        if "GPU[" in line
    ]


def _directml_devices(stdout: str) -> list[AcceleratorDevice]:
    """Parse the WMI video controller JSON (one object or a list of them).

    Raises ``ValueError`` for invalid JSON and ``TypeError`` for a
    non-numeric ``AdapterRAM``.
    """
    data = json.loads(stdout)
    if isinstance(data, dict):
        data = [data]
    devices: list[AcceleratorDevice] = []
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            _log.warning("Unexpected DirectML WMI entry: %r", item)
            continue
        ram_bytes = item.get("AdapterRAM", 0) or 0
        devices.append(AcceleratorDevice(
            index=idx,
            name=item.get("Name", "DirectX Video Controller"),
            total_memory_mb=int(ram_bytes / (1024 * 1024)),
            backend="directml",
        ))
    return devices


def probe_directml() -> list[AcceleratorDevice]:
    """Probe DirectML compatible devices on Windows."""
    if platform.system().lower() != "windows":
        return []
    try:
        proc = run_powershell(DIRECTML_QUERY, timeout=20)
        if proc.returncode == 0 and proc.stdout.strip():
            return _directml_devices(proc.stdout.strip())
    except subprocess.TimeoutExpired:
        _log.warning("DirectML WMI probe timed out after 20s")
    except (OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
        # ValueError covers invalid JSON; TypeError a non-numeric AdapterRAM.
        _log.warning("DirectML WMI probe failed: %s", exc)
    return []


def detect_accelerators(os_name: str) -> tuple[list[AcceleratorDevice], str, str]:
    """Return ``(devices, primary_backend, torch_index)`` by backend priority.

    CUDA wins over ROCm, which wins over DirectML (Windows only); with
    none of them the host is CPU-only.
    """
    cuda_devices = probe_nvidia_smi()
    if cuda_devices:
        return cuda_devices, "cuda", select_cuda_torch_index(cuda_devices[0].driver_version)
    rocm_devices = probe_rocm_smi()
    if rocm_devices:
        return rocm_devices, "rocm", ROCM_INDEX
    directml_devices = probe_directml()
    if directml_devices and os_name.lower() == "windows":
        return directml_devices, "directml", CPU_INDEX
    return [], "cpu", CPU_INDEX
