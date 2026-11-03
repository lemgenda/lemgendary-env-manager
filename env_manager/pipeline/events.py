"""Pipeline telemetry events and the pip output parser that turns lines into progress events."""

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Protocol


@dataclass
class PipelineEvent:
    """Individual telemetry event emitted during pipeline execution."""

    timestamp: str
    step_number: int
    total_steps: int
    step_name: str
    status: str  # "info", "success", "warning", "error"
    message: str
    data: dict[str, Any] | None = None
    is_progress: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary."""
        return asdict(self)


@dataclass(frozen=True)
class PipProgress:
    """Install progress known before a pip output line is parsed."""

    project_name: str
    completed_count: int
    total_reqs: int
    current_pkg: str

    def advanced(self) -> tuple[int, float]:
        """Return the completed count after one more requirement and its percent."""
        completed = min(self.total_reqs, self.completed_count + 1)
        return completed, round((completed / max(1, self.total_reqs)) * 100, 1)


# A rule maps (progress, stripped pip line) to (completed_count, current_pkg,
# message), or None when the line is not the kind the rule handles.
PipProgressRule = Callable[[PipProgress, str], "tuple[int, str, str] | None"]


def _pip_rule_collecting(progress: PipProgress, trimmed: str) -> tuple[int, str, str] | None:
    if not trimmed.startswith("Collecting "):
        return None
    parts = trimmed.split()
    pkg_name = parts[1] if len(parts) > 1 else ""
    cleaned_pkg = pkg_name.split(">=")[0].split("==")[0].split("<")[0].split(";")[0]
    completed, pct = progress.advanced()
    msg = (
        f"INSTALLING: {progress.project_name} - {pct}%"
        f" [{completed}/{progress.total_reqs} requirements, Collecting {cleaned_pkg}...]"
    )
    return completed, cleaned_pkg, msg


def _pip_rule_already_satisfied(
    progress: PipProgress,
    trimmed: str,
) -> tuple[int, str, str] | None:
    if not trimmed.startswith("Requirement already satisfied"):
        return None
    completed, pct = progress.advanced()
    pkg_info = trimmed.split(":", 1)[1].strip() if ":" in trimmed else trimmed
    cleaned_pkg = pkg_info.split()[0] if pkg_info else ""
    msg = (
        f"INSTALLING: {progress.project_name} - {pct}%"
        f" [{completed}/{progress.total_reqs} requirements, satisfied {cleaned_pkg}]"
    )
    return completed, progress.current_pkg, msg


_PIP_BAR_GLYPHS = re.compile(r"[\u2580-\u259F\u2500-\u257F\u2501\u2588\-=|]")


def _pip_rule_downloading(progress: PipProgress, trimmed: str) -> tuple[int, str, str] | None:
    is_download = (
        trimmed.startswith("Downloading ")
        or "MB/s" in trimmed
        or "kB/s" in trimmed
        or "eta" in trimmed.lower()
    )
    clean_dl = _PIP_BAR_GLYPHS.sub("", trimmed).strip() if is_download else ""
    if not clean_dl:
        return None
    msg = f"DOWNLOADING: {progress.current_pkg or progress.project_name} - {clean_dl}"
    return progress.completed_count, progress.current_pkg, msg


def _pip_rule_installing(progress: PipProgress, trimmed: str) -> tuple[int, str, str] | None:
    if not trimmed.startswith("Installing collected packages"):
        return None
    msg = (
        f"INSTALLING: {progress.project_name}"
        " - 95.0% [Building wheels and installing collected packages...]"
    )
    return progress.completed_count, progress.current_pkg, msg


def _pip_rule_installed(progress: PipProgress, trimmed: str) -> tuple[int, str, str] | None:
    if not trimmed.startswith("Successfully installed"):
        return None
    msg = f"INSTALLING: {progress.project_name} - 100% [Successfully installed dependencies]"
    return progress.completed_count, progress.current_pkg, msg


PIP_PROGRESS_RULES: tuple[PipProgressRule, ...] = (
    _pip_rule_collecting,
    _pip_rule_already_satisfied,
    _pip_rule_downloading,
    _pip_rule_installing,
    _pip_rule_installed,
)


class Emit(Protocol):
    """Emit one event for a step that is already bound (see ``StepEmitter``)."""

    def __call__(
        self,
        status: str,
        msg: str,
        *,
        data: dict[str, Any] | None = None,
        is_progress: bool = False,
    ) -> PipelineEvent:
        """Build, publish and return the event."""
        ...


@dataclass(frozen=True)
class EventPublisher:
    """Stamps events for one run and hands each one to the run's callback.

    ``on_step`` sees the step number of every event, so the orchestrator
    can track ``current_step``.
    """

    total_steps: int
    callback: Callable[[PipelineEvent], None] | None = None
    on_step: Callable[[int], None] | None = None

    def step(self, number: int, name: str) -> "StepEmitter":
        """Return an emitter bound to step ``number`` called ``name``."""
        return StepEmitter(self, number, name)

    def publish(self, event: PipelineEvent) -> PipelineEvent:
        """Record the step, pass ``event`` to the callback and return it."""
        if self.on_step is not None:
            self.on_step(event.step_number)
        if self.callback is not None:
            self.callback(event)
        return event


@dataclass(frozen=True)
class StepEmitter:
    """An ``Emit`` for one pipeline step."""

    publisher: EventPublisher
    number: int
    name: str

    def __call__(
        self,
        status: str,
        msg: str,
        *,
        data: dict[str, Any] | None = None,
        is_progress: bool = False,
    ) -> PipelineEvent:
        """Build the event for this step and publish it."""
        return self.publisher.publish(
            PipelineEvent(
                timestamp=datetime.now(UTC).isoformat(),
                step_number=self.number,
                total_steps=self.publisher.total_steps,
                step_name=self.name,
                status=status,
                message=msg,
                data=data,
                is_progress=is_progress,
            )
        )
