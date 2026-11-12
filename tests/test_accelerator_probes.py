"""Apple MPS, DirectML 64-bit VRAM and rocm-smi CSV probes (audit 4.1.3)."""

import json
import subprocess
from unittest.mock import patch

from env_manager.probe import accelerators, apple
from env_manager.probe.directml import directml_devices, is_virtual_adapter
from env_manager.probe.models import CPU_INDEX, PYPI_INDEX, ROCM_INDEX

GIB = 1024 ** 3
MIB = 1024 ** 2
EIGHT_GB_MB = 8 * 1024
WMI_32BIT_CAP = 4293918720


def test_directml_prefers_64bit_registry_vram() -> None:
    """An 8 GB GPU is no longer capped at the 32-bit AdapterRAM value."""
    payload = json.dumps([
        {"Name": "NVIDIA GeForce RTX 4070", "AdapterRAM": WMI_32BIT_CAP, "MemorySize": 8 * GIB},
        {"Name": "AMD Radeon 890M", "AdapterRAM": 512 * MIB, "MemorySize": None},
    ])
    devices = directml_devices(payload)
    assert [d.total_memory_mb for d in devices] == [EIGHT_GB_MB, 512]
    assert [d.index for d in devices] == [0, 1]


def test_directml_skips_virtual_adapters() -> None:
    """Basic Display, Hyper-V and remote adapters are not DirectML devices."""
    payload = json.dumps([
        {"Name": "Microsoft Basic Display Adapter", "AdapterRAM": 0},
        {"Name": "Microsoft Hyper-V Video", "AdapterRAM": 0},
        {"Name": "Microsoft Remote Display Adapter", "AdapterRAM": 0},
        {"Name": "Intel Arc A770", "AdapterRAM": WMI_32BIT_CAP, "MemorySize": 16 * GIB},
    ])
    devices = directml_devices(payload)
    assert [d.name for d in devices] == ["Intel Arc A770"]
    assert devices[0].index == 0


def test_directml_single_object_payload() -> None:
    """WMI returns a bare object, not a list, for one controller."""
    devices = directml_devices(json.dumps({"Name": "GPU", "AdapterRAM": 2 * GIB}))
    assert devices[0].total_memory_mb == 2 * 1024


def test_is_virtual_adapter_is_case_insensitive() -> None:
    """Is virtual adapter is case insensitive."""
    assert is_virtual_adapter("MICROSOFT BASIC RENDER DRIVER")
    assert not is_virtual_adapter("NVIDIA GeForce RTX 4090")


ROCM_CSV = (
    "\n"
    "============================ ROCm System Management Interface ============================\n"
    "device,Card series,Card model,Card vendor,Card SKU,"
    "VRAM Total Memory (B),VRAM Total Used Memory (B)\n"
    "card0,Navi 21 [Radeon RX 6800 XT],0x73bf,Advanced Micro Devices,D41205,"
    "17163091968,12345\n"
    "card1,Radeon Pro W7900,0x7448,Advanced Micro Devices,D707,51522830336,0\n"
    "================================== End of ROCm SMI Log ===================================\n"
)


def test_rocm_csv_reports_name_and_vram() -> None:
    """Rocm csv reports name and vram."""
    devices = accelerators.rocm_devices(ROCM_CSV)
    assert [(d.index, d.name) for d in devices] == [
        (0, "Navi 21 [Radeon RX 6800 XT]"), (1, "Radeon Pro W7900"),
    ]
    assert devices[0].total_memory_mb == 17163091968 // MIB
    assert all(d.backend == "rocm" for d in devices)


def test_rocm_csv_without_vram_column_reports_zero() -> None:
    """Rocm csv without vram column reports zero."""
    devices = accelerators.rocm_devices("device,Device Name\ncard0,MI300X\n")
    assert devices[0].name == "MI300X"
    assert devices[0].total_memory_mb == 0


def test_probe_rocm_smi_requests_csv() -> None:
    """Probe rocm smi requests csv."""
    completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=ROCM_CSV, stderr="")
    with patch.object(accelerators.shutil, "which", return_value="rocm-smi"), \
            patch.object(accelerators, "run_command_simple", return_value=completed) as run:
        devices = accelerators.probe_rocm_smi()
    assert "--csv" in run.call_args.args[0]
    assert [d.index for d in devices] == [0, 1]


def test_apple_silicon_is_detected_as_mps() -> None:
    """Apple silicon is detected as mps."""
    with patch.object(apple.platform, "system", return_value="Darwin"), \
            patch.object(apple.platform, "machine", return_value="arm64"), \
            patch.object(apple, "_chip_name", return_value="Apple M3"):
        devices = apple.probe_apple_mps()
    assert [(d.name, d.backend) for d in devices] == [("Apple M3", "mps")]
    assert devices[0].total_memory_mb > 0


def test_intel_mac_and_other_hosts_have_no_mps() -> None:
    """Intel mac and other hosts have no mps."""
    for system, machine in (("Darwin", "x86_64"), ("Linux", "aarch64"), ("Windows", "AMD64")):
        with patch.object(apple.platform, "system", return_value=system), \
                patch.object(apple.platform, "machine", return_value=machine):
            assert not apple.probe_apple_mps(), (system, machine)


def _no_gpu_probes():
    return (
        patch.object(accelerators, "probe_nvidia_smi", return_value=[]),
        patch.object(accelerators, "probe_rocm_smi", return_value=[]),
    )


def test_detect_accelerators_mps_uses_standard_pypi() -> None:
    """Detect accelerators mps uses standard pypi."""
    mps = apple.AcceleratorDevice(index=0, name="Apple M2", total_memory_mb=16384, backend="mps")
    nvidia, rocm = _no_gpu_probes()
    with nvidia, rocm, patch.object(accelerators, "probe_apple_mps", return_value=[mps]):
        devices, backend, index = accelerators.detect_accelerators("Darwin")
    assert (backend, index) == ("mps", PYPI_INDEX)
    assert devices == [mps]


def test_detect_accelerators_priority_and_fallback() -> None:
    """Detect accelerators priority and fallback."""
    nvidia, rocm = _no_gpu_probes()
    with nvidia, rocm, patch.object(accelerators, "probe_apple_mps", return_value=[]), \
            patch.object(accelerators, "probe_directml", return_value=[]):
        assert accelerators.detect_accelerators("Linux") == ([], "cpu", CPU_INDEX)
    amd = apple.AcceleratorDevice(index=0, name="AMD", total_memory_mb=1, backend="rocm")
    with patch.object(accelerators, "probe_nvidia_smi", return_value=[]), \
            patch.object(accelerators, "probe_rocm_smi", return_value=[amd]):
        assert accelerators.detect_accelerators("Linux")[1:] == ("rocm", ROCM_INDEX)
