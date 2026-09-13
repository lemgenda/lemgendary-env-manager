"""FastAPI and WebSocket sidecar server module.

Provides REST and WebSocket endpoints for the Tauri desktop interface and external clients.

WebSocket log streaming uses an asyncio.Queue fed from a thread pool worker to avoid
the 'asyncio.run() called from running event loop' crash.
"""

import asyncio
import threading
from typing import Any, Dict, List, Optional
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from env_manager.health_checker import run_full_health_audit
from env_manager.npm_manager import audit_npm_workspaces
from env_manager.orchestrator import PipelineEvent, PipelineOrchestrator
from env_manager.system_probe import probe_hardware
from env_manager.utils import purge_project_cache
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
# Queue used to pass events from thread workers to the asyncio event loop safely
_event_queue: asyncio.Queue = asyncio.Queue(maxsize=500)


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


def _run_pipeline_worker(target_project: Optional[str], loop: asyncio.AbstractEventLoop):
    """Background thread executing the orchestrator pipeline.

    Events are placed on the asyncio-safe queue instead of calling asyncio.run()
    directly, which would crash because a running event loop already exists.
    """
    for event in orchestrator.run_clean_install_pipeline(target_project=target_project):
        ev_dict = event.to_dict()
        recent_events.append(ev_dict)
        if len(recent_events) > 200:
            recent_events.pop(0)
        # Schedule broadcast on the running event loop from this thread
        asyncio.run_coroutine_threadsafe(manager.broadcast(ev_dict), loop)


async def _drain_event_queue():
    """Background asyncio task that forwards queued events to WebSocket clients.

    This task is started on app startup as a complementary broadcast mechanism.
    """
    while True:
        ev = await _event_queue.get()
        await manager.broadcast(ev)
        _event_queue.task_done()


@app.post("/api/pipeline/run")
async def run_pipeline(request: RunPipelineRequest):
    """Trigger the Smart Clean Install Pipeline."""
    if orchestrator.is_running:
        return {"status": "error", "message": "Pipeline is already running."}

    loop = asyncio.get_running_loop()
    thread = threading.Thread(
        target=_run_pipeline_worker,
        args=(request.target_project, loop),
        daemon=True,
    )
    thread.start()
    return {"status": "accepted", "message": "Pipeline initiated."}


class CleanRequest(BaseModel):
    project: Optional[str] = None


class ValidateRequest(BaseModel):
    project: Optional[str] = None


@app.on_event("startup")
async def startup_event():
    """Start the background event drain task on server startup."""
    asyncio.create_task(_drain_event_queue())


class UpdateRequest(BaseModel):
    project: Optional[str] = None
    dry_run: bool = False


@app.post("/api/update")
async def run_update(request: Optional[UpdateRequest] = None):
    """Trigger safe bottom-up package upgrades across all projects and auto-sync manifests."""
    from env_manager.updater import build_upgrade_plan, apply_upgrade_plan
    from env_manager.system_probe import probe_hardware

    base_dir = Path(__file__).resolve().parent.parent.parent
    plan = build_upgrade_plan(base_dir)

    if not plan.has_any_updates:
        return {"status": "up_to_date", "message": "All packages are up to date.", "events": []}

    if request and request.dry_run:
        return {
            "status": "dry_run",
            "plan": plan.to_dict(),
        }

    hw = probe_hardware()
    loop = asyncio.get_running_loop()
    events = []

    def _run_update():
        for ev in apply_upgrade_plan(plan, base_dir, extra_index_url=hw.recommended_torch_index):
            ev_dict = ev.to_dict()
            events.append(ev_dict)
            asyncio.run_coroutine_threadsafe(manager.broadcast(ev_dict), loop)

    thread = threading.Thread(target=_run_update, daemon=True)
    thread.start()
    thread.join(timeout=1200)  # max 20 min

    all_ok = all(e.get("status") in ("upgraded", "skipped") for e in events)
    return {
        "status": "success" if all_ok else "partial",
        "events": events,
    }


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
        cnt, reclaimed = purge_project_cache(Path(p.project_dir))
        cleaned_count += cnt
        total_reclaimed += reclaimed

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
