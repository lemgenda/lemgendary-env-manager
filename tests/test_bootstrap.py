"""Unit tests for bootstrap module."""

import unittest
from env_manager.bootstrap import BootstrapStatus, verify_prerequisites


class TestBootstrap(unittest.TestCase):
    """Test suite for bootstrap prerequisites."""

    def test_verify_prerequisites_returns_valid_status(self):
        """Verify bootstrap prerequisites structure."""
        status = verify_prerequisites()
        self.assertIsInstance(status, BootstrapStatus)
        self.assertTrue(status.python_valid)
        self.assertIsNotNone(status.python_version)
        data = status.to_dict()
        self.assertIn("python_valid", data)
        self.assertIn("remediation_instructions", data)


if __name__ == "__main__":
    unittest.main()
