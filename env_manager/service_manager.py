"""Ecosystem background sidecar service lifecycle management module.

Orchestrates starting, stopping, and health checking the tripartite
services: LemGendary Environment Manager (8000), LemGendary Dataset
Compiler Suite (8100), and LemGendary Model Training Suite (8200).
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
from pathlib import Path
from typing import Any

import psutil

from env_manager._logging import get_logger
from env_manager.venv_manager import get_venv_python_path
from env_manager.workspace import workspace_root

_log = get_logger(__name__)

SERVICE_CONFIGS: dict[str, dict[str, Any]] = {
    "dataset-compiler": {
        "canonical_name": "LemGendary Dataset Compiler Suite",
        "aliases": ["dataset-compiler", "lemgendary-datasets", "datasets"],
        "project": "lemgendary-datasets",
        "port": 8100,
        "health_url": "http://127.0.0.1:8100/api/health",
        "log_folder": ".lgd_server",
        "module": "api.server",
        "app_import": "api.server:app",
    },
    "training-suite": {
        "canonical_name": "LemGendary Model Training Suite",
        "aliases": ["training-suite", "lemgendary-training-suite", "training"],
        "project": "lemgendary-training-suite",
        "port": 8200,
        "health_url": "http://127.0.0.1:8200/api/health",
        "log_folder": ".lemtrain_server",
        "module": "training.server.app",
        "app_import": "training.server.app:app",
    },
    "env-manager": {
        "canonical_name": "LemGendary Environment Manager",
        "aliases": ["env-manager", "lemgendary-env-manager", "env"],
        "project": "lemgendary-env-manager",
        "port": 8000,
        "health_url": "http://127.0.0.1:8000/api/health",
        "log_folder": ".env_server",
        "module": "env_manager.server",
        "app_import": "env_manager.server:app",
    },
}


def _project_dir(cfg: dict[str, Any]) -> Path:
    """Return the service's project folder inside the current workspace."""
    return workspace_root() / cfg["project"]


def _resolve_service_key(service_id: str) -> str | None:
    """Resolve service key from canonical id or aliases."""
    normalized = service_id.strip().lower()
    for key, cfg in SERVICE_CONFIGS.items():
        if normalized == key or normalized in cfg["aliases"]:
            return key
    return None


def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Check if TCP port is actively accepting connections."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex((host, port)) == 0


def is_service_healthy(health_url: str, timeout: float = 1.0) -> bool:
    """Probe service HTTP health endpoint for 200 OK status."""
    try:
        req = urllib.request.Request(
            health_url,
            headers={"User-Agent": "LemGendary-EnvManager/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status == 200
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


def _preflight_service(key: str, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Return a response when the service must not be launched, else None."""
    port = cfg["port"]
    name = cfg["canonical_name"]
    proj_dir: Path = _project_dir(cfg)
    if key == "env-manager":
        return {
            "status": "already_running",
            "message": f"{name} is the orchestrator and is currently running on port {port}.",
            "port": port,
        }
    if is_service_healthy(cfg["health_url"]):
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
    cfg: dict[str, Any], proc: subprocess.Popen, stderr_path: Path,
) -> dict[str, Any]:
    """Poll the health endpoint up to 20 s; report started, exited or still starting.

    The 20 s window lets PyTorch/CUDA model suites initialise.
    """
    port = cfg["port"]
    name = cfg["canonical_name"]
    start_time = time.time()
    while time.time() - start_time < 20.0:
        time.sleep(0.5)
        if is_service_healthy(cfg["health_url"]):
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


def _launch_service(cfg: dict[str, Any], python_exe: Path) -> dict[str, Any]:
    """Spawn the service detached from any console and wait for it to come up."""
    port = cfg["port"]
    name = cfg["canonical_name"]
    proj_dir: Path = _project_dir(cfg)

    log_dir = proj_dir / cfg["log_folder"]
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
        cfg["app_import"],
        "--host",
        "127.0.0.1",
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

        _log.info(
            "Launched %s sidecar process (PID %d) on port %d",
            name,
            proc.pid,
            port,
        )

        return _await_service_health(cfg, proc, stderr_path)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        _log.exception("Failed to launch %s: %s", name, exc)
        return {
            "status": "error",
            "message": f"Failed to launch {name}: {exc!s}",
            "port": port,
        }


def start_service(service_id: str) -> dict[str, Any]:
    """Start an ecosystem sidecar service daemon in the background."""
    key = _resolve_service_key(service_id)
    if not key:
        return {
            "status": "error",
            "message": (
                f"Unknown service '{service_id}'. "
                f"Known services: {list(SERVICE_CONFIGS.keys())}"
            ),
        }
    cfg = SERVICE_CONFIGS[key]
    blocked = _preflight_service(key, cfg)
    if blocked is not None:
        return blocked
    python_exe = _resolve_service_python(_project_dir(cfg))
    if not python_exe.exists():
        return {
            "status": "error",
            "message": (
                f"Virtual environment python not found at {python_exe}. "
                "Please initialize virtual environment before starting service."
            ),
        }
    return _launch_service(cfg, python_exe)


def _kill_process_tree(pid: int) -> bool:
    """Terminate a process and all its descendant child processes to prevent zombies."""
    if not psutil.pid_exists(pid):
        return False
    try:
        if sys.platform == "win32":
            cflags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
            res = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                check=False,
                creationflags=cflags,
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


def stop_service(service_id: str) -> dict[str, Any]:
    """Stop an active ecosystem sidecar process by reading PID file or finding listening process."""
    key = _resolve_service_key(service_id)
    if not key:
        return {
            "status": "error",
            "message": f"Unknown service '{service_id}'.",
        }

    cfg = SERVICE_CONFIGS[key]
    name = cfg["canonical_name"]
    port = cfg["port"]
    proj_dir: Path = _project_dir(cfg)

    if key == "env-manager":
        def _terminate_self() -> None:
            time.sleep(0.5)
            _log.info("Terminating Environment Manager (Port 8000) on user request.")
            _kill_process_tree(os.getpid())

        threading.Thread(target=_terminate_self, daemon=True).start()
        return {
            "status": "stopped",
            "message": f"{name} on port {port} stopping...",
            "port": port,
        }

    # Attempt to terminate via PID file first
    pid_files = [
        proj_dir / cfg["log_folder"] / "server.pid",
        proj_dir / cfg["log_folder"] / "pid",
    ]

    terminated = False
    for pfile in pid_files:
        if pfile.exists():
            try:
                pid = int(pfile.read_text(encoding="utf-8").strip())
                if psutil.pid_exists(pid):
                    terminated = _kill_process_tree(pid)
                    _log.info("Terminated process tree for %s (PID %d) via PID file", name, pid)
                pfile.unlink(missing_ok=True)
            except (OSError, ValueError) as exc:
                _log.warning("Error stopping %s via PID file %s: %s", name, pfile, exc)

    # If still listening on port, terminate the process holding the port and all descendants
    if not terminated and is_port_in_use(port):
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.pid:
                terminated = _kill_process_tree(conn.pid)
                if terminated:
                    _log.info("Terminated process tree on port %d (PID %d)", port, conn.pid)
                else:
                    _log.warning("Could not terminate process holding port %d (PID %d)",
                                 port, conn.pid)

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
    for key in ["dataset-compiler", "training-suite"]:
        results[key] = start_service(key)
    return results
