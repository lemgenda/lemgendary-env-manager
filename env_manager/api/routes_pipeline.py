"""Pipeline and safe-update routes: start runs on worker threads and report their status."""

import asyncio
import threading

from fastapi import (
    APIRouter,
    HTTPException,
)
from pydantic import BaseModel

from env_manager.api import state
from env_manager.api.state import (
    _broadcast_from_thread,
    _operation_lock,
    _publish_pipeline_event,
    orchestrator,
    recent_events_snapshot,
)
from env_manager.update_jobs import UpdateAlreadyRunningError
from env_manager.updater import UPGRADE_ORDER, build_upgrade_plan
from env_manager.workspace import workspace_root

router = APIRouter()


class RunPipelineRequest(BaseModel):
    """Body of ``POST /api/pipeline/run``."""
    target_project: str | None = None
    # Destructive opt-in: deletes every venv and node_modules and stops
    # processes running from those venvs. Omitted means a safe run.
    clean: bool = False


class RunStepRequest(BaseModel):
    """Body of ``POST /api/pipeline/run-step``."""
    step_number: int
    target_project: str | None = None
    clean: bool = False


class UpdateRequest(BaseModel):
    """Body of ``POST /api/update``."""
    project: str | None = None
    dry_run: bool = False


@router.get("/api/pipeline/status")
async def get_pipeline_status():
    """Return the orchestrator state and the most recent pipeline events."""
    return {
        "is_running": orchestrator.is_running,
        "last_status": orchestrator.last_status,
        "last_run_timestamp": orchestrator.last_run_timestamp,
        "recent_events": recent_events_snapshot(50),
    }


def _run_pipeline_worker(
    target_project: str | None,
    clean: bool,
    loop: asyncio.AbstractEventLoop,
):
    try:
        for event in orchestrator.run_clean_install_pipeline(
            target_project=target_project,
            clean=clean,
        ):
            _publish_pipeline_event(event.to_dict(), loop)
    finally:
        orchestrator.release()


def _claim_pipeline(busy_message: str) -> str | None:
    """Claim the orchestrator for a new run; return why it cannot start, or None."""
    with _operation_lock:
        if state.update_jobs.is_running:
            return "A package update is running."
        if not orchestrator.try_claim():
            return busy_message
    return None


@router.post("/api/pipeline/run")
async def run_pipeline(request: RunPipelineRequest):
    """Start the full pipeline on a background thread."""
    refusal = _claim_pipeline("Pipeline is already running.")
    if refusal is not None:
        return {"status": "error", "message": refusal}

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
    target_project: str | None,
    clean: bool,
    loop: asyncio.AbstractEventLoop,
):
    try:
        for event in orchestrator.run_pipeline_single_step(
            step_number=step_number,
            target_project=target_project,
            clean=clean,
        ):
            _publish_pipeline_event(event.to_dict(), loop)
    finally:
        orchestrator.release()


@router.post("/api/pipeline/run-step")
async def run_pipeline_step(request: RunStepRequest):
    """Run one pipeline step on a background thread."""
    if not 1 <= request.step_number <= orchestrator.total_steps:
        return {
            "status": "error",
            "message": f"Invalid step number: {request.step_number}. Must be between 1 and 7.",
        }
    refusal = _claim_pipeline("Pipeline or step is already running.")
    if refusal is not None:
        return {"status": "error", "message": refusal}

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


@router.post("/api/update")
async def run_update(request: UpdateRequest | None = None):
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

    publish = _broadcast_from_thread(asyncio.get_running_loop())
    with _operation_lock:
        if orchestrator.is_running:
            raise HTTPException(
                status_code=409,
                detail="The pipeline is running. Wait for it to finish before updating packages.",
            )
        try:
            job = state.update_jobs.start(request.project, publish)
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


@router.get("/api/update/status")
async def get_update_status():
    """Current or last safe-update job (``job`` is null before the first run)."""
    return {"is_running": state.update_jobs.is_running, "job": state.update_jobs.snapshot()}
