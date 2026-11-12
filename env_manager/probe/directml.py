"""DirectML adapter probe for Windows: WMI names plus 64-bit registry VRAM.

``Win32_VideoController.AdapterRAM`` is a 32-bit field, so any GPU with more
than 4 GB reports about 4095 MB. The display class registry keys hold the
64-bit ``HardwareInformation.qwMemorySize``, which the query reads per
adapter and returns as ``MemorySize`` next to ``AdapterRAM``.
"""

import json
import platform
import subprocess

from env_manager._logging import get_logger
from env_manager.probe.models import AcceleratorDevice
from env_manager.probe.shell import run_powershell

_log = get_logger(__name__)

DISPLAY_CLASS_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
"""Registry key (under HKLM) of the display adapter device class."""

DIRECTML_QUERY = "; ".join((
    f"$classKey = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey('{DISPLAY_CLASS_KEY}')",
    "$vram = @{}",
    "foreach ($name in $classKey.GetSubKeyNames()) {"
    " if ($name -notmatch '^[0-9]{4}$') { continue };"
    " $key = $classKey.OpenSubKey($name);"
    " $desc = $key.GetValue('DriverDesc');"
    " $size = $key.GetValue('HardwareInformation.qwMemorySize');"
    " if ($null -eq $size) { $size = $key.GetValue('HardwareInformation.MemorySize') };"
    " if ($size -is [byte[]]) {"
    " $padded = [byte[]]::new(8);"
    " [Array]::Copy($size, $padded, [Math]::Min($size.Length, 8));"
    " $size = [BitConverter]::ToUInt64($padded, 0) };"
    " if ($desc -and $size) { $vram[[string]$desc] = [uint64]$size } }",
    "Get-CimInstance Win32_VideoController | ForEach-Object {"
    " [pscustomobject]@{ Name = $_.Name; AdapterRAM = $_.AdapterRAM;"
    " MemorySize = $vram[[string]$_.Name] } } | ConvertTo-Json -Compress",
))
"""PowerShell query listing video controllers with their 64-bit VRAM size."""

VIRTUAL_ADAPTER_MARKERS = (
    "microsoft basic display",
    "microsoft basic render",
    "microsoft hyper-v video",
    "remote display adapter",
    "virtual display",
)
"""Lower-case name fragments of software or virtual adapters DirectML cannot use."""

_BYTES_PER_MB = 1024 * 1024


def is_virtual_adapter(name: str) -> bool:
    """Return True for software, remote-desktop and hypervisor display adapters."""
    lowered = name.lower()
    return any(marker in lowered for marker in VIRTUAL_ADAPTER_MARKERS)


def _memory_bytes(item: dict) -> int:
    """Prefer the 64-bit registry size; fall back to the 32-bit WMI field."""
    registry_size = int(item.get("MemorySize") or 0)
    if registry_size > 0:
        return registry_size
    return int(item.get("AdapterRAM") or 0)


def directml_devices(stdout: str) -> list[AcceleratorDevice]:
    """Parse the query JSON (one object or a list of them) into physical adapters.

    Raises ``ValueError`` for invalid JSON or a non-numeric memory size and
    ``TypeError`` for a memory field of the wrong type.
    """
    data = json.loads(stdout)
    if isinstance(data, dict):
        data = [data]
    devices: list[AcceleratorDevice] = []
    for item in data:
        if not isinstance(item, dict):
            _log.warning("Unexpected DirectML WMI entry: %r", item)
            continue
        name = str(item.get("Name") or "DirectX Video Controller")
        if is_virtual_adapter(name):
            _log.debug("Skipping virtual display adapter %r", name)
            continue
        devices.append(AcceleratorDevice(
            index=len(devices),
            name=name,
            total_memory_mb=_memory_bytes(item) // _BYTES_PER_MB,
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
            return directml_devices(proc.stdout.strip())
    except subprocess.TimeoutExpired:
        _log.warning("DirectML WMI probe timed out after 20s")
    except (OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
        # ValueError covers invalid JSON; TypeError a non-numeric memory field.
        _log.warning("DirectML WMI probe failed: %s", exc)
    return []
