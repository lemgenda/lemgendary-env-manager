"""Pipeline step 4: npm and pip installs with live progress, then OpenCV normalization."""

from collections.abc import Callable, Generator
from pathlib import Path
from typing import Any

from env_manager._logging import get_logger
from env_manager.npm_manager import stream_npm_install
from env_manager.pipeline.events import PIP_PROGRESS_RULES, Emit, PipelineEvent, PipProgress
from env_manager.requirements_manager import parse_requirements_file
from env_manager.venv_manager import (
    normalize_opencv_variant,
    stream_install_requirements,
)

_log = get_logger(__name__)


# Which OpenCV provider each project should end up with.
# The datasets project needs mediapipe's contrib modules (SIFT, aruco,
# etc.), and mediapipe ships opencv-contrib-python. Ultralytics also pulls
# opencv-python transitively. Pinning contrib as the definitive provider
# gives us a single, deterministic cv2 with all the modules we need.
#
# Training-suite is intentionally absent: albumentations (headless) and
# ultralytics (full) coexist there for legitimate training-side reasons,
# and training code does not call GUI functions like imshow.
PREFERRED_OPENCV_VARIANT: dict[str, str] = {
    "lemgendary-datasets": "opencv-contrib-python",
}


def parse_pip_line_to_event(
    progress: PipProgress,
    line: str,
    emit_fn: Emit,
) -> tuple[int, str, PipelineEvent] | None:
    """Parse a raw pip stdout line into a live telemetry progress event.

    Returns ``(completed_count, current_pkg, event)`` for a recognised
    line, else None. Recognition rules live in ``PIP_PROGRESS_RULES``.
    """
    trimmed = line.strip()
    if not trimmed:
        return None
    for rule in PIP_PROGRESS_RULES:
        outcome = rule(progress, trimmed)
        if outcome is not None:
            completed_count, current_pkg, msg = outcome
            event = emit_fn("info", msg, is_progress=True)
            return completed_count, current_pkg, event
    return None


def _relay[R](
    gen: Generator[str, None, R],
    to_event: Callable[[str], PipelineEvent | None],
) -> Generator[PipelineEvent, None, R]:
    """Yield an event for each streamed line ``to_event`` maps; return the stream's result."""
    try:
        while True:
            event = to_event(next(gen))
            if event is not None:
                yield event
    except StopIteration as stop:
        return stop.value


def _install_node(p: Any, emit: Emit) -> Generator[PipelineEvent, None, bool]:
    """Run ``npm install`` for a Node project; return whether it succeeded."""
    yield emit("info", f"INSTALLING: {p.name} (npm) - [Running npm install...]", is_progress=True)

    def to_event(npm_line: str) -> PipelineEvent | None:
        if "added" in npm_line or "reified" in npm_line or "packages" in npm_line:
            return emit("info", f"INSTALLING: {p.name} (npm) - [{npm_line[:100]}]",
                        is_progress=True)
        return None

    npm_ok, npm_msg = yield from _relay(stream_npm_install(Path(p.project_dir)), to_event)
    if npm_ok:
        yield emit("success", f"npm install completed successfully for {p.name}.",
                   is_progress=False)
    else:
        yield emit("error", f"npm install failed for {p.name}: {npm_msg[:300]}",
                   is_progress=False)
    return npm_ok


def _declared_requirement_count(req_file: Path) -> int:
    """Return the number of named requirements, used as the progress denominator."""
    try:
        parsed_reqs = parse_requirements_file(req_file)
    except (OSError, ValueError) as exc:
        _log.warning("Cannot read %s for progress estimation: %s", req_file, exc)
        return 10
    return max(1, len([r for r in parsed_reqs if r.name and not r.is_index_url]))


def _install_python(
    p: Any, req_file: Path, hw: Any, emit: Emit,
) -> Generator[PipelineEvent, None, bool]:
    """Install ``req_file`` with live progress; return whether it succeeded."""
    declared = _declared_requirement_count(req_file)
    yield emit(
        "info",
        f"INSTALLING: {p.name} - [0/{declared} requirements, resolving dependencies...]",
        is_progress=True,
    )
    progress = PipProgress(p.name, 0, declared, "")

    def to_event(line: str) -> PipelineEvent | None:
        nonlocal progress
        prog_info = parse_pip_line_to_event(progress, line, emit)
        if prog_info is None:
            return None
        completed_count, current_pkg, event = prog_info
        progress = PipProgress(p.name, completed_count, declared, current_pkg)
        return event

    pip_gen = stream_install_requirements(
        Path(p.project_dir), req_file, extra_index_url=hw.recommended_torch_index,
    )
    ok, inst_msg = yield from _relay(pip_gen, to_event)
    if ok:
        yield emit("success", f"Dependencies installed successfully for {p.name}.",
                   is_progress=False)
    else:
        yield emit("error", f"Failed installing dependencies for {p.name}: {inst_msg[:300]}",
                   is_progress=False)
    return ok


def _normalize_opencv(p: Any, emit: Emit) -> Generator[PipelineEvent, None, None]:
    """Switch the project's cv2 provider to its preferred OpenCV variant, if it has one."""
    desired = PREFERRED_OPENCV_VARIANT.get(p.name)
    if not desired:
        return
    yield emit(
        "info",
        f"INSTALLING: {p.name} - [Normalizing OpenCV provider to {desired}...]",
        is_progress=True,
    )
    norm_ok, norm_msg = normalize_opencv_variant(Path(p.project_dir), desired)
    if norm_ok:
        yield emit("success", norm_msg, is_progress=False)
    else:
        yield emit("warning", f"OpenCV normalization failed for {p.name}: {norm_msg}",
                   is_progress=False)


def install_packages(
    projects: list[Any],
    hw: Any,
    emit: Emit,
) -> Generator[PipelineEvent, None, list[str]]:
    """Install node and python dependencies with live streaming progress telemetry."""
    install_failures: list[str] = []
    for p in projects:
        if p.is_node_project and not (yield from _install_node(p, emit)):
            install_failures.append(p.name)

        req_file = Path(p.project_dir) / "requirements.txt"
        if not req_file.exists():
            continue
        if not (yield from _install_python(p, req_file, hw, emit)):
            install_failures.append(p.name)
            continue
        yield from _normalize_opencv(p, emit)
    return install_failures
