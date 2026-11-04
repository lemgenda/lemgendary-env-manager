"""
LemGendary Environment Manager — Server REST API Route Handler Unit Tests.

Phase 2 of Ecosystem Comprehensive Testing Battery.
Tests FastAPI route handler coroutines (Hardware, Projects, Pipeline, GUI State, Sidecar Mesh)
directly without requiring external HTTP client test dependencies.
"""

import asyncio
import unittest
from unittest.mock import patch

import env_manager
from env_manager.api.routes_ecosystem import get_gui_ecosystem, get_gui_state
from env_manager.api.routes_manifests import get_manifests
from env_manager.api.routes_pipeline import (
    RunPipelineRequest,
    RunStepRequest,
    get_pipeline_status,
    run_pipeline,
    run_pipeline_step,
)
from env_manager.api.routes_status import get_hardware, get_projects
from env_manager.ecosystem import ecosystem
from env_manager.health.models import ProjectHealth
from env_manager.server import app


def _port(service_id: str) -> int:
    """The port of ``service_id`` from the ecosystem file."""
    return ecosystem().require_service(service_id).port


class TestServerApi(unittest.TestCase):
    """Test suite for env-manager FastAPI sidecar route handlers."""

    def test_app_metadata(self) -> None:
        """The app title and version come from the package."""
        self.assertEqual(app.title, "LemGendary Environment Manager API")
        self.assertEqual(app.version, env_manager.__version__)

    def test_get_hardware_endpoint(self) -> None:
        """``/api/hardware`` returns the hardware profile."""
        data = asyncio.run(get_hardware())
        self.assertIn("os_name", data)
        self.assertIn("cpu_count_logical", data)
        self.assertIn("recommended_torch_index", data)

    def test_get_projects_endpoint(self) -> None:
        """``/api/projects`` lists the health of the fixture workspace projects."""
        data = asyncio.run(get_projects())
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)
        self.assertTrue(all(isinstance(p, ProjectHealth) for p in data))
        names = [p.name for p in data]
        self.assertIn("lemgendary-env-manager", names)

    def test_get_pipeline_status_endpoint(self) -> None:
        """``/api/pipeline/status`` reports state and recent events."""
        data = asyncio.run(get_pipeline_status())
        self.assertIn("is_running", data)
        self.assertIn("recent_events", data)

    def test_get_gui_state_endpoint(self) -> None:
        """``/api/gui/state`` aggregates service, hardware, projects and pipeline."""
        data = asyncio.run(get_gui_state())
        self.assertIn("service", data)
        self.assertEqual(data["service"].get("name"), "lemgendary-env-manager")
        self.assertEqual(data["service"].get("port"), _port("env-manager"))
        self.assertIn("hardware", data)
        self.assertIn("projects", data)
        self.assertIn("pipeline", data)

    def test_get_gui_ecosystem_endpoint(self) -> None:
        """``/api/gui/ecosystem`` lists each sidecar with its port."""
        data = asyncio.run(get_gui_ecosystem())
        self.assertIn("env_manager", data)
        self.assertEqual(data["env_manager"].get("port"), _port("env-manager"))
        self.assertIn("dataset_compiler", data)
        self.assertEqual(data["dataset_compiler"].get("port"), _port("dataset-compiler"))

    def test_get_manifests_endpoint(self) -> None:
        """``/api/manifests`` returns a manifest dict."""
        data = asyncio.run(get_manifests())
        self.assertIn("manifests", data)
        self.assertIsInstance(data["manifests"], dict)

    def test_run_pipeline_step_validation(self) -> None:
        """A step number outside 1-7 is rejected."""
        # Invalid step outside 1-7 range
        req = RunStepRequest(step_number=99)
        result = asyncio.run(run_pipeline_step(req))
        self.assertEqual(result.get("status"), "error")
        self.assertIn("Invalid step number", result.get("message", ""))


class TestRunPipelineDestructiveOptIn(unittest.TestCase):
    """``POST /api/pipeline/run`` is non-destructive unless ``clean`` is true."""

    def test_request_defaults_to_non_destructive(self) -> None:
        """Pipeline requests default to ``clean=False``."""
        self.assertFalse(RunPipelineRequest().clean)
        self.assertFalse(RunPipelineRequest(target_project="lemgendary-datasets").clean)
        self.assertFalse(RunStepRequest(step_number=3).clean)

    def _started_thread_args(self, req: RunPipelineRequest) -> tuple:
        with (
            patch("threading.Thread") as mock_thread,
            patch("env_manager.api.state.orchestrator.is_running", False),
        ):
            result = asyncio.run(run_pipeline(req))
        self.assertEqual(result.get("status"), "accepted")
        mock_thread.return_value.start.assert_called_once_with()
        return mock_thread.call_args.kwargs["args"]

    def test_run_forwards_default_false(self) -> None:
        """An omitted ``clean`` reaches the worker as False."""
        args = self._started_thread_args(RunPipelineRequest(target_project="x"))
        self.assertEqual(args[:2], ("x", False))

    def test_run_forwards_explicit_true(self) -> None:
        """``clean=True`` reaches the worker unchanged."""
        args = self._started_thread_args(RunPipelineRequest(clean=True))
        self.assertEqual(args[:2], (None, True))


if __name__ == "__main__":
    unittest.main()
