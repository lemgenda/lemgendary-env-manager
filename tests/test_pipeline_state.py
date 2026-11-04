"""Pipeline state and the recent-events buffer (REVIEW_REPORT D-12)."""

import asyncio
import threading
import unittest
from unittest.mock import patch

from env_manager.api import routes_pipeline, state
from env_manager.orchestrator import PipelineOrchestrator
from env_manager.api.routes_pipeline import RunPipelineRequest
from env_manager.api.state import record_event, recent_events_snapshot


class TestOrchestratorState(unittest.TestCase):
    """``is_running`` and ``current_step`` track the run."""

    def test_closing_the_full_pipeline_early_clears_is_running(self) -> None:
        """A consumer that stops iterating does not leave the orchestrator busy."""
        orch = PipelineOrchestrator()
        gen = orch.run_clean_install_pipeline()
        first = next(gen)
        self.assertTrue(orch.is_running)
        self.assertEqual(first.step_number, 1)
        self.assertEqual(orch.current_step, 1)
        gen.close()
        self.assertFalse(orch.is_running)

    def test_claim_is_exclusive(self) -> None:
        """Only one caller can claim an idle orchestrator."""
        orch = PipelineOrchestrator()
        self.assertTrue(orch.try_claim())
        self.assertFalse(orch.try_claim())
        orch.release()
        self.assertTrue(orch.try_claim())


class TestPipelineStartRace(unittest.TestCase):
    """Two fast POSTs start one pipeline."""

    def tearDown(self) -> None:
        state.orchestrator.release()

    def test_second_request_is_refused_before_the_worker_runs(self) -> None:
        """The run is claimed before the thread starts, not when it first iterates."""
        with patch("threading.Thread") as thread:
            first = asyncio.run(routes_pipeline.run_pipeline(RunPipelineRequest()))
            second = asyncio.run(routes_pipeline.run_pipeline(RunPipelineRequest()))
        self.assertEqual(first["status"], "accepted")
        self.assertEqual(second["status"], "error")
        thread.return_value.start.assert_called_once_with()


class TestRecentEvents(unittest.TestCase):
    """Every ``recent_events`` entry carries a ``timestamp``."""

    def test_missing_timestamp_is_added(self) -> None:
        """An event without a timestamp is stamped when recorded."""
        stored = record_event({"step_number": 0, "message": "no time"})
        self.assertTrue(stored["timestamp"])
        self.assertEqual(recent_events_snapshot(1), [stored])

    def test_existing_timestamp_is_kept(self) -> None:
        """An event's own timestamp is not replaced."""
        stored = record_event({"timestamp": "2026-11-03T00:00:00+00:00", "message": "x"})
        self.assertEqual(stored["timestamp"], "2026-11-03T00:00:00+00:00")

    def test_concurrent_writers_and_readers(self) -> None:
        """Readers never see a half-written buffer and every entry is stamped."""
        def write() -> None:
            for i in range(500):
                record_event({"message": str(i)})

        writers = [threading.Thread(target=write) for _ in range(4)]
        for t in writers:
            t.start()
        while any(t.is_alive() for t in writers):
            recent_events_snapshot(state.MAX_RECENT_EVENTS)
        for t in writers:
            t.join()
        events = recent_events_snapshot(state.MAX_RECENT_EVENTS)
        self.assertEqual(len(events), state.MAX_RECENT_EVENTS)
        self.assertTrue(all(ev.get("timestamp") for ev in events))

    def test_status_endpoint_events_have_timestamps(self) -> None:
        """``GET /api/pipeline/status`` returns only stamped events."""
        record_event({"message": "a"})
        record_event({"timestamp": "2026-11-03T00:00:00+00:00", "message": "b"})
        status = asyncio.run(routes_pipeline.get_pipeline_status())
        self.assertTrue(all(ev["timestamp"] for ev in status["recent_events"]))


if __name__ == "__main__":
    unittest.main()
