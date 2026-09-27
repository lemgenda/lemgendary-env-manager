"""
LemGendary Environment Manager — Server REST API Route Handler Unit Tests.

Phase 2 of Ecosystem Comprehensive Testing Battery.
Tests FastAPI route handler coroutines (Hardware, Projects, Pipeline, GUI State, Sidecar Mesh)
directly without requiring external HTTP client test dependencies.
"""

import asyncio
import unittest

from env_manager.server import (
    app,
    get_gui_ecosystem,
    get_gui_state,
    get_hardware,
    get_manifests,
    get_pipeline_status,
    get_projects,
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


if __name__ == "__main__":
    unittest.main()
