"""
LemGendary Environment Manager — Typer CLI Unit Tests.

Phase 2 of Ecosystem Comprehensive Testing Battery.
Tests CLI dispatching (probe, sync, validate, clean) using Typer's CliRunner.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from typer.testing import CliRunner

from env_manager import requirements_manager
from env_manager.cli import app


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
        # Hermetic: sync runs against an empty temporary workspace so the
        # test can never rewrite the real sibling repositories (D-06).
        real_sync = requirements_manager.sync_all_manifests
        with tempfile.TemporaryDirectory() as tmp, \
                patch("env_manager.cli.sync_all_manifests", lambda: real_sync(Path(tmp))):
            result = self.runner.invoke(app, ["sync"])
        self.assertEqual(result.exit_code, 0, f"sync command failed: {result.output}")
        self.assertIn("Synchronizing requirements manifests", result.output)

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
