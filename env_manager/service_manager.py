"""Ecosystem background sidecar service lifecycle management module.

Orchestrates starting, stopping, and health checking the tripartite
services listed in ``ecosystem/ecosystem.json`` (Environment Manager,
Dataset Compiler Suite and Model Training Suite).
"""

from __future__ import annotations

import http.client
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from pathlib import Path
from typing import Any

import psutil

from env_manager._logging import get_logger
from env_manager.ecosystem import SELF_SERVICE_ID, Service, ecosystem
from env_manager.utils import run_command_simple
from env_manager.venv_manager import get_venv_python_path
from env_manager.workspace import workspace_root

_log = get_logger(__name__)

STARTUP_TIMEOUT_S = 20.0
"""Seconds a sidecar may take to report healthy; PyTorch/CUDA suites need most of it."""

def _name(svc: Service) -> str:
    """Canonical display name of the project that hosts ``svc``."""
    return ecosystem().display_name(svc.project)


def _health_url(svc: Service) -> str:
    return ecosystem().service_url(svc, svc.health_path)


def _project_dir(svc: Service) -> Path:
    """Return the service's project folder inside the current workspace."""
    return workspace_root() / svc.project


def is_port_in_use(port: int, host: str | None = None) -> bool:
    """Check if TCP port is actively accepting connections."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host or ecosystem().identity.bind_host, port)) == 0


def is_service_healthy(health_url: str, timeout: float = 1.0) -> bool:
    """Probe service HTTP health endpoint for 200 OK status."""
    try:
        req = urllib.request.Request(
            health_url,
            headers={"User-Agent": "LemGendary-EnvManager/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status == HTTPStatus.OK
    except (OSError, http.client.HTTPException) as exc:
        # URLError and socket timeouts are OSError subclasses. An offline
        # service is an expected state, so this is debug-level.
        _log.debug("Health probe %s failed: %s", health_url, exc)
        return False


def get_background_python_path(project_dir: Path) -> Path:
    """Return pythonw.exe on Windows for windowless background execution, or python on POSIX."""
    venv_dir = project_dir / ".venv"
    if sys.platform == "win32":
        pythonw = venv_dir / "Scripts" / "pythonw.exe"
        if pythonw.is_file():
            return pythonw
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _preflight_service(svc: Service) -> dict[str, Any] | None:
    """Return a response when the service must not be launched, else None."""
    port = svc.port
    name = _name(svc)
    proj_dir: Path = _project_dir(svc)
    if svc.id == SELF_SERVICE_ID:
        return {
            "status": "already_running",
            "message": f"{name} is the orchestrator and is currently running on port {port}.",
            "port": port,
        }
    if is_service_healthy(_health_url(svc)):
        return {
            "status": "already_running",
            "message": f"{name} is already online and healthy on port {port}.",
            "port": port,
        }
    if is_port_in_use(port):
        return {
            "status": "starting",
            "message": f"{name} is currently binding/starting on port {port}.",
            "port": port,
        }
    if not proj_dir.exists():
        return {
            "status": "error",
            "message": f"Project directory does not exist: {proj_dir}",
        }
    return None


def _resolve_service_python(proj_dir: Path) -> Path:
    """Prefer the windowless interpreter; fall back to the venv's python."""
    python_exe = get_background_python_path(proj_dir)
    if not python_exe.exists():
        python_exe = get_venv_python_path(proj_dir)
    return python_exe


def _read_log_tail(path: Path, limit: int = 500) -> str:
    """Return the last ``limit`` characters of a log file, or a reason it is unreadable."""
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]
    except OSError as exc:
        _log.warning("Cannot read %s: %s", path, exc)
        return f"(log unreadable: {exc})"


def _await_service_health(
    svc: Service, proc: subprocess.Popen, stderr_path: Path,
) -> dict[str, Any]:
    """Poll the health endpoint for ``STARTUP_TIMEOUT_S``; report started, exited or starting.

    The window lets PyTorch/CUDA model suites initialise.
    """
    port = svc.port
    name = _name(svc)
    start_time = time.time()
    while time.time() - start_time < STARTUP_TIMEOUT_S:
        time.sleep(0.5)
        if is_service_healthy(_health_url(svc)):
            return {
                "status": "started",
                "message": f"{name} started successfully on port {port}.",
                "port": port,
                "pid": proc.pid,
            }
        if proc.poll() is not None:
            err_text = _read_log_tail(stderr_path)
            return {
                "status": "error",
                "message": (
                    f"{name} process exited with code {proc.returncode}. "
                    f"Logs: {err_text.strip() or 'No error output'}"
                ),
                "port": port,
            }
    return {
        "status": "starting",
        "message": (
            f"{name} daemon launched (PID {proc.pid}), "
            f"waiting for health check on port {port}..."
        ),
        "port": port,
        "pid": proc.pid,
    }


def _spawn_detached(cmd: list[str], popen_kwargs: dict[str, Any]) -> subprocess.Popen:
    """Start a sidecar daemon and hand its process handle to the caller.

    The child must outlive this call (it is a long-running service), so the
    handle is returned instead of being scoped to a ``with`` block, which
    would wait for the daemon to exit.
    """
    return subprocess.Popen(cmd, **popen_kwargs)


def _save_pid_file(log_dir: Path, pid: int) -> None:
    """Record daemon PID so stop_service can terminate the process tree."""
    pid_file = log_dir / "server.pid"
    try:
        pid_file.write_text(str(pid), encoding="utf-8")
    except OSError as p_err:
        _log.warning("Could not write PID file %s: %s", pid_file, p_err)


def _launch_service(svc: Service, python_exe: Path) -> dict[str, Any]:
    """Spawn the service detached from any console and wait for it to come up."""
    port = svc.port
    name = _name(svc)
    proj_dir: Path = _project_dir(svc)

    log_dir = proj_dir / svc.log_dir
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _log.warning("Could not create server log folder %s: %s", log_dir, exc)

    stdout_path = log_dir / "service_stdout.log"
    stderr_path = log_dir / "service_stderr.log"

    cmd: list[str] = [
        str(python_exe),
        "-m",
        "uvicorn",
        svc.app_import,
        "--host",
        ecosystem().identity.bind_host,
        "--port",
        str(port),
    ]

    try:
        with open(stdout_path, "a", encoding="utf-8") as out_f, open(
            stderr_path, "a", encoding="utf-8"
        ) as err_f:
            popen_kwargs: dict[str, Any] = {
                "cwd": str(proj_dir),
                "stdin": subprocess.DEVNULL,
                "stdout": out_f,
                "stderr": err_f,
                "close_fds": True,
            }

            if sys.platform == "win32":
                # Windows: CREATE_NO_WINDOW ensures no console window opens
                popen_kwargs["creationflags"] = getattr(
                    subprocess, "CREATE_NO_WINDOW", 0x08000000
                )
            else:
                # POSIX (Linux & macOS): detach process into its own session without any terminal
                popen_kwargs["start_new_session"] = True

            proc = _spawn_detached(cmd, popen_kwargs)
            _save_pid_file(log_dir, proc.pid)

        _log.info(
            "Launched %s sidecar process (PID %d) on port %d",
            name,
            proc.pid,
            port,
        )

        return _await_service_health(svc, proc, stderr_path)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        _log.exception("Failed to launch %s: %s", name, exc)
        return {
            "status": "error",
            "message": f"Failed to launch {name}: {exc!s}",
            "port": port,
        }


def start_service(service_id: str) -> dict[str, Any]:
    """Start an ecosystem sidecar service daemon in the background."""
    svc = ecosystem().service(service_id)
    if svc is None:
        return {
            "status": "error",
            "message": (
                f"Unknown service '{service_id}'. "
                f"Known services: {[s.id for s in ecosystem().services]}"
            ),
        }
    blocked = _preflight_service(svc)
    if blocked is not None:
        return blocked
    python_exe = _resolve_service_python(_project_dir(svc))
    if not python_exe.exists():
        return {
            "status": "error",
            "message": (
                f"Virtual environment python not found at {python_exe}. "
                "Please initialize virtual environment before starting service."
            ),
        }
    return _launch_service(svc, python_exe)


def _kill_process_tree(pid: int) -> bool:
    """Terminate a process and all its descendant child processes to prevent zombies."""
    if not psutil.pid_exists(pid):
        return False
    try:
        if sys.platform == "win32":
            res = run_command_simple(["taskkill", "/F", "/T", "/PID", str(pid)], timeout=30)
            if res.returncode != 0:
                _log.warning(
                    "taskkill for PID %d exited with %d: %s",
                    pid, res.returncode, (res.stderr or res.stdout).strip(),
                )
            return res.returncode == 0

        parent = psutil.Process(pid)
        children = parent.children(recursive=True)
        for child in children:
            try:
                child.terminate()
            except psutil.NoSuchProcess:
                _log.debug("Child PID %d exited before terminate", child.pid)
        parent.terminate()
        _, alive = psutil.wait_procs([parent, *children], timeout=3)
        for p in alive:
            try:
                p.kill()
            except psutil.NoSuchProcess:
                _log.debug("PID %d exited before kill", p.pid)
        return True
    except (OSError, subprocess.SubprocessError, psutil.Error) as exc:
        _log.warning("Failed to terminate process tree for PID %d: %s", pid, exc)
        return False


def _stop_via_pid_files(log_dir: Path, name: str) -> bool:
    """Terminate the process tree recorded in a service PID file; remove the PID files."""
    terminated = False
    for pfile in (log_dir / "server.pid", log_dir / "pid"):
        if not pfile.exists():
            continue
        try:
            pid = int(pfile.read_text(encoding="utf-8").strip())
            if psutil.pid_exists(pid):
                terminated = _kill_process_tree(pid)
                _log.info("Terminated process tree for %s (PID %d) via PID file", name, pid)
            pfile.unlink(missing_ok=True)
        except (OSError, ValueError) as exc:
            _log.warning("Error stopping %s via PID file %s: %s", name, pfile, exc)
    return terminated


_NETSTAT_TCP_COLUMNS = 5


def _parse_netstat_pids(output: str, port: int) -> list[int]:
    """Extract listening or bound PIDs matching port from netstat stdout."""
    pids: list[int] = []
    suffix = f":{port}"
    for line in output.splitlines():
        fields = line.split()
        if len(fields) != _NETSTAT_TCP_COLUMNS or fields[0].upper() != "TCP":
            continue
        is_local = fields[1].endswith(suffix)
        is_listener = (
            fields[3].upper() == "LISTENING"
            or fields[2].endswith(":0")
            or fields[2] == "*:*"
        )
        if not (is_local and is_listener):
            continue
        try:
            pid = int(fields[4])
            if pid and pid not in pids:
                pids.append(pid)
        except ValueError:
            pass
    return pids


def _find_port_pids_netstat(port: int) -> list[int]:
    """Find listening PIDs using netstat on Windows when psutil encounters permission gates."""
    try:
        proc = run_command_simple(["netstat", "-ano", "-p", "TCP"], timeout=10)
        return _parse_netstat_pids(proc.stdout or "", port)
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("netstat check for port %d failed: %s", port, exc)
        return []


def _stop_port_holder(port: int) -> bool:
    """Terminate every process tree listening on ``port``; True if any was stopped."""
    pids: list[int] = []
    try:
        connections = psutil.net_connections(kind="inet")
        for conn in connections:
            if conn.laddr and conn.laddr.port == port and conn.pid:
                pids.append(conn.pid)
    except (psutil.AccessDenied, RuntimeError, OSError) as exc:
        _log.debug("Falling back to netstat for port %d: %s", port, exc)
        pids = _find_port_pids_netstat(port)

    terminated = False
    for pid in pids:
        if _kill_process_tree(pid):
            terminated = True
            _log.info("Terminated process tree on port %d (PID %d)", port, pid)
        else:
            _log.warning("Could not terminate process holding port %d (PID %d)", port, pid)
    return terminated


def stop_service(service_id: str) -> dict[str, Any]:
    """Stop an active ecosystem sidecar process by reading PID file or finding listening process."""
    svc = ecosystem().service(service_id)
    if svc is None:
        return {
            "status": "error",
            "message": f"Unknown service '{service_id}'.",
        }

    name = _name(svc)
    port = svc.port
    proj_dir: Path = _project_dir(svc)

    if svc.id == SELF_SERVICE_ID:
        def _terminate_self() -> None:
            time.sleep(0.5)
            _log.info("Terminating %s (port %d) on user request.", name, port)
            _kill_process_tree(os.getpid())

        threading.Thread(target=_terminate_self, daemon=True).start()
        return {
            "status": "stopped",
            "message": f"{name} on port {port} stopping...",
            "port": port,
        }

    terminated = _stop_via_pid_files(proj_dir / svc.log_dir, name)
    if not terminated and is_port_in_use(port):
        terminated = _stop_port_holder(port)

    if terminated:
        return {
            "status": "stopped",
            "message": f"{name} on port {port} stopped.",
            "port": port,
        }
    return {
        "status": "not_running",
        "message": f"{name} is not running on port {port}.",
        "port": port,
    }


def start_all_services() -> dict[str, Any]:
    """Start all offline ecosystem sidecars."""
    results: dict[str, Any] = {}
    for svc in ecosystem().services:
        if svc.id != SELF_SERVICE_ID:
            results[svc.id] = start_service(svc.id)
    return results
