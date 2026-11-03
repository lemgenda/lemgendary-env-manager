"""Unit tests for Smart Clean Install Pipeline orchestrator module."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from env_manager.orchestrator import PipelineEvent, PipelineOrchestrator, PipProgress


class TestPipelineOrchestrator(unittest.TestCase):
    """Verifies PipelineOrchestrator lifecycle, telemetry events, and step transitions."""

    def test_pipeline_event_serialization(self) -> None:
        """Verify PipelineEvent fields and dictionary serialization."""
        event = PipelineEvent(
            timestamp="2026-10-17T00:00:00Z",
            step_number=1,
            total_steps=7,
            step_name="Hardware Discovery",
            status="info",
            message="Probing hardware...",
            data={"backend": "cuda"},
        )
        d = event.to_dict()
        self.assertEqual(d["step_number"], 1)
        self.assertEqual(d["total_steps"], 7)
        self.assertEqual(d["status"], "info")
        self.assertEqual(d["data"]["backend"], "cuda")

    def test_orchestrator_initial_state(self) -> None:
        """Verify initial orchestrator attributes and step counters."""
        orchestrator = PipelineOrchestrator()
        self.assertEqual(orchestrator.total_steps, 7)
        self.assertEqual(orchestrator.current_step, 0)
        self.assertFalse(orchestrator.is_running)
        self.assertEqual(orchestrator.last_status, "idle")

    @patch("env_manager.orchestrator.probe_hardware")
    @patch("env_manager.orchestrator.verify_prerequisites")
    @patch("env_manager.orchestrator.sync_all_manifests")
    @patch("env_manager.orchestrator.discover_projects")
    def test_pipeline_step_event_emission(
        self,
        _mock_discover: MagicMock,
        _mock_sync: MagicMock,
        _mock_prereqs: MagicMock,
        mock_probe: MagicMock,
    ) -> None:
        """Verify event emissions across initial pipeline steps with event callbacks."""
        mock_hw = MagicMock()
        mock_hw.accelerators = []
        mock_hw.os_name = "Windows"
        mock_hw.architecture = "x86_64"
        mock_hw.primary_backend = "cuda"
        mock_hw.to_dict.return_value = {"primary_backend": "cuda"}
        mock_probe.return_value = mock_hw

        orchestrator = PipelineOrchestrator()
        emitted_events: list[PipelineEvent] = []

        def callback(event: PipelineEvent) -> None:
            emitted_events.append(event)

        # Run generator up to step 1
        gen = orchestrator.run_clean_install_pipeline(event_callback=callback)
        first_event = next(gen)
        second_event = next(gen)

        self.assertEqual(first_event.step_number, 1)
        self.assertEqual(first_event.status, "info")
        self.assertEqual(second_event.status, "success")
        self.assertEqual(len(emitted_events), 2)

    def test_pip_progress_parsing(self) -> None:
        """Verify pip output lines are correctly parsed into live progress events."""
        def emit_dummy(step_num: int, step_name: str, status: str, msg: str, data=None, is_progress=False) -> PipelineEvent:
            return PipelineEvent(
                timestamp="2026-10-18T00:00:00Z",
                step_number=step_num,
                total_steps=7,
                step_name=step_name,
                status=status,
                message=msg,
                data=data,
                is_progress=is_progress,
            )

        # 1. Collecting line
        res = PipelineOrchestrator.parse_pip_line_to_event(
            PipProgress("lemgendary-datasets", 0, 40, ""), "Collecting torch>=2.2.0", emit_dummy
        )
        self.assertIsNotNone(res)
        assert res is not None
        completed, pkg, ev = res
        self.assertEqual(completed, 1)
        self.assertEqual(pkg, "torch")
        self.assertTrue(ev.is_progress)
        self.assertIn("INSTALLING: lemgendary-datasets - 2.5%", ev.message)

        # 2. Downloading line
        res_dl = PipelineOrchestrator.parse_pip_line_to_event(
            PipProgress("lemgendary-datasets", 1, 40, "torch"),
            "   ━━━ 1.2/2.4 GB 25.1 MB/s eta 0:00:48",
            emit_dummy,
        )
        self.assertIsNotNone(res_dl)
        assert res_dl is not None
        _, _, ev_dl = res_dl
        self.assertTrue(ev_dl.is_progress)
        self.assertIn("DOWNLOADING: torch - 1.2/2.4 GB 25.1 MB/s eta 0:00:48", ev_dl.message)

        # 3. Installing collected packages
        res_inst = PipelineOrchestrator.parse_pip_line_to_event(
            PipProgress("lemgendary-datasets", 40, 40, "torch"),
            "Installing collected packages: torch, torchvision",
            emit_dummy,
        )
        self.assertIsNotNone(res_inst)
        assert res_inst is not None
        _, _, ev_inst = res_inst
        self.assertTrue(ev_inst.is_progress)
        self.assertIn("95.0%", ev_inst.message)


class TestPipelineDestructiveOptIn(unittest.TestCase):
    """The full pipeline deletes environments only when ``clean=True`` is explicit.

    Every destructive function is mocked; nothing on disk is deleted.
    """

    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp_dir)
        root = Path(self._tmp_dir)
        self.node_dir = root / "node-project"
        self.py_dir = root / "py-project"
        (self.node_dir / "node_modules").mkdir(parents=True)
        self.py_dir.mkdir()
        self.projects = [
            SimpleNamespace(name="node-project", project_dir=str(self.node_dir),
                            is_node_project=True, node_modules_present=True),
            SimpleNamespace(name="py-project", project_dir=str(self.py_dir),
                            is_node_project=False, node_modules_present=False),
        ]
        hw = MagicMock()
        hw.accelerators = []
        hw.to_dict.return_value = {}
        boot = MagicMock(python_valid=True, git_installed=True)
        patch("env_manager.orchestrator.probe_hardware", return_value=hw).start()
        patch("env_manager.orchestrator.verify_prerequisites", return_value=boot).start()
        patch("env_manager.orchestrator.discover_projects", return_value=self.projects).start()
        patch("env_manager.orchestrator.is_venv_valid", return_value=True).start()
        patch("env_manager.orchestrator._get_active_venv", return_value=None).start()
        self.mock_create_venv = patch(
            "env_manager.orchestrator.create_venv", return_value=(True, "ok")
        ).start()
        self.mock_rmtree = patch("env_manager.orchestrator.shutil.rmtree").start()
        self.mock_sync = patch("env_manager.orchestrator.sync_all_manifests").start()

    def tearDown(self) -> None:
        patch.stopall()

    def _run_through_step_3(self, clean: bool | None = None) -> list[PipelineEvent]:
        """Drive the pipeline generator until Step 4 starts, then stop it.

        ``clean=None`` omits the argument to exercise the default.
        """
        orchestrator = PipelineOrchestrator(base_dir=Path(self._tmp_dir))
        if clean is None:
            gen = orchestrator.run_clean_install_pipeline()
        else:
            gen = orchestrator.run_clean_install_pipeline(clean=clean)
        events: list[PipelineEvent] = []
        for event in gen:
            if event.step_number >= 4:
                break
            events.append(event)
        gen.close()
        self.mock_sync.assert_not_called()
        return events

    def test_default_run_is_non_destructive(self) -> None:
        events = self._run_through_step_3()
        self.mock_create_venv.assert_not_called()
        self.mock_rmtree.assert_not_called()
        messages = [e.message for e in events]
        self.assertIn("Existing .venv verified for py-project.", messages)
        self.assertIn("Existing node_modules verified for node-project.", messages)

    def test_explicit_false_is_non_destructive(self) -> None:
        self._run_through_step_3(clean=False)
        self.mock_create_venv.assert_not_called()
        self.mock_rmtree.assert_not_called()

    def test_explicit_true_runs_destructive_path(self) -> None:
        self._run_through_step_3(clean=True)
        self.mock_create_venv.assert_called_once_with(self.py_dir)
        self.mock_rmtree.assert_called_once_with(self.node_dir / "node_modules")

    def test_purge_failure_is_reported(self) -> None:
        self.mock_rmtree.side_effect = PermissionError("locked")
        events = self._run_through_step_3(clean=True)
        errors = [e.message for e in events if e.status == "error"]
        self.assertEqual(len(errors), 1)
        self.assertIn("Failed to purge node_modules for node-project", errors[0])
        self.assertIn("locked", errors[0])


if __name__ == "__main__":
    unittest.main()
