"""Tests for background safe-update jobs and the liveness endpoint (D-04, D-05).

The real updater is never called: every test injects a fake runner or
patches `env_manager.server.update_jobs`.
"""

import asyncio
import threading
from dataclasses import dataclass, field
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from env_manager import server
from env_manager.update_jobs import (
    UpdateAlreadyRunningError,
    UpdateJobManager,
    run_safe_update,
)


@dataclass
class _BlockingRunner:
    """Runner that waits until the test releases it."""

    final_status: str = "success"
    release: threading.Event = field(default_factory=threading.Event)
    started: threading.Event = field(default_factory=threading.Event)
    projects: list[str | None] = field(default_factory=list)

    def __call__(self, project: str | None, emit) -> str:
        self.projects.append(project)
        self.started.set()
        emit({"project": project or "all", "status": "upgraded", "message": "ok"})
        if not self.release.wait(timeout=5):
            raise TimeoutError("test runner was never released")
        return self.final_status


def _wait_until_idle(jobs: UpdateJobManager, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while jobs.is_running and time.monotonic() < deadline:
        time.sleep(0.01)


class TestUpdateJobManager(unittest.TestCase):
    """The background safe-update job manager."""
    def test_start_returns_before_the_runner_finishes(self) -> None:
        """``start`` returns before the runner has finished."""
        runner = _BlockingRunner()
        jobs = UpdateJobManager(runner)
        published: list[dict[str, Any]] = []

        job = jobs.start("lemgendary-datasets", published.append)

        self.assertEqual(job["status"], "running")
        self.assertTrue(runner.started.wait(timeout=5))
        self.assertTrue(jobs.is_running)
        runner.release.set()
        _wait_until_idle(jobs)

        self.assertFalse(jobs.is_running)
        snap = jobs.snapshot()
        assert snap is not None
        self.assertEqual(snap["job_id"], job["job_id"])
        self.assertEqual(snap["status"], "success")
        self.assertEqual(snap["project"], "lemgendary-datasets")
        self.assertEqual(len(snap["events"]), 1)
        self.assertIsNotNone(snap["finished_at"])
        self.assertEqual(published[0]["step_name"], "Safe Update")
        self.assertEqual(published[-1]["status"], "success")

    def test_second_start_while_running_is_refused(self) -> None:
        """A second start while a job runs is refused."""
        runner = _BlockingRunner()
        jobs = UpdateJobManager(runner)
        jobs.start(None, lambda ev: None)
        self.assertTrue(runner.started.wait(timeout=5))

        with self.assertRaises(UpdateAlreadyRunningError):
            jobs.start(None, lambda ev: None)

        runner.release.set()
        _wait_until_idle(jobs)
        jobs.start(None, lambda ev: None)  # lock released: a new job may start
        _wait_until_idle(jobs)
        self.assertEqual(runner.projects, [None, None])

    def test_runner_failure_is_recorded_and_releases_the_lock(self) -> None:
        """A failing runner is recorded and frees the manager for the next job."""
        def failing_runner(project, emit):
            raise RuntimeError("pip exploded")

        jobs = UpdateJobManager(failing_runner)
        published: list[dict[str, Any]] = []
        jobs.start(None, published.append)
        _wait_until_idle(jobs)

        snap = jobs.snapshot()
        assert snap is not None
        self.assertEqual(snap["status"], "failed")
        self.assertEqual(snap["error"], "pip exploded")
        self.assertFalse(jobs.is_running)
        self.assertEqual(published[-1]["status"], "error")


class TestRunSafeUpdate(unittest.TestCase):
    """``run_safe_update`` plans, applies and reports one update."""
    def test_project_is_passed_to_the_plan_builder(self) -> None:
        """The requested project reaches the plan builder."""
        plan = MagicMock(has_any_updates=False)
        with patch("env_manager.update_jobs.build_upgrade_plan", return_value=plan) as build:
            status = run_safe_update("lemgendary-docs", lambda ev: None, Path("ws"))
        build.assert_called_once_with(Path("ws"), project="lemgendary-docs")
        self.assertEqual(status, "up_to_date")

    def test_failed_package_makes_the_job_partial(self) -> None:
        """One failed package makes the job status partial."""
        plan = MagicMock(has_any_updates=True)
        events = [
            MagicMock(to_dict=lambda: {"status": "upgraded"}),
            MagicMock(to_dict=lambda: {"status": "failed"}),
        ]
        emitted: list[dict[str, Any]] = []
        with patch("env_manager.update_jobs.build_upgrade_plan", return_value=plan), \
                patch("env_manager.update_jobs.apply_upgrade_plan", return_value=iter(events)):
            status = run_safe_update(None, emitted.append, Path("ws"))
        self.assertEqual(status, "partial")
        self.assertEqual(len(emitted), 2)


class TestUpdateEndpoint(unittest.TestCase):
    """``POST /api/update`` and ``GET /api/update/status``."""
    def test_update_returns_accepted_with_job_id(self) -> None:
        """A real update returns ``accepted`` with a job id."""
        fake_jobs = MagicMock()
        fake_jobs.start.return_value = {"job_id": "abc", "status": "running"}
        with patch.object(server, "update_jobs", fake_jobs), \
                patch.object(server.orchestrator, "is_running", False):
            request = server.UpdateRequest(project="lemgendary-datasets")
            data = asyncio.run(server.run_update(request))
        self.assertEqual(data["status"], "accepted")
        self.assertEqual(data["job_id"], "abc")
        self.assertEqual(data["project"], "lemgendary-datasets")
        self.assertEqual(fake_jobs.start.call_args.args[0], "lemgendary-datasets")

    def test_update_while_running_returns_409(self) -> None:
        """An update while another runs returns 409."""
        fake_jobs = MagicMock()
        fake_jobs.start.side_effect = UpdateAlreadyRunningError(
            "An update is already running (job abc)."
        )
        with patch.object(server, "update_jobs", fake_jobs), \
                patch.object(server.orchestrator, "is_running", False):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(server.run_update(server.UpdateRequest()))
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertIn("already running", ctx.exception.detail)

    def test_update_while_pipeline_runs_returns_409(self) -> None:
        """An update while the pipeline runs returns 409."""
        fake_jobs = MagicMock()
        with patch.object(server, "update_jobs", fake_jobs), \
                patch.object(server.orchestrator, "is_running", True):
            with self.assertRaises(HTTPException) as ctx:
                asyncio.run(server.run_update(server.UpdateRequest()))
        self.assertEqual(ctx.exception.status_code, 409)
        fake_jobs.start.assert_not_called()

    def test_unknown_project_returns_404(self) -> None:
        """An unknown project returns 404."""
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(server.run_update(server.UpdateRequest(project="not-a-project")))
        self.assertEqual(ctx.exception.status_code, 404)

    def test_dry_run_passes_project_and_does_not_start_a_job(self) -> None:
        """A dry run builds the plan for the project and starts no job."""
        plan = MagicMock(has_any_updates=True)
        plan.to_dict.return_value = {"projects": []}
        fake_jobs = MagicMock()
        with patch.object(server, "build_upgrade_plan", return_value=plan) as build, \
                patch.object(server, "update_jobs", fake_jobs):
            data = asyncio.run(server.run_update(
                server.UpdateRequest(project="lemgendary-docs", dry_run=True)
            ))
        self.assertEqual(data["status"], "dry_run")
        self.assertEqual(build.call_args.kwargs["project"], "lemgendary-docs")
        fake_jobs.start.assert_not_called()

    def test_status_endpoint_reports_job(self) -> None:
        """The status endpoint reports the current job."""
        fake_jobs = MagicMock(is_running=True)
        fake_jobs.snapshot.return_value = {"job_id": "abc", "status": "running"}
        with patch.object(server, "update_jobs", fake_jobs):
            data = asyncio.run(server.get_update_status())
        self.assertTrue(data["is_running"])
        self.assertEqual(data["job"]["job_id"], "abc")

    def test_pipeline_refuses_to_start_during_update(self) -> None:
        """The pipeline refuses to start while an update runs."""
        fake_jobs = MagicMock(is_running=True)
        with patch.object(server, "update_jobs", fake_jobs), \
                patch.object(server.orchestrator, "is_running", False), \
                patch.object(server.threading, "Thread") as thread:
            data = asyncio.run(server.run_pipeline(server.RunPipelineRequest()))
        self.assertEqual(data["status"], "error")
        thread.assert_not_called()


class TestPingEndpoint(unittest.TestCase):
    """``/api/ping`` liveness probe."""
    def test_ping_is_constant_and_does_no_audit(self) -> None:
        """Ping returns a constant body and runs no health audit."""
        with patch.object(server, "run_full_health_audit") as audit:
            data = asyncio.run(server.ping())
        audit.assert_not_called()
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["service"], "lemgendary-env-manager")
        self.assertEqual(data["version"], server.app.version)

    def test_ping_route_supports_get_and_head(self) -> None:
        """Ping answers both GET and HEAD."""
        methods = set()
        for route in server.app.routes:
            if getattr(route, "path", None) == "/api/ping":
                methods |= set(getattr(route, "methods", set()))
        self.assertTrue({"GET", "HEAD"} <= methods)


if __name__ == "__main__":
    unittest.main()
