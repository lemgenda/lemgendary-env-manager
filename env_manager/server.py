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


class CleanRequest(BaseModel):
    project: Optional[str] = None


class ValidateRequest(BaseModel):
    project: Optional[str] = None


@app.get("/api/manifests")
async def get_manifests():
    """List centralized requirements manifests and their contents."""
    env_mgr_dir = Path(__file__).resolve().parent.parent
    manifests_dir = env_mgr_dir / "requirements"
    manifests = {}
    if manifests_dir.exists():
        for mf in manifests_dir.glob("*.txt"):
            manifests[mf.name] = mf.read_text(encoding="utf-8")
    return {"manifests": manifests}


@app.post("/api/manifests/sync")
async def sync_manifests():
    """Trigger synchronization of centralized manifests to all sibling projects."""
    from env_manager.requirements_manager import sync_all_manifests
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, sync_all_manifests)
    formatted = {k: {"success": ok, "message": msg} for k, (ok, msg) in results.items()}
    return {"status": "success", "results": formatted}


@app.post("/api/clean")
async def clean_artifacts(request: Optional[CleanRequest] = None):
    """Purge bytecode caches and temporary build artifacts."""
    import shutil
    base_dir = Path(__file__).resolve().parent.parent.parent
    projects = discover_projects(base_dir)
    target_name = request.project if request else None

    total_reclaimed = 0
    cleaned_count = 0

    target_projects = [p for p in projects if target_name is None or p.name == target_name]
    for p in target_projects:
        p_path = Path(p.project_dir)
        for item in p_path.rglob("__pycache__"):
            if ".venv" in item.parts:
                continue
            if item.is_dir():
                try:
                    size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
                    shutil.rmtree(item)
                    total_reclaimed += size
                    cleaned_count += 1
                except Exception:
                    pass

        for pattern in ["*.pyc", "*.pyo", "*.pyd"]:
            for item in p_path.rglob(pattern):
                if ".venv" in item.parts:
                    continue
                if item.is_file():
                    try:
                        size = item.stat().st_size
                        item.unlink()
                        total_reclaimed += size
                        cleaned_count += 1
                    except Exception:
                        pass

    return {
        "status": "success",
        "cleaned_count": cleaned_count,
        "reclaimed_bytes": total_reclaimed,
        "reclaimed_mb": round(total_reclaimed / (1024 * 1024), 2),
    }


@app.post("/api/validate")
async def validate_codebase(request: Optional[ValidateRequest] = None):
    """Run syntax compilation and zero-emoji compliance checks across projects."""
    from env_manager.validator import validate_project
    base_dir = Path(__file__).resolve().parent.parent.parent
    projects = discover_projects(base_dir)
    target_name = request.project if request else None

    target_projects = [p for p in projects if target_name is None or p.name == target_name]
    results = {}
    all_passed = True

    for p in target_projects:
        rep = validate_project(Path(p.project_dir))
        results[p.name] = rep.to_dict()
        if not rep.passed:
            all_passed = False

    return {
        "status": "success" if all_passed else "failed",
        "all_passed": all_passed,
        "projects": results,
    }


@app.websocket("/ws/log")
@app.websocket("/ws/logs")
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
