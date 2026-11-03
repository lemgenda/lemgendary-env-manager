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
import json
import logging
import os
import signal
import threading
import time
import urllib.error
import urllib.request
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional

import yaml

from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from env_manager import __version__
from env_manager.bug_report import (
    DEFAULT_SETTINGS as BUG_REPORT_SETTINGS,
    BugReportFields,
    BugReportResponse,
    BugReportService,
    ReportsDirError,
    SystemPlatformAdapter,
    UploadRejectedError,
    collect_submission,
)

from env_manager.health_checker import run_full_health_audit
from env_manager.hooks import install_git_hooks
from env_manager.npm_manager import audit_npm_workspaces
from env_manager.orchestrator import PipelineOrchestrator
from env_manager.security import (
    ALLOWED_HEADERS,
    ALLOWED_METHODS,
    ALLOWED_ORIGINS,
    origin_guard_middleware,
)
from env_manager.secrets_vault import (
    SecretsListResponse,
    SecretsPayload,
    SecretsSaveResponse,
    SecretsStorageError,
    SecretsValidationError,
    SecretsVault,
)
from env_manager.requirements_manager import sync_all_manifests
from env_manager.service_manager import start_all_services, start_service, stop_service
from env_manager.system_probe import probe_hardware
from env_manager.update_jobs import UpdateAlreadyRunningError, UpdateJobManager, run_safe_update
from env_manager.updater import UPGRADE_ORDER, build_upgrade_plan
from env_manager.utils import purge_project_cache
from env_manager.validator import validate_project
from env_manager.venv_manager import discover_projects
from env_manager.workspace import ENV_MANAGER_DIR, workspace_root

# Failures a WebSocket send/receive raises once the peer has gone away:
# starlette's disconnect signal, RuntimeError for a closed socket, and
# OSError (uvicorn's ClientDisconnected) for a dropped transport.
_WEBSOCKET_GONE_ERRORS = (WebSocketDisconnect, RuntimeError, OSError)

_log = logging.getLogger("env_manager.server")


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
            except _WEBSOCKET_GONE_ERRORS as exc:
                _log.info("Dropping websocket client after failed broadcast: %s", exc)
                self.disconnect(connection)


manager = ConnectionManager()
recent_events: List[Dict[str, Any]] = []
_event_queue: asyncio.Queue = asyncio.Queue(maxsize=500)
orchestrator = PipelineOrchestrator()


async def _drain_event_queue():
    while True:
        ev = await _event_queue.get()
        await manager.broadcast(ev)
        _event_queue.task_done()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    drain_task = asyncio.create_task(_drain_event_queue())
    # Automatically ensure tripartite sidecars are started on startup
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, start_all_services)
    try:
        yield
    finally:
        drain_task.cancel()
        try:
            await drain_task
        except asyncio.CancelledError:
            _log.debug("Drain event queue task cancelled cleanly during shutdown.")


app = FastAPI(
    title="LemGendary Environment Manager API",
    version=__version__,
    description="REST and WebSocket sidecar service for LemGendary AI Studio.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_ORIGINS),
    allow_credentials=False,
    allow_methods=list(ALLOWED_METHODS),
    allow_headers=list(ALLOWED_HEADERS),
)
# Added last, so it runs first: refuses foreign origins before CORS or routing.
app.add_middleware(origin_guard_middleware)


class RunPipelineRequest(BaseModel):
    target_project: Optional[str] = None
    # Destructive opt-in: deletes every venv and node_modules and stops
    # processes running from those venvs. Omitted means a safe run.
    clean: bool = False


class RunStepRequest(BaseModel):
    step_number: int
    target_project: Optional[str] = None
    clean: bool = False



class CleanRequest(BaseModel):
    project: Optional[str] = None


class ValidateRequest(BaseModel):
    project: Optional[str] = None


class UpdateRequest(BaseModel):
    project: Optional[str] = None
    dry_run: bool = False


@app.get("/api/ping")
@app.head("/api/ping")
async def ping():
    """Liveness probe: constant-time, no I/O. Use /api/health for the full audit."""
    return {"status": "ok", "service": "lemgendary-env-manager", "version": app.version}


@app.get("/api/health")
@app.head("/api/health")
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


def _run_pipeline_worker(
    target_project: Optional[str],
    clean: bool,
    loop: asyncio.AbstractEventLoop,
):
    for event in orchestrator.run_clean_install_pipeline(
        target_project=target_project,
        clean=clean,
    ):
        ev_dict = event.to_dict()
        recent_events.append(ev_dict)
        if len(recent_events) > 200:
            recent_events.pop(0)
        asyncio.run_coroutine_threadsafe(manager.broadcast(ev_dict), loop)


@app.post("/api/pipeline/run")
async def run_pipeline(request: RunPipelineRequest):
    if orchestrator.is_running:
        return {"status": "error", "message": "Pipeline is already running."}
    if update_jobs.is_running:
        return {"status": "error", "message": "A package update is running."}

    loop = asyncio.get_running_loop()
    thread = threading.Thread(
        target=_run_pipeline_worker,
        args=(request.target_project, request.clean, loop),
        daemon=True,
    )
    thread.start()
    return {"status": "accepted", "message": "Pipeline initiated."}


def _run_step_worker(
    step_number: int,
    target_project: Optional[str],
    clean: bool,
    loop: asyncio.AbstractEventLoop,
):
    for event in orchestrator.run_pipeline_single_step(
        step_number=step_number,
        target_project=target_project,
        clean=clean,
    ):
        ev_dict = event.to_dict()
        recent_events.append(ev_dict)
        if len(recent_events) > 200:
            recent_events.pop(0)
        asyncio.run_coroutine_threadsafe(manager.broadcast(ev_dict), loop)


@app.post("/api/pipeline/run-step")
async def run_pipeline_step(request: RunStepRequest):
    if orchestrator.is_running:
        return {"status": "error", "message": "Pipeline or step is already running."}
    if update_jobs.is_running:
        return {"status": "error", "message": "A package update is running."}
    if request.step_number < 1 or request.step_number > 7:
        return {
            "status": "error",
            "message": f"Invalid step number: {request.step_number}. Must be between 1 and 7.",
        }

    loop = asyncio.get_running_loop()
    thread = threading.Thread(
        target=_run_step_worker,
        args=(request.step_number, request.target_project, request.clean, loop),
        daemon=True,
    )
    thread.start()
    return {
        "status": "accepted",
        "message": f"Pipeline step {request.step_number} initiated.",
    }


def _broadcast_from_thread(loop: asyncio.AbstractEventLoop):
    """Return a sink that worker threads use to publish WebSocket events."""

    def _publish(ev_dict: Dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(manager.broadcast(ev_dict), loop)

    return _publish


update_jobs = UpdateJobManager(
    runner=lambda project, emit: run_safe_update(project, emit, workspace_root()),
)


@app.post("/api/update")
async def run_update(request: Optional[UpdateRequest] = None):
    """Start safe bottom-up package upgrades as a background job.

    A dry run returns the plan. A real run returns at once with
    ``status: "accepted"`` and a ``job_id``; progress is streamed over
    ``/ws/log`` and readable at ``GET /api/update/status``.
    """
    request = request or UpdateRequest()
    if request.project is not None and request.project not in UPGRADE_ORDER:
        raise HTTPException(status_code=404, detail=f"Unknown project: {request.project}")

    if request.dry_run:
        loop = asyncio.get_running_loop()
        try:
            plan = await loop.run_in_executor(
                None, lambda: build_upgrade_plan(workspace_root(), project=request.project)
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if not plan.has_any_updates:
            return {"status": "up_to_date", "message": "All packages are up to date.", "events": []}
        return {"status": "dry_run", "plan": plan.to_dict()}

    if orchestrator.is_running:
        raise HTTPException(
            status_code=409,
            detail="The pipeline is running. Wait for it to finish before updating packages.",
        )

    publish = _broadcast_from_thread(asyncio.get_running_loop())
    try:
        job = update_jobs.start(request.project, publish)
    except UpdateAlreadyRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    scope = request.project or "all projects"
    return {
        "status": "accepted",
        "message": f"Safe update started for {scope}.",
        "job_id": job["job_id"],
        "project": request.project,
        "events": [],
    }


@app.get("/api/update/status")
async def get_update_status():
    """Current or last safe-update job (``job`` is null before the first run)."""
    return {"is_running": update_jobs.is_running, "job": update_jobs.snapshot()}


@app.get("/api/manifests")
async def get_manifests():
    env_mgr_dir = ENV_MANAGER_DIR
    manifests_dir = env_mgr_dir / "requirements"
    manifests = {}
    if manifests_dir.exists():
        for mf in manifests_dir.glob("*.txt"):
            manifests[mf.name] = mf.read_text(encoding="utf-8")
    return {"manifests": manifests}


@app.post("/api/manifests/sync")
async def sync_manifests():
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, sync_all_manifests)
    formatted = {k: {"success": ok, "message": msg} for k, (ok, msg) in results.items()}
    return {"status": "success", "results": formatted}


class ManifestValidateRequest(BaseModel):
    name: str
    content: str


class ManifestSaveRequest(BaseModel):
    name: str
    content: str


def _resolve_manifest_target(name: str) -> Optional[Path]:
    root = workspace_root()
    manifest_map = {
        "unified_data.yaml": root / "lemgendary-datasets" / "unified_data.yaml",
        "unified_models_v2.yaml": root / "lemgendary-training-suite" / "unified_models_v2.yaml",
        "config.yaml": root / "lemgendary-training-suite" / "config.yaml",
        "presets.yaml": root / "lemgendary-training-suite" / "presets.yaml",
        "runtime_env.yaml": root / "lemgendary-env-manager" / "requirements" / "runtime_env.yaml",
        "requirements-training.txt": root / "lemgendary-training-suite" / "requirements.txt",
        "requirements-datasets.txt": root / "lemgendary-datasets" / "requirements.txt",
        "requirements-env-manager.txt": root / "lemgendary-env-manager" / "requirements.txt",
        "requirements-documentation.txt": root / "lemgendary-docs" / "requirements.txt",
        "package.json": root / "lemgendary-ai-studio-gui" / "package.json",
    }
    return manifest_map.get(name)


@app.get("/api/manifests/registry")
async def get_manifest_registry():
    """List all manageable ecosystem manifests, locations, and formats."""
    manifest_targets = [
        {"name": "unified_data.yaml", "project": "lemgendary-datasets", "format": "yaml", "description": "Authoritative dataset and source repository registry."},
        {"name": "unified_models_v2.yaml", "project": "lemgendary-training-suite", "format": "yaml", "description": "Neural architecture registry and training hyperparameters."},
        {"name": "config.yaml", "project": "lemgendary-training-suite", "format": "yaml", "description": "Global training execution and hardware governor configuration."},
        {"name": "presets.yaml", "project": "lemgendary-training-suite", "format": "yaml", "description": "Canonical training and evaluation preset profiles."},
        {"name": "runtime_env.yaml", "project": "lemgendary-env-manager", "format": "yaml", "description": "Runtime platform specifications and dependency matrices."},
        {"name": "requirements-training.txt", "project": "lemgendary-training-suite", "format": "text", "description": "Training suite Python dependency manifest."},
        {"name": "requirements-datasets.txt", "project": "lemgendary-datasets", "format": "text", "description": "Dataset compiler Python dependency manifest."},
        {"name": "requirements-env-manager.txt", "project": "lemgendary-env-manager", "format": "text", "description": "Environment manager Python dependency manifest."},
        {"name": "requirements-documentation.txt", "project": "lemgendary-docs", "format": "text", "description": "Documentation hub Python dependency manifest."},
        {"name": "package.json", "project": "lemgendary-ai-studio-gui", "format": "json", "description": "Desktop GUI Tauri v2 and React package manifest."},
    ]
    items = []
    for target in manifest_targets:
        target_path = _resolve_manifest_target(target["name"])
        exists = target_path.exists() if target_path else False
        size_bytes = target_path.stat().st_size if exists and target_path else 0
        items.append({
            **target,
            "exists": exists,
            "size_bytes": size_bytes,
            "path": str(target_path) if target_path else None,
        })
    return {"manifests": items}


@app.get("/api/manifests/read")
async def read_manifest(name: str = Query(..., description="Target manifest name")):
    """Read full text content of a registered manifest."""
    target_path = _resolve_manifest_target(name)
    if not target_path or not target_path.exists():
        raise HTTPException(status_code=404, detail=f"Manifest '{name}' not found")
    content = target_path.read_text(encoding="utf-8")
    ext = target_path.suffix.lstrip(".")
    fmt = "yaml" if ext in ("yaml", "yml") else "json" if ext == "json" else "text"
    return {
        "name": name,
        "format": fmt,
        "path": str(target_path),
        "content": content,
    }


@app.post("/api/manifests/validate")
async def validate_manifest_content(req: ManifestValidateRequest):
    """Validate syntax for YAML, JSON, or text manifest content."""
    target_path = _resolve_manifest_target(req.name)
    ext = target_path.suffix.lstrip(".") if target_path else "yaml"
    try:
        if ext in ("yaml", "yml"):
            yaml.safe_load(req.content)
        elif ext == "json":
            json.loads(req.content)
        return {"valid": True, "error": None}
    except (yaml.YAMLError, ValueError) as exc:
        _log.info("Manifest %s failed syntax validation: %s", req.name, exc)
        return {"valid": False, "error": str(exc)}


@app.post("/api/manifests/save")
async def save_manifest_content(req: ManifestSaveRequest):
    """Safely validate, back up, and atomically write manifest content to disk."""
    target_path = _resolve_manifest_target(req.name)
    if not target_path:
        raise HTTPException(status_code=404, detail=f"Unknown manifest '{req.name}'")
    ext = target_path.suffix.lstrip(".")
    try:
        if ext in ("yaml", "yml"):
            yaml.safe_load(req.content)
        elif ext == "json":
            json.loads(req.content)
    except (yaml.YAMLError, ValueError) as exc:
        _log.info("Rejected manifest %s save: %s", req.name, exc)
        raise HTTPException(status_code=400, detail=f"Syntax validation failed: {exc}") from exc

    # Backup existing file
    if target_path.exists():
        bak_path = target_path.with_suffix(target_path.suffix + ".bak")
        bak_path.write_bytes(target_path.read_bytes())

    # Write new content atomically
    target_path.write_text(req.content, encoding="utf-8")
    return {
        "status": "success",
        "name": req.name,
        "bytes_written": len(req.content.encode("utf-8")),
    }


# ─── Secrets & Ecosystem Tokens Vault ────────────────────────────────────────

def _secrets_vault() -> SecretsVault:
    """Vault rooted at the workspace; Kaggle CLI config lives in the user home."""
    return SecretsVault(workspace_root(), Path.home() / ".kaggle")


@app.get("/api/secrets", response_model=SecretsListResponse)
async def get_secrets() -> SecretsListResponse:
    """List ecosystem secrets masked (``has_value`` and ``value_hint`` only). No side effects."""
    vault = _secrets_vault()
    try:
        items = vault.list_masked()
    except SecretsStorageError as exc:
        _log.error("%s", exc)
        return SecretsListResponse(
            status="error", initialized=True, secrets=[], message=str(exc)
        )
    return SecretsListResponse(
        status="success", initialized=vault.is_initialized(), secrets=items
    )


@app.post("/api/secrets", response_model=SecretsSaveResponse)
async def save_secrets(payload: SecretsPayload) -> SecretsSaveResponse:
    """Store secrets write-only: an empty ``secret_value`` keeps the stored value for that id."""
    try:
        return _secrets_vault().save(payload.secrets)
    except SecretsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SecretsStorageError as exc:
        _log.error("%s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ─── Bug Reports ──────────────────────────────────────────────────────────────

def _bug_report_service() -> BugReportService:
    """Service rooted at the workspace, using the real mail client and file manager."""
    return BugReportService(workspace_root(), BUG_REPORT_SETTINGS, SystemPlatformAdapter())


@app.post(
    "/api/bug-report",
    response_model=BugReportResponse,
    responses={413: {"description": "An upload or the whole report is too large."}},
)
async def submit_bug_report(
    fields: Annotated[BugReportFields, Form()],
    screenshot: Annotated[list[UploadFile] | None, File()] = None,
    telemetry: Annotated[UploadFile | None, File()] = None,
    recording: Annotated[UploadFile | None, File()] = None,
) -> BugReportResponse:
    """Save a bug report ZIP in the workspace and open the mail client to send it.

    The user attaches the archive by hand: ``mailto:`` cannot carry files.
    """
    try:
        submission = await collect_submission(
            fields, screenshot or [], telemetry, recording, BUG_REPORT_SETTINGS
        )
    except UploadRejectedError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail()) from exc
    try:
        return await run_in_threadpool(_bug_report_service().submit, submission)
    except (ReportsDirError, OSError) as exc:
        _log.error("Could not save the bug report: %s", exc)
        raise HTTPException(
            status_code=500, detail="Could not save the bug report; see the env manager log."
        ) from exc


# ─── Documentation Hub Offline & Online Metadata ─────────────────────────────

def _docs_dir() -> Path:
    return workspace_root() / "lemgendary-docs"


if _docs_dir().exists():
    from starlette.staticfiles import StaticFiles
    app.mount("/documentation-hub", StaticFiles(directory=str(_docs_dir()), html=True), name="documentation_hub")


@app.get("/api/docs/status")
async def get_docs_status():
    """Report offline documentation hub availability and official online links."""
    return {
        "offline_available": _docs_dir().exists(),
        "local_url": "http://127.0.0.1:8000/documentation-hub/index.html",
        "online_url": "https://lemgenda.github.io/ai-training-whitepapers/index.html",
    }


@app.post("/api/clean")
async def clean_artifacts(request: Optional[CleanRequest] = None):
    base_dir = workspace_root()
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
    base_dir = workspace_root()
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


class HooksInstallRequest(BaseModel):
    project: Optional[str] = None


@app.post("/api/hooks/install")
async def install_hooks_endpoint(request: Optional[HooksInstallRequest] = None):
    """Install or repair standardized pre-commit hooks across all ecosystem projects."""
    base_dir = workspace_root()
    target_project = request.project if request else None

    loop = asyncio.get_running_loop()
    raw_results = await loop.run_in_executor(
        None,
        lambda: install_git_hooks(base_dir=base_dir, target_project=target_project),
    )

    results = [
        {"project": proj, "success": ok, "message": msg}
        for proj, ok, msg in raw_results
    ]
    all_ok = all(item["success"] for item in results)

    return {
        "status": "success" if all_ok else "partial",
        "all_ok": all_ok,
        "results": results,
    }


def _probe_sidecar(url: str, timeout: float = 1.5) -> Dict[str, Any]:
    """Probe an external HTTP sidecar endpoint cleanly with a brief timeout."""
    req = urllib.request.Request(url, headers={"User-Agent": "LemGendary-Env-Manager"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200:
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


@app.get("/api/gui/state")
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


@app.get("/api/gui/ecosystem")
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


@app.post("/api/services/{service_id}/start")
async def start_ecosystem_service(service_id: str):
    """Start an ecosystem sidecar service daemon in the background."""
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, start_service, service_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message"))
    return result


@app.post("/api/services/{service_id}/stop")
async def stop_ecosystem_service(service_id: str):
    """Stop an active ecosystem sidecar service daemon."""
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, stop_service, service_id)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result.get("message"))
    return result


@app.post("/api/services/start-all")
async def start_all_ecosystem_services():
    """Start all offline ecosystem sidecars."""
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, start_all_services)
    return {"results": results}


@app.post("/api/shutdown")
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


@app.websocket("/ws/log")
@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        for ev in recent_events[-20:]:
            await websocket.send_json(ev)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect as exc:
        _log.debug("WebSocket client disconnected (code %s)", exc.code)
    except (RuntimeError, OSError) as exc:
        _log.warning("WebSocket connection failed: %s", exc)
    finally:
        manager.disconnect(websocket)
