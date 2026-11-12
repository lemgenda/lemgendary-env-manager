"""Apple Silicon (Metal Performance Shaders) probe for macOS on arm64."""

import platform
import shutil
import subprocess

import psutil

from env_manager._logging import get_logger
from env_manager.probe.models import AcceleratorDevice
from env_manager.utils import run_command_simple

_log = get_logger(__name__)

APPLE_SILICON_MACHINES = frozenset({"arm64", "aarch64"})
"""``platform.machine()`` values of Apple Silicon Macs."""

_BYTES_PER_MB = 1024 * 1024


def _chip_name() -> str:
    """Return the chip brand (for example ``Apple M2``) or a generic label."""
    sysctl = shutil.which("sysctl")
    if sysctl:
        try:
            proc = run_command_simple([sysctl, "-n", "machdep.cpu.brand_string"], timeout=5)
        except (OSError, subprocess.SubprocessError) as exc:
            _log.warning("sysctl chip probe failed: %s", exc)
        else:
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
    return "Apple Silicon GPU"


def probe_apple_mps() -> list[AcceleratorDevice]:
    """Return the integrated Apple Silicon GPU, or [] on any other host.

    Apple Silicon has unified memory, so the GPU budget is the system RAM.
    """
    if platform.system() != "Darwin" or platform.machine().lower() not in APPLE_SILICON_MACHINES:
        return []
    return [AcceleratorDevice(
        index=0,
        name=_chip_name(),
        total_memory_mb=psutil.virtual_memory().total // _BYTES_PER_MB,
        backend="mps",
    )]
