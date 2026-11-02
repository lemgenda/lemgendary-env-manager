"""Background jobs for safe package updates.

`POST /api/update` used to wait for the whole upgrade inside the request
handler, which froze the event loop for up to 20 minutes (review finding
D-04). This module runs the upgrade on a worker thread instead. A lock makes
sure only one update runs at a time, and the job state can be read at any
time through `UpdateJobManager.snapshot()`.
"""

import logging
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, UTC
from pathlib import Path
from typing import Any
from collections.abc import Callable

from env_manager.system_probe import probe_hardware
from env_manager.updater import apply_upgrade_plan, build_upgrade_plan

_log = logging.getLogger("env_manager.update_jobs")

EventSink = Callable[[dict[str, Any]], None]
# A runner gets the target project (None means all projects) and a sink for
# progress events. It returns the final job status.
UpdateRunner = Callable[[str | None, EventSink], str]

STEP_NAME = "Safe Update"


class UpdateAlreadyRunningError(RuntimeError):
    """Raised when an update is requested while another one is running."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class UpdateJob:
    """State of one safe-update job."""

    job_id: str
    project: str | None
    status: str  # running, up_to_date, success, partial, failed
    started_at: str
    finished_at: str | None = None
    error: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def lifecycle_event(job: UpdateJob, status: str, message: str) -> dict[str, Any]:
    """Build a PipelineEvent-shaped message so the GUI log shows job progress."""
    return {
        "timestamp": _now(),
        "step_number": 0,
        "total_steps": 1,
        "step_name": STEP_NAME,
        "status": status,
        "message": message,
        "data": {"job_id": job.job_id, "project": job.project, "job_status": job.status},
        "is_progress": False,
    }


def run_safe_update(project: str | None, emit: EventSink, base_dir: Path) -> str:
    """Build and apply the upgrade plan for one project or all projects."""
    plan = build_upgrade_plan(base_dir, project=project)
    if not plan.has_any_updates:
        return "up_to_date"

    hw = probe_hardware()
    all_ok = True
    for ev in apply_upgrade_plan(plan, base_dir, extra_index_url=hw.recommended_torch_index):
        ev_dict = ev.to_dict()
        if ev_dict.get("status") not in ("upgraded", "skipped"):
            all_ok = False
        emit(ev_dict)
    return "success" if all_ok else "partial"


class UpdateJobManager:
    """Runs at most one update job at a time on a background thread."""

    def __init__(self, runner: UpdateRunner):
        self._runner = runner
        self._run_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._job: UpdateJob | None = None

    @property
    def is_running(self) -> bool:
        return self._run_lock.locked()

    def snapshot(self) -> dict[str, Any] | None:
        """Copy of the current or last job, or None if no job ran yet."""
        with self._state_lock:
            return self._job.to_dict() if self._job else None

    def start(self, project: str | None, publish: EventSink) -> dict[str, Any]:
        """Start a job and return its initial state without waiting for it.

        Raises UpdateAlreadyRunningError if another job holds the lock.
        """
        if not self._run_lock.acquire(blocking=False):
            current = self.snapshot()
            job_id = current["job_id"] if current else "unknown"
            raise UpdateAlreadyRunningError(f"An update is already running (job {job_id}).")

        job = UpdateJob(
            job_id=uuid.uuid4().hex,
            project=project,
            status="running",
            started_at=_now(),
        )
        with self._state_lock:
            self._job = job
            initial = job.to_dict()

        try:
            worker = threading.Thread(
                target=self._run,
                args=(job, publish),
                name=f"update-job-{job.job_id[:8]}",
                daemon=True,
            )
            worker.start()
        except RuntimeError:
            self._run_lock.release()
            raise
        return initial

    def _run(self, job: UpdateJob, publish: EventSink) -> None:
        scope = job.project or "all projects"
        publish(lifecycle_event(job, "info", f"Safe update started for {scope}."))

        def emit(ev: dict[str, Any]) -> None:
            with self._state_lock:
                job.events.append(ev)
            publish(ev)

        error: str | None = None
        try:
            try:
                final_status = self._runner(job.project, emit)
            except Exception as exc:  # job boundary: record the failure, never lose it
                _log.exception("Safe update job %s failed", job.job_id)
                final_status = "failed"
                error = str(exc)
            with self._state_lock:
                job.status = final_status
                job.error = error
                job.finished_at = _now()
        finally:
            self._run_lock.release()

        if final_status == "failed":
            publish(lifecycle_event(job, "error", f"Safe update failed for {scope}: {error}"))
        elif final_status == "partial":
            message = f"Safe update finished with errors for {scope}."
            publish(lifecycle_event(job, "warning", message))
        else:
            message = f"Safe update finished for {scope} ({final_status})."
            publish(lifecycle_event(job, "success", message))
