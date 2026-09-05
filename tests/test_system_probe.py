"""Unit tests for system_probe module."""

import unittest
from env_manager.system_probe import HardwareProfile, probe_hardware


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


if __name__ == "__main__":
    unittest.main()
