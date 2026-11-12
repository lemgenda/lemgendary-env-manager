"""Accelerator probes (NVIDIA CUDA, AMD ROCm) and backend selection."""

import csv
import shutil
import subprocess

from env_manager._logging import get_logger
from env_manager.probe.models import (
    CPU_INDEX,
    NVIDIA_SMI_FIELDS,
    PYPI_INDEX,
    ROCM_INDEX,
    AcceleratorDevice,
    select_cuda_torch_index,
)
from env_manager.probe.apple import probe_apple_mps
from env_manager.probe.directml import probe_directml
from env_manager.utils import run_command_simple

_log = get_logger(__name__)

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


ROCM_SMI_ARGS = ("--showproductname", "--showmeminfo", "vram", "--csv")
"""``rocm-smi`` arguments that print one CSV row per GPU."""

ROCM_NAME_COLUMNS = ("card series", "device name", "card model")
"""Lower-case ``rocm-smi`` CSV columns that carry the product name, best first."""

ROCM_VRAM_COLUMN = "vram total memory"
"""Lower-case prefix of the ``rocm-smi`` CSV column holding total VRAM in bytes."""

_BYTES_PER_MB = 1024 * 1024


def _rocm_vram_mb(row: dict[str, str]) -> int:
    """Total VRAM in MB from a ``rocm-smi`` CSV row; 0 when absent or unparsable."""
    for column, value in row.items():
        if column.startswith(ROCM_VRAM_COLUMN):
            try:
                return int(value) // _BYTES_PER_MB
            except ValueError as exc:
                _log.warning("Unparsable rocm-smi VRAM %r: %s", value, exc)
    return 0


def rocm_devices(stdout: str) -> list[AcceleratorDevice]:
    """Parse ``rocm-smi --csv`` output into devices with name and VRAM."""
    lines = [line for line in stdout.splitlines() if "," in line]
    devices: list[AcceleratorDevice] = []
    for raw in csv.DictReader(lines):
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        card = row.get("device", "")
        if not card.lower().startswith("card"):
            continue
        name = next((row[c] for c in ROCM_NAME_COLUMNS if row.get(c)), "AMD ROCm Device")
        digits = "".join(ch for ch in card if ch.isdigit())
        devices.append(AcceleratorDevice(
            index=int(digits) if digits else len(devices),
            name=name,
            total_memory_mb=_rocm_vram_mb(row),
            backend="rocm",
        ))
    return devices


def probe_rocm_smi() -> list[AcceleratorDevice]:
    """Probe AMD GPUs using rocm-smi if available."""
    rocm_smi_path = shutil.which("rocm-smi")
    if not rocm_smi_path:
        return []
    try:
        proc = run_command_simple([rocm_smi_path, *ROCM_SMI_ARGS], timeout=5)
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("rocm-smi probe failed: %s", exc)
        return []
    if proc.returncode != 0:
        return []
    return rocm_devices(proc.stdout)


def detect_accelerators(os_name: str) -> tuple[list[AcceleratorDevice], str, str]:
    """Return ``(devices, primary_backend, torch_index)`` by backend priority.

    CUDA wins over ROCm, then Apple Silicon MPS (standard PyPI wheels),
    then DirectML (Windows only); with none of them the host is CPU-only.
    """
    cuda_devices = probe_nvidia_smi()
    if cuda_devices:
        return cuda_devices, "cuda", select_cuda_torch_index(cuda_devices[0].driver_version)
    rocm_devices = probe_rocm_smi()
    if rocm_devices:
        return rocm_devices, "rocm", ROCM_INDEX
    mps_devices = probe_apple_mps()
    if mps_devices:
        return mps_devices, "mps", PYPI_INDEX
    directml_devices = probe_directml()
    if directml_devices and os_name.lower() == "windows":
        return directml_devices, "directml", CPU_INDEX
    return [], "cpu", CPU_INDEX
