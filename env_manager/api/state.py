"""Process-wide server state: WebSocket clients, recent events, orchestrator, update jobs."""

import asyncio
import threading
from collections import deque
from datetime import UTC, datetime
from typing import Any

from fastapi import (
    WebSocket,
    WebSocketDisconnect,
)

from env_manager._logging import get_logger
from env_manager.orchestrator import PipelineOrchestrator
from env_manager.update_jobs import UpdateJobManager, run_safe_update
from env_manager.workspace import workspace_root

_log = get_logger(__name__)


# Failures a WebSocket send/receive raises once the peer has gone away:
# starlette's disconnect signal, RuntimeError for a closed socket, and
# OSError (uvicorn's ClientDisconnected) for a dropped transport.
_WEBSOCKET_GONE_ERRORS = (WebSocketDisconnect, RuntimeError, OSError)


class ConnectionManager:
    """Tracks open ``/ws/log`` WebSocket clients and broadcasts events to them."""
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        """Accept the WebSocket and start broadcasting to it."""
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        """Stop broadcasting to a closed WebSocket."""
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict[str, Any]):
        """Send ``message`` to every client; drop clients whose socket is gone."""
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except _WEBSOCKET_GONE_ERRORS as exc:
                _log.info("Dropping websocket client after failed broadcast: %s", exc)
                self.disconnect(connection)


manager = ConnectionManager()


MAX_RECENT_EVENTS = 200
"""Pipeline events kept in memory for status and WebSocket replay."""


_events_lock = threading.Lock()


_operation_lock = threading.Lock()
"""Held while checking and claiming the pipeline or the update job, so the two never overlap."""


def stamp_event(ev_dict: dict[str, Any]) -> dict[str, Any]:
    """Return ``ev_dict`` with a ``timestamp``, adding the current UTC time if it has none."""
    if ev_dict.get("timestamp"):
        return ev_dict
    return {**ev_dict, "timestamp": datetime.now(UTC).isoformat()}


def record_event(ev_dict: dict[str, Any]) -> dict[str, Any]:
    """Add an event to ``recent_events``; every stored entry carries a ``timestamp``.

    Returns the stored dict, so the same timestamp is broadcast and replayed.
    """
    stamped = stamp_event(ev_dict)
    with _events_lock:
        recent_events.append(stamped)
    return stamped


def recent_events_snapshot(limit: int) -> list[dict[str, Any]]:
    """Return the newest ``limit`` recorded events, oldest first."""
    with _events_lock:
        return list(recent_events)[-limit:]


def _publish_pipeline_event(ev_dict: dict[str, Any], loop: asyncio.AbstractEventLoop) -> None:
    """Record a pipeline event and broadcast it to WebSocket clients."""
    stamped = record_event(ev_dict)
    asyncio.run_coroutine_threadsafe(manager.broadcast(stamped), loop)


orchestrator = PipelineOrchestrator()


def _broadcast_from_thread(loop: asyncio.AbstractEventLoop):
    """Return a sink that worker threads use to publish WebSocket events."""

    def _publish(ev_dict: dict[str, Any]) -> None:
        asyncio.run_coroutine_threadsafe(manager.broadcast(stamp_event(ev_dict)), loop)

    return _publish


update_jobs = UpdateJobManager(
    runner=lambda project, emit: run_safe_update(project, emit, workspace_root()),
)
recent_events: deque[dict[str, Any]] = deque(maxlen=MAX_RECENT_EVENTS)
