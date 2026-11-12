"""Hardware profile records and the CUDA driver-to-wheel-index table."""

from dataclasses import asdict, dataclass
from typing import Any

from env_manager._logging import get_logger

_log = get_logger(__name__)

NVIDIA_SMI_FIELDS = ("index", "name", "memory.total", "driver_version")
"""Columns requested from ``nvidia-smi --query-gpu``, in output order."""

CUDA_INDEX_BY_MIN_DRIVER = (
    (610, "https://download.pytorch.org/whl/cu134"),
    (560, "https://download.pytorch.org/whl/cu126"),
    (535, "https://download.pytorch.org/whl/cu121"),
)
"""PyTorch wheel index for each minimum NVIDIA driver major version, newest first."""

CUDA_FALLBACK_INDEX = "https://download.pytorch.org/whl/cu118"
"""Wheel index for drivers older than every entry in ``CUDA_INDEX_BY_MIN_DRIVER``."""

CUDA_DEFAULT_INDEX = "https://download.pytorch.org/whl/cu126"
"""Wheel index used when the NVIDIA driver version is unknown or unparsable."""

ROCM_INDEX = "https://download.pytorch.org/whl/rocm6.0"
"""PyTorch wheel index for AMD ROCm hosts."""

CPU_INDEX = "https://download.pytorch.org/whl/cpu"
"""PyTorch wheel index for DirectML and CPU-only hosts."""

PYPI_INDEX = "https://pypi.org/simple"
"""Standard PyPI index; its macOS arm64 torch wheels include the MPS backend."""


@dataclass
class AcceleratorDevice:
    """Represents an accelerated computing device (GPU/NPU)."""
    index: int
    name: str
    total_memory_mb: int
    backend: str
    driver_version: str | None = None
    compute_capability: str | None = None


@dataclass
class MetaTrader5Info:
    """MetaTrader 5 terminal installation details."""
    installed: bool
    version: str | None = None
    install_path: str | None = None
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
    accelerators: list[AcceleratorDevice]
    recommended_torch_index: str
    metatrader5: MetaTrader5Info | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a plain dict for JSON output."""
        return asdict(self)


def select_cuda_torch_index(driver_version: str | None) -> str:
    """Select modern PyTorch wheel index matching host NVIDIA driver."""
    if not driver_version:
        return CUDA_DEFAULT_INDEX
    try:
        major = int(driver_version.split(".")[0])
    except ValueError as exc:
        _log.warning("Unparsable NVIDIA driver version %r, defaulting to cu126: %s",
                     driver_version, exc)
        return CUDA_DEFAULT_INDEX
    for min_driver, index_url in CUDA_INDEX_BY_MIN_DRIVER:
        if major >= min_driver:
            return index_url
    return CUDA_FALLBACK_INDEX
