"""Manifest routes: list, sync, read, validate and save the ecosystem manifests."""

import asyncio
import json
from pathlib import Path

import yaml
from fastapi import (
    APIRouter,
    HTTPException,
    Query,
)
from pydantic import BaseModel

from env_manager._logging import get_logger
from env_manager.requirements_manager import sync_all_manifests
from env_manager.workspace import ENV_MANAGER_DIR, workspace_root

router = APIRouter()
_log = get_logger(__name__)


@router.get("/api/manifests")
async def get_manifests():
    """Return the text of each central requirements manifest by file name."""
    env_mgr_dir = ENV_MANAGER_DIR
    manifests_dir = env_mgr_dir / "requirements"
    manifests = {}
    if manifests_dir.exists():
        for mf in manifests_dir.glob("*.txt"):
            manifests[mf.name] = mf.read_text(encoding="utf-8")
    return {"manifests": manifests}


@router.post("/api/manifests/sync")
async def sync_manifests():
    """Copy the central manifests into the sibling projects."""
    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, sync_all_manifests)
    formatted = {k: {"success": ok, "message": msg} for k, (ok, msg) in results.items()}
    return {"status": "success", "results": formatted}


class ManifestValidateRequest(BaseModel):
    """Body of ``POST /api/manifests/validate``."""
    name: str
    content: str


class ManifestSaveRequest(BaseModel):
    """Body of ``POST /api/manifests/save``."""
    name: str
    content: str


def _resolve_manifest_target(name: str) -> Path | None:
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


@router.get("/api/manifests/registry")
async def get_manifest_registry():
    """List all manageable ecosystem manifests, locations, and formats."""
    manifest_targets = [
        {
            "name": "unified_data.yaml", "project": "lemgendary-datasets",
            "format": "yaml",
            "description": "Authoritative dataset and source repository registry.",
        },
        {
            "name": "unified_models_v2.yaml", "project": "lemgendary-training-suite",
            "format": "yaml",
            "description": "Neural architecture registry and training hyperparameters.",
        },
        {
            "name": "config.yaml", "project": "lemgendary-training-suite",
            "format": "yaml",
            "description": "Global training execution and hardware governor configuration.",
        },
        {
            "name": "presets.yaml", "project": "lemgendary-training-suite",
            "format": "yaml",
            "description": "Canonical training and evaluation preset profiles.",
        },
        {
            "name": "runtime_env.yaml", "project": "lemgendary-env-manager",
            "format": "yaml",
            "description": "Runtime platform specifications and dependency matrices.",
        },
        {
            "name": "requirements-training.txt", "project": "lemgendary-training-suite",
            "format": "text",
            "description": "Training suite Python dependency manifest.",
        },
        {
            "name": "requirements-datasets.txt", "project": "lemgendary-datasets",
            "format": "text",
            "description": "Dataset compiler Python dependency manifest.",
        },
        {
            "name": "requirements-env-manager.txt", "project": "lemgendary-env-manager",
            "format": "text",
            "description": "Environment manager Python dependency manifest.",
        },
        {
            "name": "requirements-documentation.txt", "project": "lemgendary-docs",
            "format": "text",
            "description": "Documentation hub Python dependency manifest.",
        },
        {
            "name": "package.json", "project": "lemgendary-ai-studio-gui",
            "format": "json",
            "description": "Desktop GUI Tauri v2 and React package manifest.",
        },
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


@router.get("/api/manifests/read")
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


@router.post("/api/manifests/validate")
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


@router.post("/api/manifests/save")
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
