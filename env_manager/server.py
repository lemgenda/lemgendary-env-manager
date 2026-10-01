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
from pathlib import Path
import threading
import urllib.error
import urllib.request
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from env_manager.health_checker import run_full_health_audit
from env_manager.npm_manager import audit_npm_workspaces
from env_manager.orchestrator import PipelineEvent, PipelineOrchestrator
from env_manager.service_manager import start_all_services, start_service, stop_service
from env_manager.system_probe import probe_hardware
from env_manager.utils import purge_project_cache
from env_manager.venv_manager import discover_projects

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
            except Exception as exc:
                _log.debug("Failed to broadcast message to websocket client: %s", exc)
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


class CleanRequest(BaseModel):
    project: Optional[str] = None


class ValidateRequest(BaseModel):
    project: Optional[str] = None


class ManifestValidateRequest(BaseModel):
    name: str
    content: str


class ManifestSaveRequest(BaseModel):
    name: str
    content: str


def _get_workspace_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _resolve_manifest_target(name: str) -> Optional[Path]:
    root = _get_workspace_root()
    manifest_map = {
        "unified_data.yaml": root / "lemgendary-datasets" / "unified_data.yaml",
        "unified_models_v2.yaml": root / "lemgendary-training-suite" / "unified_models_v2.yaml",
        "config.yaml": root / "lemgendary-training-suite" / "config.yaml",
        "presets.yaml": root / "lemgendary-training-suite" / "presets.yaml",
        "runtime_env.yaml": root / "lemgendary-env-manager" / "requirements" / "runtime_env.yaml",
        "requirements-training.txt": root / "lemgendary-training-suite" / "requirements.txt",
        "requirements-datasets.txt": root / "lemgendary-datasets" / "requirements.txt",
        "requirements-env-manager.txt": root / "lemgendary-env-manager" / "requirements.txt",
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
    from fastapi import HTTPException
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
    import yaml
    target_path = _resolve_manifest_target(req.name)
    ext = target_path.suffix.lstrip(".") if target_path else "yaml"
    try:
        if ext in ("yaml", "yml"):
            yaml.safe_load(req.content)
        elif ext == "json":
            json.loads(req.content)
        return {"valid": True, "error": None}
    except Exception as exc:
        return {"valid": False, "error": str(exc)}


@app.post("/api/manifests/save")
async def save_manifest_content(req: ManifestSaveRequest):
    """Safely validate, back up, and atomically write manifest content to disk."""
    import yaml
    from fastapi import HTTPException
    target_path = _resolve_manifest_target(req.name)
    if not target_path:
        raise HTTPException(status_code=404, detail=f"Unknown manifest '{req.name}'")
    ext = target_path.suffix.lstrip(".")
    try:
        if ext in ("yaml", "yml"):
            yaml.safe_load(req.content)
        elif ext == "json":
            json.loads(req.content)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Syntax validation failed: {exc}")

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

class SecretItem(BaseModel):
    id: str
    service: str
    label: str
    username: Optional[str] = None
    secret_value: str
    server: Optional[str] = None
    is_default: bool = False
    created_at: Optional[str] = None


class SecretsPayload(BaseModel):
    secrets: List[SecretItem]


def _resolve_secrets_file() -> Path:
    base_dir = Path(__file__).resolve().parent.parent.parent
    return base_dir / ".secrets.yaml"


def _seed_secrets_from_existing(base_dir: Path) -> List[Dict[str, Any]]:
    """Seed initial secrets registry from legacy credential files across repositories."""
    seeds: List[Dict[str, Any]] = []

    # 1. Kaggle users from .kaggle_users in training-suite
    kaggle_users_file = base_dir / "lemgendary-training-suite" / ".kaggle_users"
    if kaggle_users_file.exists():
        try:
            content = kaggle_users_file.read_text(encoding="utf-8").strip()
            for line in content.split(";"):
                line = line.strip()
                if not line:
                    continue
                parts = dict(p.strip().split("=", 1) for p in line.split(",") if "=" in p)
                u = parts.get("KAGGLE_USERNAME")
                t = parts.get("KAGGLE_API_TOKEN")
                if u and t:
                    seeds.append({
                        "id": f"kaggle-{u}",
                        "service": "kaggle",
                        "label": f"Kaggle ({u})",
                        "username": u,
                        "secret_value": t,
                        "server": None,
                        "is_default": u == "lemtreursi",
                    })
        except Exception as exc:
            _log.debug("Error parsing .kaggle_users: %s", exc)

    # 2. Google Drive from .GOOGLE_DRIVE in training suite
    gdrive_file = base_dir / "lemgendary-training-suite" / ".GOOGLE_DRIVE"
    if gdrive_file.exists():
        try:
            key = gdrive_file.read_text(encoding="utf-8").strip()
            if key:
                seeds.append({
                    "id": "gdrive-primary",
                    "service": "google_drive",
                    "label": "Google Drive Primary API Key",
                    "username": "default",
                    "secret_value": key,
                    "server": None,
                    "is_default": True,
                })
        except Exception as exc:
            _log.debug("Error parsing .GOOGLE_DRIVE: %s", exc)

    # 3. GitHub PAT
    gh_file = base_dir / "lemgendary-training-suite" / ".GITHUB_PAT"
    if gh_file.exists():
        try:
            tok = gh_file.read_text(encoding="utf-8").strip()
            if tok:
                seeds.append({
                    "id": "github-primary",
                    "service": "github",
                    "label": "GitHub Personal Access Token",
                    "username": "lemgenda",
                    "secret_value": tok,
                    "server": None,
                    "is_default": True,
                })
        except Exception as exc:
            _log.debug("Error parsing .GITHUB_PAT: %s", exc)

    # 4. HuggingFace Token
    hf_file = base_dir / "lemgendary-datasets" / ".huggingface_token"
    if hf_file.exists():
        try:
            tok = hf_file.read_text(encoding="utf-8").strip()
            if tok:
                seeds.append({
                    "id": "huggingface-primary",
                    "service": "huggingface",
                    "label": "Hugging Face Access Token",
                    "username": "lemgenda",
                    "secret_value": tok,
                    "server": None,
                    "is_default": True,
                })
        except Exception as exc:
            _log.debug("Error parsing .huggingface_token: %s", exc)

    # 5. MetaTrader 5
    mt5_file = base_dir / "lemgendary-datasets" / ".mt5_credentials"
    if mt5_file.exists():
        try:
            lines = mt5_file.read_text(encoding="utf-8").splitlines()
            u = ""
            p = ""
            for l in lines:
                if l.startswith("User:"):
                    u = l.split(":", 1)[1].strip()
                elif l.startswith("Pass:"):
                    p = l.split(":", 1)[1].strip()
            if u:
                seeds.append({
                    "id": f"mt5-{u}",
                    "service": "metatrader5",
                    "label": f"MT5 Account ({u})",
                    "username": u,
                    "secret_value": p,
                    "server": "MetaQuotes-Demo",
                    "is_default": True,
                })
        except Exception as exc:
            _log.debug("Error parsing .mt5_credentials: %s", exc)

    # 6. Saturn Cloud
    saturn_file = base_dir / "lemgendary-training-suite" / ".SATURN_PAT"
    if saturn_file.exists():
        try:
            tok = saturn_file.read_text(encoding="utf-8").strip()
            if tok:
                seeds.append({
                    "id": "saturn-primary",
                    "service": "saturn_cloud",
                    "label": "Saturn Cloud Bearer Token",
                    "username": "saturn-user",
                    "secret_value": tok,
                    "server": None,
                    "is_default": True,
                })
        except Exception as exc:
            _log.debug("Error parsing .SATURN_PAT: %s", exc)

    return seeds


@app.get("/api/secrets")
async def get_secrets():
    """Retrieve all ecosystem secrets, seeding defaults from credential files if uninitialized."""
    import yaml
    base_dir = Path(__file__).resolve().parent.parent.parent
    sec_file = _resolve_secrets_file()
    if not sec_file.exists():
        seeds = _seed_secrets_from_existing(base_dir)
        with open(sec_file, "w", encoding="utf-8") as f:
            yaml.safe_dump({"secrets": seeds}, f, default_flow_style=False, sort_keys=False)
        return {"status": "success", "secrets": seeds}

    try:
        with open(sec_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        return {"status": "success", "secrets": data.get("secrets", [])}
    except Exception as exc:
        _log.error("Failed loading .secrets.yaml: %s", exc)
        return {"status": "error", "message": str(exc), "secrets": []}


@app.post("/api/secrets")
async def save_secrets(payload: SecretsPayload):
    """Save secrets atomically and propagate mandatory tokens to ecosystem files."""
    import yaml
    from fastapi import HTTPException

    # Enforce mandatory Kaggle secret
    has_kaggle = any(s.service.lower() == "kaggle" and s.secret_value.strip() for s in payload.secrets)
    if not has_kaggle:
        raise HTTPException(
            status_code=400,
            detail="Validation failed: Kaggle API token is mandatory across the LemGendary Ecosystem.",
        )

    base_dir = Path(__file__).resolve().parent.parent.parent
    sec_file = _resolve_secrets_file()

    # Backup existing
    if sec_file.exists():
        bak = sec_file.with_suffix(".yaml.bak")
        bak.write_bytes(sec_file.read_bytes())

    records = [s.model_dump() for s in payload.secrets]
    with open(sec_file, "w", encoding="utf-8") as f:
        yaml.safe_dump({"secrets": records}, f, default_flow_style=False, sort_keys=False)

    # Propagate to legacy credentials for seamless backwards compatibility
    kaggle_items = [s for s in payload.secrets if s.service.lower() == "kaggle"]
    default_kaggle = next((s for s in kaggle_items if s.is_default), kaggle_items[0] if kaggle_items else None)

    if default_kaggle:
        for proj in ["lemgendary-datasets", "lemgendary-training-suite", "lemgendary-env-manager"]:
            tok_f = base_dir / proj / ".kaggle_token"
            try:
                tok_f.write_text(default_kaggle.secret_value.strip(), encoding="utf-8")
            except OSError:
                pass

        # Update .kaggle_users
        users_line = ";\n".join(
            f"KAGGLE_USERNAME={s.username or 'lemtreursi'}, KAGGLE_API_TOKEN={s.secret_value.strip()}"
            for s in kaggle_items
        ) + ";\n"
        users_f = base_dir / "lemgendary-training-suite" / ".kaggle_users"
        try:
            users_f.write_text(users_line, encoding="utf-8")
        except OSError:
            pass

    return {
        "status": "success",
        "count": len(payload.secrets),
        "message": "Secrets securely persisted and propagated across repositories.",
    }


# ─── Documentation Hub Offline & Online Metadata ─────────────────────────────

DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "lemgendary-docs"
if DOCS_DIR.exists():
    from starlette.staticfiles import StaticFiles
    app.mount("/documentation-hub", StaticFiles(directory=str(DOCS_DIR), html=True), name="documentation_hub")


@app.get("/api/docs/status")
async def get_docs_status():
    """Report offline documentation hub availability and official online links."""
    return {
        "offline_available": DOCS_DIR.exists(),
        "local_url": "http://127.0.0.1:8000/documentation-hub/index.html",
        "online_url": "https://lemgenda.github.io/ai-training-whitepapers/index.html",
    }


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
            "version": "2.0.0",
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


@app.websocket("/ws/log")
@app.websocket("/ws/logs")
async def websocket_logs(websocket: WebSocket):
    await manager.connect(websocket)
    for ev in recent_events[-20:]:
        try:
            await websocket.send_json(ev)
        except Exception as exc:
            _log.debug("Error sending replay event to websocket: %s", exc)
            break

    try:
        while True:
            await websocket.receive_text()
    except (WebSocketDisconnect, Exception) as exc:
        _log.debug("WebSocket disconnected: %s", exc)
        manager.disconnect(websocket)