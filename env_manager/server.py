"""FastAPI and WebSocket sidecar server module.

Runs as a Windows service or background executable, exposing endpoints for:
- Health checks
- Hardware probing (Torch/TensorRT/CUDA detection)
- Project discovery
- Dependency drift detection (Python, NPM)
- Manifest coverage analysis
- Smart clean installs (system + env)
- Update dry-runs and executions
- Pipeline orchestration
- Validation (py_compile, ESLint, Pylint, YAML, HTML, W3C)
- WebSocket log streaming

Start-Process "http://127.0.0.1:8000/docs" opens Swagger UI, which lets you click through every endpoint manually.
"""

import asyncio
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from pathlib import Path

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from env_manager.health_checker import run_full_health_audit
from env_manager.npm_manager import audit_npm_workspaces
from env_manager.orchestrator import PipelineEvent, PipelineOrchestrator
from env_manager.system_probe import probe_hardware
from env_manager.utils import purge_project_cache
from env_manager.venv_manager import discover_projects


# ─── WebSocket connection manager ──────────────────────────────────────────

class ConnectionManager:
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
_event_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
orchestrator = PipelineOrchestrator()


async def _drain_event_queue():
    try:
        while True:
            ev = await _event_queue.get()
            await manager.broadcast(ev)
            _event_queue.task_done()
    except asyncio.CancelledError:
        raise


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    drain_task = asyncio.create_task(_drain_event_queue())
    try:
        yield
    finally:
        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title="LemGendary Environment Manager API",
    version="2.0.0",
    description="REST and WebSocket sidecar service for LemGendary AI Studio.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RunPipelineRequest(BaseModel):
    target_project: Optional[str] = None


class CleanRequest(BaseModel):
    project: Optional[str] = None


class ValidateRequest(BaseModel):
    project: Optional[str] = None


class UpdateRequest(BaseModel):
    project: Optional[str] = None
    dry_run: bool = False


@app.get("/api/health")
async def get_health(
    include_safety: bool = Query(
        False,
        description=(
            "If true, run per-project pip dry-runs to classify which outdated "
            "packages can be safely upgraded. Adds 15-40s of latency per "
            "Python project. Default false for fast polling."
        ),
    ),
):
    """Retrieve complete ecosystem health audit."""
    loop = asyncio.get_running_loop()
    report = await loop.run_in_executor(
        None,
        lambda: run_full_health_audit(include_safety=include_safety),
    )
    return report.to_dict()


@app.get("/api/drift")
async def get_drift(
    include_safety: bool = Query(
        False,
        description="Same semantics as /api/health?include_safety.",
    ),
):
    """Retrieve Python and NPM package drift data, manifest coverage, and
    single-manifest inventory.

    Returns:

    - ``python``: cross-project drift entries (packages in >= 2 manifests).
    - ``coverage``: per-project declared-vs-installed reconciliation.
    - ``single_manifest``: packages declared in exactly one manifest.
    - ``npm``: npm workspace drift entries.
    """
    loop = asyncio.get_running_loop()
    report = await loop.run_in_executor(
        None,
        lambda: run_full_health_audit(include_safety=include_safety),
    )

    python_drift: List[Dict[str, Any]] = []
    for entry in report.version_drift:
        python_drift.append({
            "package_name": entry.package_name,
            "versions": entry.versions,
            "pins": {
                proj: {
                    "pin_type": pin.pin_type,
                    "specifier": pin.specifier,
                    "installed": pin.installed,
                }
                for proj, pin in entry.pins.items()
            },
            "upgrades": {
                proj: {
                    "current": status.current,
                    "latest": status.latest,
                    "safe": status.safe,
                    "reason": status.reason,
                    "is_outdated": status.is_outdated,
                }
                for proj, status in entry.upgrades.items()
            },
            "has_drift": entry.has_drift,
            "has_pin_mismatch": entry.has_pin_mismatch,
            "projects_declared": entry.projects_declared,
        })

    coverage: List[Dict[str, Any]] = [c.to_dict() for c in report.manifest_coverage]
    single_manifest: List[Dict[str, Any]] = [
        s.to_dict() for s in report.single_manifest_packages
    ]

    npm_drift: List[Dict[str, Any]] = []
    for entry in report.npm_drift:
        npm_drift.append({
            "package_name": entry.package_name,
            "versions": entry.versions,
            "specifiers": entry.specifiers,
            "has_drift": entry.has_drift,
            "is_dev_dependency": entry.is_dev_dependency,
        })

    return {
        "python": python_drift,
        "coverage": coverage,
        "single_manifest": single_manifest,
        "npm": npm_drift,
        "include_safety": include_safety,
    }


@app.get("/api/hardware")
async def get_hardware():
    loop = asyncio.get_running_loop()
    profile = await loop.run_in_executor(None, probe_hardware)
    return profile.to_dict()


@app.get("/api/projects")
async def get_projects():
    loop = asyncio.get_running_loop()
    projects = await loop.run_in_executor(None, discover_projects)
    return [p.to_dict() for p in projects]


@app.get("/api/npm")
async def get_npm():
    loop = asyncio.get_running_loop()
    npm_statuses = await loop.run_in_executor(None, audit_npm_workspaces)
    return [s.to_dict() for s in npm_statuses]


@app.get("/api/pipeline/status")
async def get_pipeline_status():
    return {
        "is_running": orchestrator.is_running,
        "last_status": orchestrator.last_status,
        "last_run_timestamp": orchestrator.last_run_timestamp,
        "recent_events": recent_events[-50:],
    }


def _run_pipeline_worker(target_project: Optional[str], loop: asyncio.AbstractEventLoop):
    for event in orchestrator.run_clean_install_pipeline(target_project=target_project):
        ev_dict = event.to_dict()
        recent_events.append(ev_dict)
        if len(recent_events) > 200:
            recent_events.pop(0)
        asyncio.run_coroutine_threadsafe(manager.broadcast(ev_dict), loop)


@app.post("/api/pipeline/run")
async def run_pipeline(request: RunPipelineRequest):
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


@app.post("/api/update")
async def run_update(request: Optional[UpdateRequest] = None):
    """Trigger safe bottom-up package upgrades across all projects."""
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
    events: List[Dict[str, Any]] = []

    def _run_update():
        for ev in apply_upgrade_plan(plan, base_dir, extra_index_url=hw.recommended_torch_index):
            ev_dict = ev.to_dict()
            events.append(ev_dict)
            asyncio.run_coroutine_threadsafe(manager.broadcast(ev_dict), loop)

    thread = threading.Thread(target=_run_update, daemon=True)
    thread.start()
    thread.join(timeout=1200)

    all_ok = all(e.get("status") in ("upgraded", "skipped") for e in events)
    return {
        "status": "success" if all_ok else "partial",
        "events": events,
    }


@app.get("/api/manifests")
async def get_manifests():
    env_mgr_dir = Path(__file__).resolve().parent.parent
    manifests_dir = env_mgr_dir / "requirements"
    manifests = {}
    if manifests_dir.exists():
        for mf in manifests_dir.glob("*.txt"):
            manifests[mf.name] = mf.read_text(encoding="utf-8")
    return {"manifests": manifests}


@app.post("/api/manifests/sync")
async def sync_manifests():
    from env_manager.requirements_manager import sync_all_manifests
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, sync_all_manifests)
    formatted = {k: {"success": ok, "message": msg} for k, (ok, msg) in results.items()}
    return {"status": "success", "results": formatted}


@app.post("/api/clean")
async def clean_artifacts(request: Optional[CleanRequest] = None):
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
    await manager.connect(websocket)
    for ev in recent_events[-20:]:
        try:
            await websocket.send_json(ev)
        except Exception:
            break

    try:
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception):
        manager.disconnect(websocket)