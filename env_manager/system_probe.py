"""System and hardware probing module for LemGendary Environment Manager.

Detects operating system, CPU, memory, and accelerator backends:
- NVIDIA CUDA
- AMD ROCm
- Apple Silicon MPS (macOS arm64)
- DirectML (Windows)
- CPU Fallback

Also probes MetaTrader 5 terminal installation on Windows. The probes live
in the ``env_manager.probe`` package (``models``, ``shell``,
``accelerators``, ``apple``, ``directml``, ``metatrader``); this module
assembles the full :class:`HardwareProfile` and re-exports the public names.
"""

import os
import platform
import sys

import psutil

from env_manager.probe.accelerators import (
    detect_accelerators,
    probe_nvidia_smi,
    probe_rocm_smi,
)
from env_manager.probe.apple import probe_apple_mps
from env_manager.probe.directml import probe_directml
from env_manager.probe.metatrader import mt5_known_paths_probe, probe_metatrader5
from env_manager.probe.models import (
    CUDA_FALLBACK_INDEX,
    CUDA_INDEX_BY_MIN_DRIVER,
    NVIDIA_SMI_FIELDS,
    AcceleratorDevice,
    HardwareProfile,
    MetaTrader5Info,
    select_cuda_torch_index,
)
from env_manager.probe.shell import ps_quote

__all__ = [
    "CUDA_FALLBACK_INDEX",
    "CUDA_INDEX_BY_MIN_DRIVER",
    "NVIDIA_SMI_FIELDS",
    "AcceleratorDevice",
    "HardwareProfile",
    "MetaTrader5Info",
    "mt5_known_paths_probe",
    "probe_apple_mps",
    "probe_directml",
    "probe_hardware",
    "probe_metatrader5",
    "probe_nvidia_smi",
    "probe_rocm_smi",
    "ps_quote",
    "select_cuda_torch_index",
]


def probe_hardware() -> HardwareProfile:
    """Execute comprehensive hardware discovery across all supported backends."""
    os_name = platform.system()
    accelerators, primary_backend, torch_index = detect_accelerators(os_name)
    return HardwareProfile(
        os_name=os_name,
        os_version=platform.version(),
        os_release=platform.release(),
        architecture=platform.machine(),
        python_version=(
            f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        ),
        python_executable=sys.executable,
        cpu_count_logical=psutil.cpu_count(logical=True) or os.cpu_count() or 1,
        cpu_count_physical=psutil.cpu_count(logical=False) or 1,
        total_ram_mb=int(psutil.virtual_memory().total / (1024 * 1024)),
        primary_backend=primary_backend,
        accelerators=accelerators,
        recommended_torch_index=torch_index,
        metatrader5=probe_metatrader5() if os_name.lower() == "windows" else None,
    )
