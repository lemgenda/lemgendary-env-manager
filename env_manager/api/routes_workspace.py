"""Workspace routes: secrets, bug reports, docs status, clean, validate and git hooks."""

import asyncio
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from env_manager._logging import get_logger
from env_manager.bug_report import (
    DEFAULT_SETTINGS as BUG_REPORT_SETTINGS,
)
from env_manager.bug_report import (
    BugReportFields,
    BugReportResponse,
    BugReportService,
    ReportsDirError,
    SystemPlatformAdapter,
    UploadRejectedError,
    collect_submission,
)
from env_manager.hooks import install_git_hooks
from env_manager.secrets_vault import (
    SecretsListResponse,
    SecretsPayload,
    SecretsSaveResponse,
    SecretsStorageError,
    SecretsValidationError,
    SecretsVault,
)
from env_manager.utils import purge_workspace_caches
from env_manager.validator import validate_workspace
from env_manager.venv_manager import discover_projects
from env_manager.workspace import workspace_root

router = APIRouter()
_log = get_logger(__name__)


class CleanRequest(BaseModel):
    """Body of ``POST /api/clean``; no project means every project."""
    project: str | None = None


class ValidateRequest(BaseModel):
    """Body of ``POST /api/validate``; no project means every project."""
    project: str | None = None


def _secrets_vault() -> SecretsVault:
    """Vault rooted at the workspace; Kaggle CLI config lives in the user home."""
    return SecretsVault(workspace_root(), Path.home() / ".kaggle")


@router.get("/api/secrets", response_model=SecretsListResponse)
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


@router.post("/api/secrets", response_model=SecretsSaveResponse)
async def save_secrets(payload: SecretsPayload) -> SecretsSaveResponse:
    """Store secrets write-only: an empty ``secret_value`` keeps the stored value for that id."""
    try:
        return _secrets_vault().save(payload.secrets)
    except SecretsValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except SecretsStorageError as exc:
        _log.error("%s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def _bug_report_service() -> BugReportService:
    """Service rooted at the workspace, using the real mail client and file manager."""
    return BugReportService(workspace_root(), BUG_REPORT_SETTINGS, SystemPlatformAdapter())


@router.post(
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


def docs_dir() -> Path:
    return workspace_root() / "lemgendary-docs"


@router.get("/api/docs/status")
async def get_docs_status():
    """Report offline documentation hub availability and official online links."""
    return {
        "offline_available": docs_dir().exists(),
        "local_url": "http://127.0.0.1:8000/documentation-hub/index.html",
        "online_url": "https://lemgenda.github.io/ai-training-whitepapers/index.html",
    }


@router.post("/api/clean")
async def clean_artifacts(request: CleanRequest | None = None):
    """Purge bytecode caches and build artifacts; report the space reclaimed."""
    target_name = request.project if request else None
    cleaned_count, total_reclaimed = await run_in_threadpool(
        lambda: purge_workspace_caches(discover_projects(workspace_root()), target_name)
    )

    return {
        "status": "success",
        "cleaned_count": cleaned_count,
        "reclaimed_bytes": total_reclaimed,
        "reclaimed_mb": round(total_reclaimed / (1024 * 1024), 2),
    }


@router.post("/api/validate")
async def validate_codebase(request: ValidateRequest | None = None):
    """Run the full validation suite on one or every project."""
    target_name = request.project if request else None
    reports = await run_in_threadpool(validate_workspace, workspace_root(), target_name)
    results = {name: rep.to_dict() for name, rep in reports.items()}
    all_passed = all(rep.passed for rep in reports.values())

    return {
        "status": "success" if all_passed else "failed",
        "all_passed": all_passed,
        "projects": results,
    }


class HooksInstallRequest(BaseModel):
    """Body of ``POST /api/hooks/install``; no project means every project."""
    project: str | None = None


@router.post("/api/hooks/install")
async def install_hooks_endpoint(request: HooksInstallRequest | None = None):
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
