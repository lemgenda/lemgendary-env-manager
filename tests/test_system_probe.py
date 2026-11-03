"""Unit tests for system_probe module."""

import unittest
from env_manager.system_probe import HardwareProfile, probe_hardware, select_cuda_torch_index


class TestSystemProbe(unittest.TestCase):
    """Test suite for system and hardware discovery."""

    def test_probe_hardware_returns_valid_profile(self):
        """Verify hardware profile structure and values."""
        profile = probe_hardware()
        self.assertIsInstance(profile, HardwareProfile)
        self.assertIn(profile.os_name, ["Windows", "Linux", "Darwin"])
        self.assertGreaterEqual(profile.cpu_count_logical, 1)
        self.assertIn(profile.primary_backend, ["cuda", "rocm", "directml", "cpu"])
        self.assertTrue(profile.recommended_torch_index.startswith("https://download.pytorch.org/whl/"))
        data = profile.to_dict()
        self.assertIn("os_name", data)
        self.assertIn("accelerators", data)



def test_select_cuda_torch_index_by_driver_major() -> None:
    """Each driver generation maps to its wheel index; unknown input defaults to cu126."""
    cases = {
        "565.90": "https://download.pytorch.org/whl/cu126",
        "550.12": "https://download.pytorch.org/whl/cu121",
        "470.1": "https://download.pytorch.org/whl/cu118",
        None: "https://download.pytorch.org/whl/cu126",
        "garbage": "https://download.pytorch.org/whl/cu126",
    }
    for driver, expected in cases.items():
        assert select_cuda_torch_index(driver) == expected, driver


if __name__ == "__main__":
    unittest.main()
