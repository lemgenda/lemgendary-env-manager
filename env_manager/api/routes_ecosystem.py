"""GUI and ecosystem routes: GUI state, sidecar services, shutdown and the log WebSocket."""

import asyncio
import json
import os
import signal
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from typing import Any

from fastapi import (
    APIRouter,
    HTTPException,
    WebSocket,
    WebSocketDisconnect,
)

from env_manager import __version__
from env_manager._logging import get_logger
from env_manager.api.state import manager, orchestrator, recent_events_snapshot
from env_manager.service_manager import start_all_services, start_service, stop_service
from env_manager.system_probe import probe_hardware
from env_manager.venv_manager import discover_projects

router = APIRouter()
_log = get_logger(__name__)


def _probe_sidecar(url: str, timeout: float = 1.5) -> dict[str, Any]:
    """Probe an external HTTP sidecar endpoint cleanly with a brief timeout."""
    req = urllib.request.Request(url, headers={"User-Agent": "LemGendary-Env-Manager"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == HTTPStatus.OK:
                body = resp.read().decode("utf-8")
                try:
                    data = json.loads(body)
                    return {"status": "online", "reachable": True, "data": data}
                except json.JSONDecodeError as exc:
                    _log.debug("Sidecar response at %s was not valid JSON: %s", url, exc)
                    return {"status": "online", "reachable": True, "raw": body}
            return {"status": "error", "reachable": True, "http_status": resp.status}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _log.debug("Sidecar probe to %s failed: %s", url, exc)
        return {"status": "offline", "reachable": False, "error": str(exc)}


@router.get("/api/gui/state")
async def get_gui_state():
    """Aggregated ecosystem and host state for LemGendary AI Studio Desktop GUI.

    Returns host telemetry, hardware profile, discovered projects summary,
    and orchestrator status in a single non-blocking payload.
    """
    loop = asyncio.get_running_loop()
    hw_future = loop.run_in_executor(None, probe_hardware)
    proj_future = loop.run_in_executor(None, discover_projects)
    hw, projects = await asyncio.gather(hw_future, proj_future)

    return {
        "service": {
            "name": "lemgendary-env-manager",
            "version": __version__,
            "port": 8000,
            "status": "online",
        },
        "hardware": hw.to_dict(),
        "projects": [p.to_dict() for p in projects],
        "pipeline": {
            "is_running": orchestrator.is_running,
            "last_status": orchestrator.last_status,
            "last_run_timestamp": orchestrator.last_run_timestamp,
        },
    }


@router.get("/api/gui/ecosystem")
async def get_gui_ecosystem():
    """Multi-sidecar health overview for LemGendary AI Studio Desktop GUI top-bar status."""
    loop = asyncio.get_running_loop()
    datasets_probe = await loop.run_in_executor(
        None,
        lambda: _probe_sidecar("http://127.0.0.1:8100/api/health", timeout=1.5),
    )
    training_probe = await loop.run_in_executor(
        None,
        lambda: _probe_sidecar("http://127.0.0.1:8200/api/health", timeout=1.5),
    )

    return {
        "env_manager": {
            "service": "lemgendary-env-manager",
            "port": 8000,
            "status": "online",
            "reachable": True,
        },
        "dataset_compiler": {
            "service": "lemgendary-datasets",
            "port": 8100,
            **datasets_probe,
        },
        "training_suite": {
            "service": "lemgendary-training-suite",
            "port": 8200,
            **training_probe,
        },
    }


@router.post("/api/services/{service_id}/start")
async def start_ecosystem_service(service_id: str):
    """Start an ecosystem sidecar service daemon in the background."""
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, start_service, service_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message"))
    return result


@router.post("/api/services/{service_id}/stop")
async def stop_ecosystem_service(service_id: str):
    """Stop an active ecosystem sidecar service daemon."""
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, stop_service, service_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message"))
    return result


@router.post("/api/services/start-all")
async def start_all_ecosystem_services():
    """Start all offline ecosystem sidecars."""
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, start_all_services)
    return {"results": results}


@router.post("/api/shutdown")
async def graceful_shutdown():
    """Initiate a graceful self-shutdown of the Environment Manager server.

    Schedules process termination after a short delay so the HTTP response
    is delivered to the caller before the process exits.
    """
    def _exit_after_delay() -> None:
        time.sleep(0.5)
        _log.info("Graceful shutdown requested via /api/shutdown - terminating server process.")
        os.kill(os.getpid(), signal.SIGTERM)

    t = threading.Thread(target=_exit_after_delay, daemon=True)
    t.start()
    return {
        "status": "shutting_down",
        "message": "Environment Manager is shutting down. Restart via the CLI or Task Manager.",
    }


@router.websocket("/ws/log")
@router.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    """Replay recent events to a new client, then keep the socket open for broadcasts."""
    await manager.connect(websocket)
    try:
        for ev in recent_events_snapshot(20):
            await websocket.send_json(ev)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect as exc:
        _log.debug("WebSocket client disconnected (code %s)", exc.code)
    except (RuntimeError, OSError) as exc:
        _log.warning("WebSocket connection failed: %s", exc)
    finally:
        manager.disconnect(websocket)
