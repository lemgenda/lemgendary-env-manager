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

This module builds the FastAPI app (lifespan, CORS and origin-guard
middleware, the documentation-hub mount) and includes one router per area
from ``env_manager.api``; shared state lives in ``env_manager.api.state``.

``/docs`` on the env-manager service (host and port in
``ecosystem/ecosystem.json``) opens Swagger UI, which lets you click through
every endpoint manually.
"""

import asyncio
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import (
    FastAPI,
)

from env_manager import __version__
from env_manager._logging import get_logger
from env_manager.api import (
    routes_ecosystem,
    routes_manifests,
    routes_pipeline,
    routes_status,
    routes_workspace,
)
from env_manager.api.routes_workspace import docs_dir
from env_manager.security import install_browser_guard
from env_manager.service_manager import start_all_services

_log = get_logger(__name__)


def _log_sidecar_startup(future: "asyncio.Future[dict[str, Any]]") -> None:
    """Log the outcome of the startup sidecar launch, including any exception."""
    if future.cancelled():
        _log.info("Sidecar startup was cancelled.")
        return
    exc = future.exception()
    if exc is not None:
        _log.error("Starting the ecosystem sidecars failed: %s", exc, exc_info=exc)
        return
    for service_id, outcome in future.result().items():
        _log.info("Sidecar %s at startup: %s", service_id, outcome)


DISABLE_SIDECAR_AUTORUN_ENV = "LEM_DISABLE_SIDECAR_AUTORUN"
"""Set to '1' or 'true' to disable automatic sidecar startup in server lifespan (e.g. tests)."""


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    """Start the ecosystem sidecars in the background; their outcome is logged."""
    disable_autorun = os.getenv(DISABLE_SIDECAR_AUTORUN_ENV, "").strip().lower() in ("1", "true")
    if not disable_autorun:
        loop = asyncio.get_running_loop()
        startup = loop.run_in_executor(None, start_all_services)
        startup.add_done_callback(_log_sidecar_startup)
    yield


app = FastAPI(
    title="LemGendary Environment Manager API",
    version=__version__,
    description="REST and WebSocket sidecar service for LemGendary AI Studio.",
    lifespan=lifespan,
)


install_browser_guard(app)


if docs_dir().exists():
    from starlette.staticfiles import StaticFiles
    app.mount(
        "/documentation-hub",
        StaticFiles(directory=str(docs_dir()), html=True),
        name="documentation_hub",
    )


for _router in (
    routes_status.router,
    routes_pipeline.router,
    routes_manifests.router,
    routes_workspace.router,
    routes_ecosystem.router,
):
    app.include_router(_router)
