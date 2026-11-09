"""Tests for sidecar service management and process termination."""

from pathlib import Path
from unittest.mock import patch

from env_manager.ecosystem import ecosystem
from env_manager.service_manager import (
    _parse_netstat_pids,
    _stop_via_pid_files,
    stop_service,
)


_PID_PRIMARY_A = 12345
_PID_PRIMARY_B = 67890
_PID_ESTABLISHED = 99999
_PID_ALT = 11111


def test_parse_netstat_pids_matches_various_formats() -> None:
    """Verify netstat output parsing on Windows captures LISTENING states and wildcards."""
    test_port = ecosystem().require_service("training-suite").port
    alt_port = ecosystem().require_service("dataset-compiler").port
    netstat_output = (
        f"  TCP    127.0.0.1:{test_port}         0.0.0.0:0              LISTENING       12345\n"
        f"  TCP    0.0.0.0:{test_port}           0.0.0.0:0              LISTENING       12345\n"
        f"  TCP    [::]:{test_port}              [::]:0                 LISTENING       67890\n"
        f"  TCP    127.0.0.1:{test_port}         *:*                    LISTENING       12345\n"
        f"  TCP    127.0.0.1:{test_port}         127.0.0.1:54321        ESTABLISHED     99999\n"
        f"  TCP    127.0.0.1:{alt_port}         0.0.0.0:0              LISTENING       11111\n"
    )
    pids_target = _parse_netstat_pids(netstat_output, test_port)
    assert _PID_PRIMARY_A in pids_target
    assert _PID_PRIMARY_B in pids_target
    assert _PID_ESTABLISHED not in pids_target
    assert _PID_ALT not in pids_target

    pids_alt = _parse_netstat_pids(netstat_output, alt_port)
    assert pids_alt == [_PID_ALT]


def test_stop_via_pid_files_terminates_and_unlinks(tmp_path: Path) -> None:
    """Verify PID file reader kills process tree and removes server.pid."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True)
    pid_file = log_dir / "server.pid"
    pid_file.write_text("43210", encoding="utf-8")

    with patch("env_manager.service_manager.psutil.pid_exists", return_value=True), \
         patch("env_manager.service_manager._kill_process_tree", return_value=True) as mock_kill:
        stopped = _stop_via_pid_files(log_dir, "test_service")
        assert stopped is True
        mock_kill.assert_called_once_with(43210)
        assert not pid_file.exists()


def test_stop_service_unknown_returns_error() -> None:
    """Stopping an unregistered service ID returns an error response."""
    result = stop_service("nonexistent-service")
    assert result["status"] == "error"
    assert "Unknown service" in result["message"]
