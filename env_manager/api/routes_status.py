"""Read-only status routes: ping, health, drift, hardware, projects and npm."""

import asyncio
from typing import Any

from fastapi import (
    APIRouter,
    Query,
)

from env_manager import __version__
from env_manager.health_checker import run_full_health_audit
from env_manager.npm_manager import audit_npm_workspaces
from env_manager.system_probe import probe_hardware
from env_manager.venv_manager import discover_projects

router = APIRouter()


@router.get("/api/ping")
@router.head("/api/ping")
async def ping():
    """Liveness probe: constant-time, no I/O. Use /api/health for the full audit."""
    return {"status": "ok", "service": "lemgendary-env-manager", "version": __version__}


@router.get("/api/health")
@router.head("/api/health")
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


@router.get("/api/drift")
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

    python_drift: list[dict[str, Any]] = []
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

    coverage: list[dict[str, Any]] = [c.to_dict() for c in report.manifest_coverage]
    single_manifest: list[dict[str, Any]] = [
        s.to_dict() for s in report.single_manifest_packages
    ]

    npm_drift: list[dict[str, Any]] = []
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


@router.get("/api/hardware")
async def get_hardware():
    """Return the host hardware profile."""
    loop = asyncio.get_running_loop()
    profile = await loop.run_in_executor(None, probe_hardware)
    return profile.to_dict()


@router.get("/api/projects")
async def get_projects():
    """Return every discovered project with its venv state."""
    loop = asyncio.get_running_loop()
    projects = await loop.run_in_executor(None, discover_projects)
    return [p.to_dict() for p in projects]


@router.get("/api/npm")
async def get_npm():
    """Return the npm package status of each Node workspace."""
    loop = asyncio.get_running_loop()
    npm_statuses = await loop.run_in_executor(None, audit_npm_workspaces)
    return [s.to_dict() for s in npm_statuses]
