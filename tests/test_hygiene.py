"""Contract and hygiene fixes (REVIEW_REPORT D-20)."""

import asyncio
import logging
import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import psutil
import pytest

from env_manager import server, service_manager, system_probe, venv_manager


def test_startup_sidecar_failure_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """A startup failure is logged under ``lem_env.server``, not lost."""
    async def run() -> None:
        with patch("env_manager.server.start_all_services", side_effect=RuntimeError("boom")):
            async with server.lifespan(server.app):
                await asyncio.sleep(0.2)

    with caplog.at_level(logging.ERROR, logger="lem_env.server"):
        asyncio.run(run())
    assert "Starting the ecosystem sidecars failed: boom" in caplog.text


def test_dead_event_queue_is_gone() -> None:
    """The never-fed event queue and its drain task were removed."""
    assert not hasattr(server, "_event_queue")
    assert not hasattr(server, "_drain_event_queue")


def test_stop_service_survives_access_denied_socket_listing() -> None:
    """``psutil.AccessDenied`` from ``net_connections`` (macOS) is handled."""
    with patch("env_manager.service_manager.is_port_in_use", return_value=True), \
            patch("env_manager.service_manager.psutil.net_connections",
                  side_effect=psutil.AccessDenied()):
        result = service_manager.stop_service("dataset-compiler")
    assert result["status"] == "not_running"


def test_powershell_literal_escapes_single_quotes() -> None:
    """A path with ``'`` cannot break out of the PowerShell string literal."""
    assert system_probe.ps_quote("dir/a'b") == "'dir/a''b'"


def test_mt5_known_paths_have_no_hardcoded_fallback() -> None:
    """With no ``ProgramFiles`` variables set, no drive-letter path is probed."""
    probed: list[str] = []

    def is_file(path: Path) -> bool:
        probed.append(str(path))
        return False

    with patch.dict(os.environ, {}, clear=True), patch.object(Path, "is_file", is_file):
        assert system_probe.mt5_known_paths_probe() is None
    assert not probed


def test_discover_projects_runs_each_interpreter_once(tmp_path: Path) -> None:
    """Discovery checks validity and version with one ``python --version`` per project."""
    project = tmp_path / "lemgendary-datasets"
    python = venv_manager.get_venv_python_path(project)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"")
    (project / ".venv" / "pyvenv.cfg").write_text("home = x\n", encoding="utf-8")
    version = subprocess.CompletedProcess(args=[], returncode=0, stdout="Python 3.12.10\n",
                                          stderr="")
    pip_list = subprocess.CompletedProcess(args=[], returncode=0, stdout="[]", stderr="")
    with patch("env_manager.venv_manager.run_command_simple", return_value=version) as run, \
            patch("env_manager.venv_manager.run_pip_with_recovery", return_value=pip_list):
        found = venv_manager.discover_projects(tmp_path)
    info = next(p for p in found if p.name == "lemgendary-datasets")
    assert info.is_valid
    assert info.python_version == "Python 3.12.10"
    assert run.call_count == 1
