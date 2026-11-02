"""
LemGendary Environment Manager — Server REST API Route Handler Unit Tests.

Phase 2 of Ecosystem Comprehensive Testing Battery.
Tests FastAPI route handler coroutines (Hardware, Projects, Pipeline, GUI State, Sidecar Mesh)
directly without requiring external HTTP client test dependencies.
"""

import asyncio
import unittest
from unittest.mock import patch

from env_manager.server import (
    app,
    get_gui_ecosystem,
    get_gui_state,
    get_hardware,
    get_manifests,
    get_pipeline_status,
    get_projects,
    run_pipeline,
    run_pipeline_step,
    RunPipelineRequest,
    RunStepRequest,
)


class TestServerApi(unittest.TestCase):
    """Test suite for env-manager FastAPI sidecar route handlers."""

    def test_app_metadata(self) -> None:
        self.assertEqual(app.title, "LemGendary Environment Manager API")
        self.assertEqual(app.version, "2.0.0")

    def test_get_hardware_endpoint(self) -> None:
        data = asyncio.run(get_hardware())
        self.assertIn("os_name", data)
        self.assertIn("cpu_count_logical", data)
        self.assertIn("recommended_torch_index", data)

    def test_get_projects_endpoint(self) -> None:
        data = asyncio.run(get_projects())
        self.assertIsInstance(data, list)
        self.assertGreater(len(data), 0)
        names = [p.get("name") for p in data]
        self.assertIn("lemgendary-env-manager", names)

    def test_get_pipeline_status_endpoint(self) -> None:
        data = asyncio.run(get_pipeline_status())
        self.assertIn("is_running", data)
        self.assertIn("recent_events", data)

    def test_get_gui_state_endpoint(self) -> None:
        data = asyncio.run(get_gui_state())
        self.assertIn("service", data)
        self.assertEqual(data["service"].get("name"), "lemgendary-env-manager")
        self.assertEqual(data["service"].get("port"), 8000)
        self.assertIn("hardware", data)
        self.assertIn("projects", data)
        self.assertIn("pipeline", data)

    def test_get_gui_ecosystem_endpoint(self) -> None:
        data = asyncio.run(get_gui_ecosystem())
        self.assertIn("env_manager", data)
        self.assertEqual(data["env_manager"].get("port"), 8000)
        self.assertIn("dataset_compiler", data)
        self.assertEqual(data["dataset_compiler"].get("port"), 8100)

    def test_get_manifests_endpoint(self) -> None:
        data = asyncio.run(get_manifests())
        self.assertIn("manifests", data)
        self.assertIsInstance(data["manifests"], dict)

    def test_run_pipeline_step_validation(self) -> None:
        # Invalid step outside 1-7 range
        req = RunStepRequest(step_number=99)
        result = asyncio.run(run_pipeline_step(req))
        self.assertEqual(result.get("status"), "error")
        self.assertIn("Invalid step number", result.get("message", ""))


class TestRunPipelineDestructiveOptIn(unittest.TestCase):
    """``POST /api/pipeline/run`` is non-destructive unless ``clean`` is true."""

    def test_request_defaults_to_non_destructive(self) -> None:
        self.assertFalse(RunPipelineRequest().clean)
        self.assertFalse(RunPipelineRequest(target_project="lemgendary-datasets").clean)
        self.assertFalse(RunStepRequest(step_number=3).clean)

    def _started_thread_args(self, req: RunPipelineRequest) -> tuple:
        with (
            patch("env_manager.server.threading.Thread") as mock_thread,
            patch("env_manager.server.orchestrator.is_running", False),
        ):
            result = asyncio.run(run_pipeline(req))
        self.assertEqual(result.get("status"), "accepted")
        mock_thread.return_value.start.assert_called_once_with()
        return mock_thread.call_args.kwargs["args"]

    def test_run_forwards_default_false(self) -> None:
        args = self._started_thread_args(RunPipelineRequest(target_project="x"))
        self.assertEqual(args[:2], ("x", False))

    def test_run_forwards_explicit_true(self) -> None:
        args = self._started_thread_args(RunPipelineRequest(clean=True))
        self.assertEqual(args[:2], (None, True))


if __name__ == "__main__":
    unittest.main()
