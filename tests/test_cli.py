"""
LemGendary Environment Manager — Typer CLI Unit Tests.

Phase 2 of Ecosystem Comprehensive Testing Battery.
Tests CLI dispatching (probe, sync, validate, clean) using Typer's CliRunner.
"""

import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from env_manager.cli import app
from env_manager.workspace import workspace_root


class TestCliCommands(unittest.TestCase):
    """Test suite for env-manager Typer CLI commands."""

    def setUp(self) -> None:
        self.runner = CliRunner()

    def test_cli_probe_command(self) -> None:
        with patch("env_manager.bootstrap.check_winget_updates", return_value=[]):
            result = self.runner.invoke(app, ["probe"])
            self.assertEqual(result.exit_code, 0, f"probe command failed: {result.output}")
            self.assertIn("System Hardware & Accelerator Profile", result.output)
            self.assertIn("Operating System", result.output)

    def test_cli_sync_command(self) -> None:
        # The conftest fixture makes a fake workspace the root (D-06).
        result = self.runner.invoke(app, ["sync"])
        self.assertEqual(result.exit_code, 0, f"sync command failed: {result.output}")
        self.assertIn("Synchronizing requirements manifests", result.output)
        synced = workspace_root() / "lemgendary-docs" / "requirements.txt"
        self.assertEqual(synced.read_text(encoding="utf-8"), "rich>=13.0.0\n")

    def test_cli_clean_command(self) -> None:
        result = self.runner.invoke(app, ["clean"])
        self.assertEqual(result.exit_code, 0, f"clean command failed: {result.output}")
        self.assertIn("Reclaimed", result.output)

    def test_cli_validate_command_single_project(self) -> None:
        result = self.runner.invoke(app, ["validate", "--project", "lemgendary-docs"])
        self.assertEqual(result.exit_code, 0, f"validate command failed: {result.output}")
        self.assertIn("lemgendary-docs", result.output)
        self.assertIn("[PASS]", result.output)


if __name__ == "__main__":
    unittest.main()
