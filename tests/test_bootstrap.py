"""Unit tests for bootstrap module."""

import unittest
from unittest.mock import patch
from env_manager.bootstrap import BootstrapStatus, verify_prerequisites


class TestBootstrap(unittest.TestCase):
    """Test suite for bootstrap prerequisites."""

    @patch("env_manager.bootstrap.check_winget_updates", return_value=[])
    def test_verify_prerequisites_returns_valid_status(self, mock_winget):
        """Verify bootstrap prerequisites structure with mocked network-bound update check."""
        status = verify_prerequisites()
        self.assertIsInstance(status, BootstrapStatus)
        self.assertTrue(status.python_valid)
        self.assertIsNotNone(status.python_version)
        data = status.to_dict()
        self.assertIn("python_valid", data)
        self.assertIn("remediation_instructions", data)


if __name__ == "__main__":
    unittest.main()
