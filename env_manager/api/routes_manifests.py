"""Manifest routes: list, sync, read, validate and save the ecosystem manifests."""

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import yaml
from fastapi import (
    APIRouter,
    HTTPException,
    Query,
)
from pydantic import BaseModel

from env_manager._logging import get_logger
from env_manager.ecosystem import SELF_SERVICE_ID, ecosystem
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


@dataclass(frozen=True)
class ManifestTarget:
    """An editable manifest: its file name, owning project and project-relative path."""

    name: str
    project: str
    relative_path: str
    format: str
    description: str

    def describe(self) -> dict[str, str]:
        """The public fields listed by ``/api/manifests/registry``."""
        return {
            "name": self.name, "project": self.project,
            "format": self.format, "description": self.description,
        }


_SERVICE_MANIFESTS: tuple[tuple[str, str, str, str], ...] = (
    ("dataset-compiler", "unified_data.yaml", "unified_data.yaml",
     "Authoritative dataset and source repository registry."),
    ("training-suite", "unified_models_v2.yaml", "unified_models_v2.yaml",
     "Neural architecture registry and training hyperparameters."),
    ("training-suite", "config.yaml", "config.yaml",
     "Global training execution and hardware governor configuration."),
    ("training-suite", "presets.yaml", "presets.yaml",
     "Canonical training and evaluation preset profiles."),
    (SELF_SERVICE_ID, "runtime_env.yaml", "requirements/runtime_env.yaml",
     "Runtime platform specifications and dependency matrices."),
)
"""Registries and configs kept by one service's project: ``(service id, name, path, text)``."""


def manifest_targets() -> list[ManifestTarget]:
    """Every editable manifest; project folders and manifest names come from ``ecosystem.json``."""
    eco = ecosystem()
    targets = [
        ManifestTarget(
            name, eco.require_service(service_id).project, path,
            "yaml", description,
        )
        for service_id, name, path, description in _SERVICE_MANIFESTS
    ]
    targets.extend(
        ManifestTarget(
            p.requirements_manifest, p.folder, "requirements.txt", "text",
            f"{p.display_name} Python dependency manifest.",
        )
        for p in eco.projects if p.requirements_manifest
    )
    targets.extend(
        ManifestTarget(
            "package.json", p.folder, "package.json", "json",
            f"{p.display_name} npm package manifest.",
        )
        for p in eco.projects if p.is_node
    )
    return targets


def _resolve_manifest_target(name: str) -> Path | None:
    for target in manifest_targets():
        if target.name == name:
            return workspace_root() / target.project / target.relative_path
    return None


@router.get("/api/manifests/registry")
async def get_manifest_registry():
    """List all manageable ecosystem manifests, locations, and formats."""
    items = []
    root = workspace_root()
    for target in manifest_targets():
        target_path = root / target.project / target.relative_path
        exists = target_path.exists()
        items.append({
            **target.describe(),
            "exists": exists,
            "size_bytes": target_path.stat().st_size if exists else 0,
            "path": str(target_path),
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
