"""Browser-origin guard for the Environment Manager sidecar.

Only the LemGendary AI Studio GUI (the Tauri window or the Vite dev server)
may call this service from a browser. Any other web page open on the same
machine is refused, so it cannot read secrets or start destructive jobs.

Rules:

- A request whose ``Origin`` header is not in ``ALLOWED_ORIGINS`` is refused.
- A browser request (one that carries ``Origin``) using a state-changing
  method must also send ``CLIENT_HEADER: CLIENT_HEADER_VALUE``. A custom
  header forces a CORS preflight, which blocks cross-site form posts.
- Requests without ``Origin`` (CLI, scripts, tests) are allowed.
- WebSocket handshakes follow the same origin rule.

This module is the single place in this repository that defines these values.
"""

from __future__ import annotations

import logging
from typing import Final

from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_log = logging.getLogger("env_manager.security")

ALLOWED_ORIGINS: Final[tuple[str, ...]] = (
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
    "http://localhost:1420",
    "http://127.0.0.1:1420",
)

CLIENT_HEADER: Final[str] = "X-LemGendary-Client"
CLIENT_HEADER_VALUE: Final[str] = "ai-studio-gui"

ALLOWED_HEADERS: Final[tuple[str, ...]] = ("Content-Type", CLIENT_HEADER)
ALLOWED_METHODS: Final[tuple[str, ...]] = (
    "GET",
    "HEAD",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
)
STATE_CHANGING_METHODS: Final[frozenset[str]] = frozenset(
    {"POST", "PUT", "PATCH", "DELETE"}
)

# RFC 6455 close code for "policy violation".
_WS_POLICY_VIOLATION: Final[int] = 1008


def _header(scope: Scope, name: bytes) -> str | None:
    """Return the first value of header ``name`` (lower-case bytes), if any."""
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


def rejection_reason(
    scope: Scope, allowed_origins: tuple[str, ...] = ALLOWED_ORIGINS
) -> str | None:
    """Return why a request must be refused, or ``None`` if it is allowed."""
    origin = _header(scope, b"origin")
    if origin is None:
        return None
    if origin not in allowed_origins:
        return f"Origin '{origin}' is not allowed."
    if scope["type"] != "http":
        return None
    method = str(scope.get("method", "")).upper()
    if method not in STATE_CHANGING_METHODS:
        return None
    client = _header(scope, CLIENT_HEADER.lower().encode("latin-1"))
    if client != CLIENT_HEADER_VALUE:
        return f"Missing or invalid {CLIENT_HEADER} header."
    return None


def origin_guard_middleware(
    app: ASGIApp, allowed_origins: tuple[str, ...] = ALLOWED_ORIGINS
) -> ASGIApp:
    """Wrap ``app`` in an ASGI middleware that enforces the rules above.

    Use with ``FastAPI.add_middleware(origin_guard_middleware)``.
    """

    async def guard(scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await app(scope, receive, send)
            return

        reason = rejection_reason(scope, allowed_origins)
        if reason is None:
            await app(scope, receive, send)
            return

        _log.warning(
            "Refused %s %s: %s",
            scope.get("method", "WEBSOCKET"),
            scope.get("path", ""),
            reason,
        )
        if scope["type"] == "http":
            response = JSONResponse({"detail": reason}, status_code=403)
            await response(scope, receive, send)
            return

        # Read the connect event, then close before accepting: the server
        # answers the handshake with HTTP 403.
        await receive()
        await send({"type": "websocket.close", "code": _WS_POLICY_VIOLATION})

    return guard


def install_browser_guard(app: Starlette) -> None:
    """Add the closed CORS allow-list and, outermost, the origin guard to ``app``.

    The guard is added last, so it runs first: it refuses foreign origins
    before CORS or routing see the request.
    """
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(ALLOWED_ORIGINS),
        allow_credentials=False,
        allow_methods=list(ALLOWED_METHODS),
        allow_headers=list(ALLOWED_HEADERS),
    )
    app.add_middleware(origin_guard_middleware)
