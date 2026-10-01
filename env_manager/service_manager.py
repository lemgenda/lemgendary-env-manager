"""Ecosystem background sidecar service lifecycle management module.

Orchestrates starting, stopping, and health checking the tripartite
services: LemGendary Environment Manager (8000), LemGendary Dataset
Compiler Suite (8100), and LemGendary Model Training Suite (8200).
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from env_manager.venv_manager import get_venv_python_path

_log = logging.getLogger("env_manager.service_manager")

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent.parent

SERVICE_CONFIGS: Dict[str, Dict[str, Any]] = {
    "dataset-compiler": {
        "canonical_name": "LemGendary Dataset Compiler Suite",
        "aliases": ["dataset-compiler", "lemgendary-datasets", "datasets"],
        "project_dir": WORKSPACE_ROOT / "lemgendary-datasets",
        "port": 8100,
        "health_url": "http://127.0.0.1:8100/api/health",
        "log_folder": ".lgd_server",
        "module": "api.server",
        "app_import": "api.server:app",
    },
    "training-suite": {
        "canonical_name": "LemGendary Model Training Suite",
        "aliases": ["training-suite", "lemgendary-training-suite", "training"],
        "project_dir": WORKSPACE_ROOT / "lemgendary-training-suite",
        "port": 8200,
        "health_url": "http://127.0.0.1:8200/api/health",
        "log_folder": ".lemtrain_server",
        "module": "training.server.app",
        "app_import": "training.server.app:app",
    },
    "env-manager": {
        "canonical_name": "LemGendary Environment Manager",
        "aliases": ["env-manager", "lemgendary-env-manager", "env"],
        "project_dir": WORKSPACE_ROOT / "lemgendary-env-manager",
        "port": 8000,
        "health_url": "http://127.0.0.1:8000/api/health",
        "log_folder": ".env_server",
        "module": "env_manager.server",
        "app_import": "env_manager.server:app",
    },
}


def _resolve_service_key(service_id: str) -> Optional[str]:
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
    except Exception:
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


def start_service(service_id: str) -> Dict[str, Any]:
    """Start an ecosystem sidecar service daemon in the background."""
    key = _resolve_service_key(service_id)
    if not key:
        return {
            "status": "error",
            "message": f"Unknown service '{service_id}'. Known services: {list(SERVICE_CONFIGS.keys())}",
        }

    cfg = SERVICE_CONFIGS[key]
    port = cfg["port"]
    name = cfg["canonical_name"]
    health_url = cfg["health_url"]
    proj_dir: Path = cfg["project_dir"]

    if key == "env-manager":
        return {
            "status": "already_running",
            "message": f"{name} is the orchestrator and is currently running on port {port}.",
            "port": port,
        }

    if is_service_healthy(health_url):
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

    python_exe = get_background_python_path(proj_dir)
    if not python_exe.exists():
        python_exe = get_venv_python_path(proj_dir)
    if not python_exe.exists():
        return {
            "status": "error",
            "message": (
                f"Virtual environment python not found at {python_exe}. "
                "Please initialize virtual environment before starting service."
            ),
        }

    log_dir = proj_dir / cfg["log_folder"]
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _log.warning("Could not create server log folder %s: %s", log_dir, exc)

    stdout_path = log_dir / "service_stdout.log"
    stderr_path = log_dir / "service_stderr.log"

    cmd: List[str] = [
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
            popen_kwargs: Dict[str, Any] = {
                "cwd": str(proj_dir),
                "stdin": subprocess.DEVNULL,
                "stdout": out_f,
                "stderr": err_f,
                "close_fds": True,
            }

            if sys.platform == "win32":
                # Windows: Use CREATE_NO_WINDOW (0x08000000) to ensure zero console or terminal windows open
                popen_kwargs["creationflags"] = getattr(
                    subprocess, "CREATE_NO_WINDOW", 0x08000000
                )
            else:
                # POSIX (Linux & macOS): detach process into its own session without any terminal
                popen_kwargs["start_new_session"] = True

            proc = subprocess.Popen(cmd, **popen_kwargs)

        _log.info(
            "Launched %s sidecar process (PID %d) on port %d",
            name,
            proc.pid,
            port,
        )

        # Poll health endpoint up to 20.0 seconds to confirm startup (allows PyTorch/CUDA model suites to initialize)
        start_time = time.time()
        while time.time() - start_time < 20.0:
            time.sleep(0.5)
            if is_service_healthy(health_url):
                return {
                    "status": "started",
                    "message": f"{name} started successfully on port {port}.",
                    "port": port,
                    "pid": proc.pid,
                }
            if proc.poll() is not None:
                # Process terminated prematurely
                err_text = ""
                if stderr_path.exists():
                    try:
                        err_text = stderr_path.read_text(encoding="utf-8")[-500:]
                    except Exception:
                        pass
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

    except Exception as exc:
        _log.exception("Failed to launch %s: %s", name, exc)
        return {
            "status": "error",
            "message": f"Failed to launch {name}: {str(exc)}",
            "port": port,
        }


def stop_service(service_id: str) -> Dict[str, Any]:
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
    proj_dir: Path = cfg["project_dir"]

    if key == "env-manager":
        return {
            "status": "error",
            "message": "Cannot terminate Environment Manager from its own control endpoint.",
        }

    # Attempt to terminate via PID file first
    pid_files = [
        proj_dir / cfg["log_folder"] / "server.pid",
        proj_dir / cfg["log_folder"] / "pid",
    ]

    import psutil

    terminated = False
    for pfile in pid_files:
        if pfile.exists():
            try:
                pid = int(pfile.read_text(encoding="utf-8").strip())
                if psutil.pid_exists(pid):
                    p = psutil.Process(pid)
                    p.terminate()
                    try:
                        p.wait(timeout=3)
                    except psutil.TimeoutExpired:
                        p.kill()
                    terminated = True
                    _log.info("Terminated %s (PID %d) via PID file", name, pid)
                pfile.unlink(missing_ok=True)
            except Exception as exc:
                _log.warning("Error stopping %s via PID file %s: %s", name, pfile, exc)

    # If still listening on port, terminate the process holding the port
    if not terminated and is_port_in_use(port):
        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.pid:
                try:
                    p = psutil.Process(conn.pid)
                    p.terminate()
                    try:
                        p.wait(timeout=3)
                    except psutil.TimeoutExpired:
                        p.kill()
                    terminated = True
                    _log.info("Terminated process on port %d (PID %d)", port, conn.pid)
                except Exception as exc:
                    _log.warning("Could not terminate process holding port %d: %s", port, exc)

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


def start_all_services() -> Dict[str, Any]:
    """Start all offline ecosystem sidecars."""
    results: Dict[str, Any] = {}
    for key in ["dataset-compiler", "training-suite"]:
        results[key] = start_service(key)
    return results
