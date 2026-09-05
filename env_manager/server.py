"""FastAPI and WebSocket sidecar server module.

Provides REST and WebSocket endpoints for the Tauri desktop interface and external clients.
"""

import asyncio
from typing import Any, Dict, List, Optional
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from env_manager.health_checker import run_full_health_audit
from env_manager.npm_manager import audit_npm_workspaces
from env_manager.orchestrator import PipelineEvent, PipelineOrchestrator
from env_manager.system_probe import probe_hardware
from env_manager.venv_manager import discover_projects

app = FastAPI(
    title="LemGendary Environment Manager API",
    version="2.0.0",
    description="REST and WebSocket sidecar service for LemGendary AI Studio.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

orchestrator = PipelineOrchestrator()


class ConnectionManager:
    """Manages active WebSocket connections."""

    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: Dict[str, Any]):
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.disconnect(connection)


manager = ConnectionManager()
recent_events: List[Dict[str, Any]] = []


class RunPipelineRequest(BaseModel):
    target_project: Optional[str] = None


@app.get("/api/health")
async def get_health():
    """Retrieve complete ecosystem health audit."""
    loop = asyncio.get_running_loop()
    report = await loop.run_in_executor(None, run_full_health_audit)
    return report.to_dict()


@app.get("/api/hardware")
async def get_hardware():
    """Retrieve system hardware and accelerator discovery."""
    loop = asyncio.get_running_loop()
    profile = await loop.run_in_executor(None, probe_hardware)
    return profile.to_dict()


@app.get("/api/projects")
async def get_projects():
    """Retrieve status of all managed projects."""
    loop = asyncio.get_running_loop()
    projects = await loop.run_in_executor(None, discover_projects)
    return [p.to_dict() for p in projects]


@app.get("/api/npm")
async def get_npm():
    """Retrieve NPM packages status."""
    loop = asyncio.get_running_loop()
    npm_statuses = await loop.run_in_executor(None, audit_npm_workspaces)
    return [s.to_dict() for s in npm_statuses]


@app.get("/api/pipeline/status")
async def get_pipeline_status():
    """Retrieve current orchestrator execution status and recent events."""
    return {
        "is_running": orchestrator.is_running,
        "last_status": orchestrator.last_status,
        "last_run_timestamp": orchestrator.last_run_timestamp,
        "recent_events": recent_events[-50:],
    }


def _run_pipeline_worker(target_project: Optional[str]):
    """Background worker executing orchestrator pipeline and pushing events to asyncio queue."""
    for event in orchestrator.run_clean_install_pipeline(target_project=target_project):
        ev_dict = event.to_dict()
        recent_events.append(ev_dict)
        if len(recent_events) > 200:
            recent_events.pop(0)
        asyncio.run(manager.broadcast(ev_dict))


@app.post("/api/pipeline/run")
async def run_pipeline(request: RunPipelineRequest):
    """Trigger the Smart Clean Install Pipeline."""
    if orchestrator.is_running:
        return {"status": "error", "message": "Pipeline is already running."}

    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, _run_pipeline_worker, request.target_project)
    return {"status": "accepted", "message": "Pipeline initiated."}


@app.websocket("/ws/log")
async def websocket_logs(websocket: WebSocket):
    """WebSocket stream for real-time pipeline events and log entries."""
    await manager.connect(websocket)
    # Send recent events upon connection
    for ev in recent_events[-20:]:
        try:
            await websocket.send_json(ev)
        except Exception:
            break

    try:
        while True:
            # Keep-alive loop
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        manager.disconnect(websocket)
