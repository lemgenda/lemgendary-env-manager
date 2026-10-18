"""Unit tests for Smart Clean Install Pipeline orchestrator module."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from env_manager.orchestrator import PipelineEvent, PipelineOrchestrator


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
        mock_discover: MagicMock,
        mock_sync: MagicMock,
        mock_prereqs: MagicMock,
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
        res = PipelineOrchestrator._parse_pip_line_to_event(
            "lemgendary-datasets", "Collecting torch>=2.2.0", 0, 40, "", emit_dummy
        )
        self.assertIsNotNone(res)
        assert res is not None
        completed, pkg, ev = res
        self.assertEqual(completed, 1)
        self.assertEqual(pkg, "torch")
        self.assertTrue(ev.is_progress)
        self.assertIn("INSTALLING: lemgendary-datasets - 2.5%", ev.message)

        # 2. Downloading line
        res_dl = PipelineOrchestrator._parse_pip_line_to_event(
            "lemgendary-datasets", "   ━━━ 1.2/2.4 GB 25.1 MB/s eta 0:00:48", 1, 40, "torch", emit_dummy
        )
        self.assertIsNotNone(res_dl)
        assert res_dl is not None
        _, _, ev_dl = res_dl
        self.assertTrue(ev_dl.is_progress)
        self.assertIn("DOWNLOADING: torch - 1.2/2.4 GB 25.1 MB/s eta 0:00:48", ev_dl.message)

        # 3. Installing collected packages
        res_inst = PipelineOrchestrator._parse_pip_line_to_event(
            "lemgendary-datasets", "Installing collected packages: torch, torchvision", 40, 40, "torch", emit_dummy
        )
        self.assertIsNotNone(res_inst)
        assert res_inst is not None
        _, _, ev_inst = res_inst
        self.assertTrue(ev_inst.is_progress)
        self.assertIn("95.0%", ev_inst.message)


if __name__ == "__main__":
    unittest.main()
