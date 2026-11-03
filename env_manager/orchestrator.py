"""Smart Clean Install Pipeline orchestrator module.

Executes unified environment lifecycle operations across all LemGendary projects
and streams real-time telemetry events to CLI, WebSocket, and GUI clients.

The steps live in ``env_manager.pipeline.steps`` and are shared by the full
run and the single-step run; this module owns the run state (claim, status,
current step) and the event callback wiring.
"""

import threading
from collections.abc import Callable, Generator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from env_manager.health_checker import HealthAuditReport
from env_manager.pipeline.events import Emit, EventPublisher, PipelineEvent, PipProgress
from env_manager.pipeline.install import PREFERRED_OPENCV_VARIANT, parse_pip_line_to_event
from env_manager.pipeline.steps import STEPS, TOTAL_STEPS, PipelineRun, RunOptions
from env_manager.pipeline.upgrades import UPGRADE_SUMMARY_SIZE, execute_safe_upgrades
from env_manager.pipeline.venvs import get_active_venv
from env_manager.system_probe import HardwareProfile
from env_manager.venv_manager import ProjectVenvInfo
from env_manager.workspace import workspace_root

EventCallback = Callable[[PipelineEvent], None]


class PipelineOrchestrator:
    """Orchestrates end-to-end environment creation, installation, and auditing."""

    def __init__(self, base_dir: Path | None = None):
        self._base_dir = base_dir

        self.current_step = 0
        self.total_steps = TOTAL_STEPS
        self.is_running = False
        self.last_status = "idle"
        self.last_run_timestamp: str | None = None
        self.last_report: HealthAuditReport | None = None
        self._claim_lock = threading.Lock()

    def try_claim(self) -> bool:
        """Mark the orchestrator running if it is idle; return False if a run is active.

        Callers that start a run on another thread claim it first, so two
        requests arriving together cannot both start a pipeline.
        """
        with self._claim_lock:
            if self.is_running:
                return False
            self.is_running = True
            return True

    def release(self) -> None:
        """Mark the orchestrator idle (the run ended or never started)."""
        with self._claim_lock:
            self.is_running = False

    @property
    def base_dir(self) -> Path:
        """Workspace this orchestrator manages: the injected one, else the current root."""
        return self._base_dir if self._base_dir is not None else workspace_root()

    def _set_current_step(self, step_number: int) -> None:
        if 1 <= step_number <= self.total_steps:
            self.current_step = step_number

    def _start(self, publisher: EventPublisher, options: RunOptions) -> PipelineRun:
        self.is_running = True
        self.last_status = "running"
        self.last_run_timestamp = datetime.now(UTC).isoformat()
        return PipelineRun(
            base_dir=self.base_dir, publisher=publisher, options=options,
            active_venv=get_active_venv(),
        )

    def run_clean_install_pipeline(
        self,
        event_callback: EventCallback | None = None,
        target_project: str | None = None,
        clean: bool = False,
    ) -> Generator[PipelineEvent, None, HealthAuditReport]:
        """Execute the 7-step Smart Clean Install Pipeline.

        The default run is non-destructive: valid venvs and node_modules are
        kept and no processes are stopped. Only ``clean=True`` deletes every
        venv and node_modules and stops processes running from those venvs.
        """
        publisher = EventPublisher(self.total_steps, event_callback, self._set_current_step)
        run = self._start(publisher, RunOptions(target_project, clean, full_run=True))
        try:
            report: HealthAuditReport | None = None
            for step in STEPS.values():
                outcome = yield from step(run)
                if isinstance(outcome.value, HealthAuditReport):
                    report = outcome.value
                    self.last_report = report
                    self.last_status = outcome.status
            if report is None:
                raise RuntimeError("The health matrix step returned no report")
            return report
        except Exception as exc:
            self.last_status = "failed"
            yield publisher.step(0, "Pipeline Error")(
                "error", f"Unhandled pipeline exception: {exc}",
            )
            raise
        finally:
            # Also runs on GeneratorExit when a consumer closes the run early.
            self.release()

    def run_pipeline_single_step(
        self,
        step_number: int,
        event_callback: EventCallback | None = None,
        target_project: str | None = None,
        clean: bool = False,
    ) -> Generator[PipelineEvent, None, Any]:
        """Execute an individual step from the 7-step pipeline in isolation.

        The step is the same generator the full run uses (``pipeline.steps``);
        its value becomes this generator's return value.
        """
        publisher = EventPublisher(self.total_steps, event_callback)
        run = self._start(publisher, RunOptions(target_project, clean))
        self.current_step = step_number
        step = STEPS.get(step_number)
        try:
            if step is None:
                yield publisher.step(0, "Invalid Step")(
                    "error",
                    f"Requested step number {step_number} is outside the valid range (1-7).",
                )
                self.last_status = "error"
                return None
            outcome = yield from step(run)
            if isinstance(outcome.value, HealthAuditReport):
                self.last_report = outcome.value
            self.last_status = outcome.status
            return outcome.value
        except Exception as exc:
            self.last_status = "failed"
            yield publisher.step(step_number, f"Step {step_number} Error")(
                "error", f"Unhandled exception during step {step_number}: {exc}",
            )
            raise
        finally:
            self.release()

    def get_status(self) -> dict[str, Any]:
        """Return current status and telemetry of the pipeline."""
        return {
            "is_running": self.is_running,
            "current_step": self.current_step,
            "total_steps": self.total_steps,
            "last_status": self.last_status,
            "last_run_timestamp": self.last_run_timestamp,
            "has_report": self.last_report is not None,
        }

    def reset(self) -> None:
        """Reset internal execution state."""
        self.current_step = 0
        self.is_running = False
        self.last_status = "idle"

    @staticmethod
    def parse_pip_line_to_event(
        progress: PipProgress, line: str, emit_fn: Emit,
    ) -> tuple[int, str, PipelineEvent] | None:
        """Parse a raw pip stdout line into a live progress event (see ``pipeline.install``)."""
        return parse_pip_line_to_event(progress, line, emit_fn)

    def execute_safe_upgrades(
        self, projects: list[ProjectVenvInfo], hw: HardwareProfile, emit: Emit,
    ) -> Generator[PipelineEvent, None, None]:
        """Apply safe dependency upgrades bottom-up across managed projects."""
        yield from execute_safe_upgrades(self.base_dir, projects, hw, emit)


__all__ = [
    "PREFERRED_OPENCV_VARIANT",
    "UPGRADE_SUMMARY_SIZE",
    "PipProgress",
    "PipelineEvent",
    "PipelineOrchestrator",
]
