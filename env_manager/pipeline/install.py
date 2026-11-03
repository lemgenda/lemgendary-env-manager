"""Pipeline step 4: npm and pip installs with live progress, then OpenCV normalization."""

from collections.abc import Generator
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


def install_packages(
    projects: list[Any],
    hw: Any,
    emit: Emit,
) -> Generator[PipelineEvent, None, list[str]]:
    """Install node and python dependencies with live streaming progress telemetry."""
    install_failures: list[str] = []

    for p in projects:
        p_dir = Path(p.project_dir)

        if p.is_node_project:
            yield emit(
                "info",
                f"INSTALLING: {p.name} (npm) - [Running npm install...]",
                is_progress=True,
            )
            npm_gen = stream_npm_install(p_dir)
            npm_ok = False
            npm_msg = ""
            try:
                while True:
                    npm_line = next(npm_gen)
                    if "added" in npm_line or "reified" in npm_line or "packages" in npm_line:
                        yield emit(
                            "info",
                            f"INSTALLING: {p.name} (npm) - [{npm_line[:100]}]",
                            is_progress=True,
                        )
            except StopIteration as stop:
                npm_ok, npm_msg = stop.value

            if npm_ok:
                yield emit(
                    "success",
                    f"npm install completed successfully for {p.name}.",
                    is_progress=False,
                )
            else:
                install_failures.append(p.name)
                yield emit(
                    "error",
                    f"npm install failed for {p.name}: {npm_msg[:300]}",
                    is_progress=False,
                )

        req_file = p_dir / "requirements.txt"
        if not req_file.exists():
            continue

        declared_req_count = 1
        try:
            parsed_reqs = parse_requirements_file(req_file)
            declared_req_count = max(
                1, len([r for r in parsed_reqs if r.name and not r.is_index_url])
            )
        except (OSError, ValueError) as exc:
            _log.warning("Cannot read %s for progress estimation: %s", req_file, exc)
            declared_req_count = 10

        yield emit(
            "info",
            f"INSTALLING: {p.name}"
            f" - [0/{declared_req_count} requirements, resolving dependencies...]",
            is_progress=True,
        )

        pip_gen = stream_install_requirements(
            p_dir,
            req_file,
            extra_index_url=hw.recommended_torch_index,
        )

        completed_count = 0
        current_pkg = ""
        ok = False
        inst_msg = ""

        try:
            while True:
                line = next(pip_gen)
                prog_info = parse_pip_line_to_event(
                    PipProgress(p.name, completed_count, declared_req_count, current_pkg),
                    line,
                    emit,
                )
                if prog_info is not None:
                    completed_count, current_pkg, ev = prog_info
                    yield ev
        except StopIteration as stop:
            ok, inst_msg = stop.value

        if ok:
            yield emit(
                "success",
                f"Dependencies installed successfully for {p.name}.",
                is_progress=False,
            )
        else:
            install_failures.append(p.name)
            yield emit(
                "error",
                f"Failed installing dependencies for {p.name}: {inst_msg[:300]}",
                is_progress=False,
            )
            continue

        desired = PREFERRED_OPENCV_VARIANT.get(p.name)
        if desired:
            yield emit(
                "info",
                f"INSTALLING: {p.name} - [Normalizing OpenCV provider to {desired}...]",
                is_progress=True,
            )
            norm_ok, norm_msg = normalize_opencv_variant(p_dir, desired)
            if norm_ok:
                yield emit("success", norm_msg, is_progress=False)
            else:
                yield emit(
                    "warning",
                    f"OpenCV normalization failed for {p.name}: {norm_msg}",
                    is_progress=False,
                )

    return install_failures
