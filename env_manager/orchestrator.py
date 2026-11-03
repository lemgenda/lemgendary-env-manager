"""Smart Clean Install Pipeline orchestrator module.

Executes unified environment lifecycle operations across all LemGendary projects
and streams real-time telemetry events to CLI, WebSocket, and GUI clients.
"""

import re
import shutil
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, UTC
from pathlib import Path
from typing import Any
from collections.abc import Callable, Generator

from env_manager._logging import get_logger
from env_manager.bootstrap import verify_prerequisites
from env_manager.dependency_resolver import (
    classify_safe_upgrades,
    find_outdated_packages,
    venv_package_count,
)
from env_manager.health_checker import HealthAuditReport, run_full_health_audit
from env_manager.npm_manager import stream_npm_install
from env_manager.requirements_manager import parse_requirements_file, sync_all_manifests
from env_manager.system_probe import probe_hardware
from env_manager.updater import (
    _apply_batch_with_rollback,
    _apply_npm_update,
    _find_npm_outdated,
    sync_manifests_from_venvs,
)
from env_manager.validator import validate_project
from env_manager.venv_manager import (
    create_venv,
    discover_projects,
    is_venv_valid,
    normalize_opencv_variant,
    stream_install_requirements,
)
from env_manager.workspace import workspace_root

_log = get_logger(__name__)

UPGRADE_SUMMARY_SIZE = 5
"""Upgraded packages named in the step summary before "+N more"."""


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


def _get_active_venv() -> Path | None:
    """Return the .venv directory that the current process is running from."""
    try:
        exe = Path(sys.executable).resolve()
    except (OSError, RuntimeError) as exc:
        _log.warning("Cannot resolve the running interpreter %s: %s", sys.executable, exc)
        return None
    for parent in exe.parents:
        if parent.name == ".venv":
            return parent
    return None


def _is_same_venv(venv_dir: Path, active_venv: Path | None) -> bool:
    """Return True when ``venv_dir`` is the venv this process runs from."""
    if active_venv is None:
        return False
    try:
        return venv_dir.resolve() == active_venv
    except (OSError, RuntimeError) as exc:
        _log.warning("Cannot resolve venv path %s: %s", venv_dir, exc)
        return False


def _purge_node_modules(nm_dir: Path) -> str | None:
    """Delete a node_modules tree; return the error text if it fails."""
    try:
        shutil.rmtree(nm_dir)
    except OSError as exc:
        return str(exc)
    return None


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
    progress: PipProgress, trimmed: str,
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


_PIP_PROGRESS_RULES: tuple[PipProgressRule, ...] = (
    _pip_rule_collecting,
    _pip_rule_already_satisfied,
    _pip_rule_downloading,
    _pip_rule_installing,
    _pip_rule_installed,
)


@dataclass(frozen=True)
class _StepContext:
    """Inputs shared by the single-step handlers of the orchestrator."""

    emit: Callable[..., "PipelineEvent"]
    target_project: str | None
    clean: bool
    active_venv: Path | None


class PipelineOrchestrator:
    """Orchestrates end-to-end environment creation, installation, and auditing."""

    def __init__(self, base_dir: Path | None = None):
        self._base_dir = base_dir

        self.current_step = 0
        self.total_steps = 7
        self.is_running = False
        self.last_status = "idle"
        self.last_run_timestamp: str | None = None
        self.last_report: HealthAuditReport | None = None

    @property
    def base_dir(self) -> Path:
        """Workspace this orchestrator manages: the injected one, else the current root."""
        return self._base_dir if self._base_dir is not None else workspace_root()

    def run_clean_install_pipeline(
        self,
        event_callback: Callable[[PipelineEvent], None] | None = None,
        target_project: str | None = None,
        clean: bool = False,
    ) -> Generator[PipelineEvent, None, HealthAuditReport]:
        """Execute the 7-step Smart Clean Install Pipeline.

        The default run is non-destructive: valid venvs and node_modules are
        kept and no processes are stopped. Only ``clean=True`` deletes every
        venv and node_modules and stops processes running from those venvs.
        """
        self.is_running = True
        self.last_status = "running"
        self.last_run_timestamp = datetime.now(UTC).isoformat()

        active_venv = _get_active_venv()
        install_failures: list[str] = []

        def emit(
            step_num: int,
            step_name: str,
            status: str,
            msg: str,
            *,
            data: dict[str, Any] | None = None,
            is_progress: bool = False,
        ) -> PipelineEvent:
            ev = PipelineEvent(
                timestamp=datetime.now(UTC).isoformat(),
                step_number=step_num,
                total_steps=self.total_steps,
                step_name=step_name,
                status=status,
                message=msg,
                data=data,
                is_progress=is_progress,
            )
            if event_callback:
                event_callback(ev)
            return ev

        try:
            # Step 1: System Probe & Hardware Detection
            yield emit(1, "Hardware Discovery", "info",
                       "Probing system hardware and accelerators...")
            hw = probe_hardware()
            accel_names = [a.name for a in hw.accelerators] or ["None (CPU Fallback)"]
            yield emit(
                1,
                "Hardware Discovery",
                "success",
                f"Detected platform {hw.os_name} ({hw.architecture}) with backend: "
                f"{hw.primary_backend.upper()} [{', '.join(accel_names)}].",
                data=hw.to_dict(),
            )

            # Step 2: Global Prerequisites Audit
            yield emit(2, "Toolchain Audit", "info",
                       "Verifying Python, Git, and NPM toolchains...")
            boot = verify_prerequisites()
            if not boot.python_valid or not boot.git_installed:
                yield emit(
                    2,
                    "Toolchain Audit",
                    "error",
                    f"Toolchain prerequisites missing: {', '.join(boot.missing_prerequisites)}",
                    data=boot.to_dict(),
                )
            else:
                yield emit(
                    2,
                    "Toolchain Audit",
                    "success",
                    f"Python {boot.python_version} and Git verified successfully.",
                    data=boot.to_dict(),
                )

            # Step 3: Project Discovery & Venv Provisioning
            yield emit(3, "Virtual Environments", "info",
                       "Discovering managed projects and virtual environments...")
            projects = discover_projects(self.base_dir)
            if target_project:
                projects = [p for p in projects if p.name == target_project]

            for p in projects:
                p_dir = Path(p.project_dir)

                if p.is_node_project:
                    if clean:
                        nm_dir = p_dir / "node_modules"
                        if nm_dir.exists():
                            yield emit(
                                3, "Virtual Environments", "info",
                                f"Purging existing node_modules for {p.name} (clean install)...",
                            )
                            purge_error = _purge_node_modules(nm_dir)
                            if purge_error:
                                yield emit(
                                    3, "Virtual Environments", "error",
                                    f"Failed to purge node_modules for {p.name}: {purge_error}",
                                )
                    if p.node_modules_present and not clean:
                        yield emit(3, "Virtual Environments", "info",
                                   f"Existing node_modules verified for {p.name}.")
                    else:
                        yield emit(
                            3, "Virtual Environments", "info",
                            f"node_modules will be installed freshly for {p.name} in Step 4.",
                        )
                    continue

                venv_dir = p_dir / ".venv"

                is_active_venv = _is_same_venv(venv_dir, active_venv)

                if clean and is_active_venv:
                    yield emit(
                        3,
                        "Virtual Environments",
                        "warning",
                        f"Skipping clean purge for active environment {p.name} "
                        f"(cannot delete the venv currently executing this pipeline).",
                    )
                elif clean or not is_venv_valid(p_dir):
                    action_label = "Recreating fresh" if clean else "Creating fresh"
                    yield emit(3, "Virtual Environments", "info",
                               f"{action_label} virtual environment for {p.name}...")
                    success, msg = create_venv(p_dir)
                    if success:
                        yield emit(3, "Virtual Environments", "success",
                                   f"Virtual environment verified for {p.name}.")
                    else:
                        yield emit(3, "Virtual Environments", "error",
                                   f"Failed to create .venv for {p.name}: {msg}")
                else:
                    yield emit(3, "Virtual Environments", "info",
                               f"Existing .venv verified for {p.name}.")

            # Step 4: Requirements Manifest Sync & Installation
            yield emit(4, "Requirements Synchronization", "info",
                       "Synchronizing centralized manifests to projects...")
            sync_results = sync_all_manifests(self.base_dir)
            for p_name, (ok, s_msg) in sync_results.items():
                if target_project and p_name != target_project:
                    continue
                if ok:
                    yield emit(4, "Requirements Synchronization", "success", s_msg)
                else:
                    yield emit(4, "Requirements Synchronization", "warning", s_msg)

            yield emit(4, "Package Installation", "info",
                       "Installing dependencies into target environments...")
            failures = yield from self._execute_package_installation(
                projects, hw, emit
            )
            install_failures.extend(failures)

            # Step 5: Safe Dependency Audit & Upgrades
            yield emit(
                5, "Dependency Upgrades", "info",
                "Auditing outdated packages and applying safe upgrades bottom-up...",
            )
            yield from self.execute_safe_upgrades(projects, hw, emit)
            # Step 6: Bytecode Compilation, Zero-Emoji & Full Linting Validation
            yield emit(6, "Codebase Verification", "info",
                       "Running full validation suite (py_compile, emoji, lint, PS1)...")
            all_validation_passed = True
            for p in projects:
                p_dir = Path(p.project_dir)
                report = validate_project(
                    p_dir,
                    is_node_project=p.is_node_project if hasattr(p, "is_node_project") else False,
                )
                if report.passed:
                    yield emit(
                        6,
                        "Codebase Verification",
                        "success",
                        f"{p.name}: Compiled {report.compiled_files_count} files, "
                        f"0 syntax errors, 0 emoji violations.",
                    )
                else:
                    all_validation_passed = False
                    err_summary = (
                        f"Syntax: {len(report.compile_errors)}, "
                        f"Emoji: {len(report.emoji_violations)}, "
                        f"Lint: {len(report.lint_errors)}, "
                        f"YAML: {len(report.yaml_errors)}, "
                        f"HTML: {len(report.html_errors)}, "
                        f"WCAG: {len(report.wcag_violations)}"
                    )
                    yield emit(
                        6,
                        "Codebase Verification",
                        "error",
                        f"{p.name} validation failed: {err_summary}",
                        data=report.to_dict(),
                    )

            if all_validation_passed:
                yield emit(6, "Codebase Verification", "info",
                           "All projects passed validation. Syncing manifests...")
                sync_result = sync_all_manifests(self.base_dir)
                sync_ok = all(ok for ok, _ in sync_result.values()) if sync_result else False
                sync_msg = "; ".join(
                    f"{name}: {msg}" for name, (_, msg) in sync_result.items()
                ) if sync_result else "no manifests processed"
                yield emit(
                    6,
                    "Codebase Verification",
                    "success" if sync_ok else "warning",
                    f"Manifest sync completed: {sync_msg}",
                )
            else:
                yield emit(
                    6,
                    "Codebase Verification",
                    "warning",
                    "Manifest sync skipped due to validation failures. Fix errors and re-run.",
                )

            # Step 7: Final Health Report Generation
            yield emit(7, "Health Matrix", "info",
                       "Generating final ecosystem health report...")

            if install_failures:
                yield emit(
                    7,
                    "Health Matrix",
                    "warning",
                    f"Dependency installation failed for: {', '.join(install_failures)}. "
                    f"Fix pip resolver conflicts and re-run.",
                )

            final_report = run_full_health_audit(self.base_dir)
            self.last_report = final_report
            self.last_status = "success" if final_report.overall_healthy else "warning"

            status_type = "success" if final_report.overall_healthy else "warning"
            yield emit(
                7,
                "Health Matrix",
                status_type,
                f"Pipeline execution completed. "
                f"Ecosystem healthy status: {final_report.overall_healthy}.",
                data=final_report.to_dict(),
            )

            self.is_running = False
            return final_report

        except Exception as exc:
            self.is_running = False
            self.last_status = "failed"
            yield emit(0, "Pipeline Error", "error",
                       f"Unhandled pipeline exception: {exc}")
            raise

    def run_pipeline_single_step(
        self,
        step_number: int,
        event_callback: Callable[[PipelineEvent], None] | None = None,
        target_project: str | None = None,
        clean: bool = False,
    ) -> Generator[PipelineEvent, None, Any]:
        """Execute an individual step from the 7-step pipeline in isolation.

        Each step is a generator method registered in ``step_handlers``; the
        handler's return value becomes this generator's return value.
        """
        self.is_running = True
        self.current_step = step_number
        self.last_status = "running"
        self.last_run_timestamp = datetime.now(UTC).isoformat()

        def emit(
            step_num: int,
            step_name: str,
            status: str,
            msg: str,
            *,
            data: dict[str, Any] | None = None,
            is_progress: bool = False,
        ) -> PipelineEvent:
            ev = PipelineEvent(
                timestamp=datetime.now(UTC).isoformat(),
                step_number=step_num,
                total_steps=self.total_steps,
                step_name=step_name,
                status=status,
                message=msg,
                data=data,
                is_progress=is_progress,
            )
            if event_callback:
                event_callback(ev)
            return ev

        ctx = _StepContext(
            emit=emit,
            target_project=target_project,
            clean=clean,
            active_venv=_get_active_venv(),
        )
        step_handlers = {
            1: self._single_step_hardware_discovery,
            2: self._single_step_toolchain_audit,
            3: self._single_step_virtual_environments,
            4: self._single_step_requirements_install,
            5: self._single_step_dependency_upgrades,
            6: self._single_step_codebase_verification,
            7: self._single_step_health_matrix,
        }
        handler = step_handlers.get(step_number)
        try:
            if handler is None:
                yield emit(
                    0,
                    "Invalid Step",
                    "error",
                    f"Requested step number {step_number} is outside the valid range (1-7).",
                )
                self.last_status = "error"
                return None
            return (yield from handler(ctx))
        except Exception as exc:
            self.last_status = "failed"
            yield emit(step_number, f"Step {step_number} Error", "error",
                       f"Unhandled exception during step {step_number}: {exc}")
            raise
        finally:
            self.is_running = False

    def _single_step_hardware_discovery(
        self, ctx: _StepContext,
    ) -> Generator[PipelineEvent, None, Any]:
        """Step 1 of :meth:`run_pipeline_single_step`."""
        emit = ctx.emit
        # Step 1: System Probe & Hardware Detection
        yield emit(1, "Hardware Discovery", "info",
                   "Probing system hardware and accelerators...")
        hw = probe_hardware()
        accel_names = [a.name for a in hw.accelerators] or ["None (CPU Fallback)"]
        yield emit(
            1,
            "Hardware Discovery",
            "success",
            f"Detected platform {hw.os_name} ({hw.architecture}) with backend: "
            f"{hw.primary_backend.upper()} [{', '.join(accel_names)}].",
            data=hw.to_dict(),
        )
        self.last_status = "success"
        return hw

    def _single_step_toolchain_audit(
        self, ctx: _StepContext,
    ) -> Generator[PipelineEvent, None, Any]:
        """Step 2 of :meth:`run_pipeline_single_step`."""
        emit = ctx.emit
        # Step 2: Global Prerequisites Audit
        yield emit(2, "Toolchain Audit", "info",
                   "Verifying Python, Git, and NPM toolchains...")
        boot = verify_prerequisites()
        if not boot.python_valid or not boot.git_installed:
            self.last_status = "warning"
            yield emit(
                2,
                "Toolchain Audit",
                "error",
                f"Toolchain prerequisites missing: {', '.join(boot.missing_prerequisites)}",
                data=boot.to_dict(),
            )
        else:
            self.last_status = "success"
            yield emit(
                2,
                "Toolchain Audit",
                "success",
                f"Python {boot.python_version} and Git verified successfully.",
                data=boot.to_dict(),
            )
        return boot

    def _single_step_virtual_environments(
        self, ctx: _StepContext,
    ) -> Generator[PipelineEvent, None, Any]:
        """Step 3 of :meth:`run_pipeline_single_step`."""
        emit = ctx.emit
        target_project = ctx.target_project
        clean = ctx.clean
        active_venv = ctx.active_venv
        # Step 3: Project Discovery & Venv Provisioning
        yield emit(3, "Virtual Environments", "info",
                   "Discovering managed projects and virtual environments...")
        projects = discover_projects(self.base_dir)
        if target_project:
            projects = [p for p in projects if p.name == target_project]

        for p in projects:
            p_dir = Path(p.project_dir)

            if p.is_node_project:
                if clean:
                    nm_dir = p_dir / "node_modules"
                    if nm_dir.exists():
                        yield emit(
                            3, "Virtual Environments", "info",
                            f"Purging node_modules for {p.name} (clean install)...",
                        )
                        purge_error = _purge_node_modules(nm_dir)
                        if purge_error:
                            yield emit(
                                3, "Virtual Environments", "error",
                                f"Failed to purge node_modules for {p.name}: {purge_error}",
                            )
                if p.node_modules_present and not clean:
                    yield emit(3, "Virtual Environments", "info",
                               f"Existing node_modules verified for {p.name}.")
                else:
                    yield emit(3, "Virtual Environments", "info",
                               f"node_modules will be installed for {p.name} in Step 4.")
                continue

            venv_dir = p_dir / ".venv"
            is_active_venv = _is_same_venv(venv_dir, active_venv)

            if clean and is_active_venv:
                yield emit(
                    3,
                    "Virtual Environments",
                    "warning",
                    f"Skipping clean purge for active environment {p.name} "
                    f"(cannot delete active runtime venv).",
                )
            elif clean or not is_venv_valid(p_dir):
                action_label = "Recreating fresh" if clean else "Creating fresh"
                yield emit(3, "Virtual Environments", "info",
                           f"{action_label} virtual environment for {p.name}...")
                success, msg = create_venv(p_dir)
                if success:
                    yield emit(3, "Virtual Environments", "success",
                               f"Virtual environment verified for {p.name}.")
                else:
                    yield emit(3, "Virtual Environments", "error",
                               f"Failed to create .venv for {p.name}: {msg}")
            else:
                yield emit(3, "Virtual Environments", "info",
                           f"Existing .venv verified for {p.name}.")
        self.last_status = "success"
        return projects

    def _single_step_requirements_install(
        self, ctx: _StepContext,
    ) -> Generator[PipelineEvent, None, Any]:
        """Step 4 of :meth:`run_pipeline_single_step`."""
        emit = ctx.emit
        target_project = ctx.target_project
        # Step 4: Requirements Manifest Sync & Installation
        yield emit(4, "Requirements Synchronization", "info",
                   "Synchronizing centralized manifests to projects...")
        sync_results = sync_all_manifests(self.base_dir)
        for p_name, (ok, s_msg) in sync_results.items():
            if target_project and p_name != target_project:
                continue
            if ok:
                yield emit(4, "Requirements Synchronization", "success", s_msg)
            else:
                yield emit(4, "Requirements Synchronization", "warning", s_msg)

        yield emit(4, "Package Installation", "info",
                   "Installing dependencies into target environments...")
        hw = probe_hardware()
        projects = discover_projects(self.base_dir)
        if target_project:
            projects = [p for p in projects if p.name == target_project]

        failures = yield from self._execute_package_installation(
            projects, hw, emit
        )
        self.last_status = "success" if not failures else "warning"
        return sync_results

    def _single_step_dependency_upgrades(
        self, ctx: _StepContext,
    ) -> Generator[PipelineEvent, None, Any]:
        """Step 5 of :meth:`run_pipeline_single_step`."""
        emit = ctx.emit
        target_project = ctx.target_project
        # Step 5: Safe Dependency Audit & Upgrades
        yield emit(
            5, "Dependency Upgrades", "info",
            "Auditing outdated packages and applying safe upgrades bottom-up...",
        )
        hw = probe_hardware()
        projects = discover_projects(self.base_dir)
        if target_project:
            projects = [p for p in projects if p.name == target_project]
        yield from self.execute_safe_upgrades(projects, hw, emit)
        self.last_status = "success"

    def _single_step_codebase_verification(
        self, ctx: _StepContext,
    ) -> Generator[PipelineEvent, None, Any]:
        """Step 6 of :meth:`run_pipeline_single_step`."""
        emit = ctx.emit
        target_project = ctx.target_project
        # Step 6: Bytecode Compilation, Zero-Emoji & Full Linting Validation
        yield emit(6, "Codebase Verification", "info",
                   "Running full validation suite (py_compile, emoji, lint, PS1)...")
        projects = discover_projects(self.base_dir)
        if target_project:
            projects = [p for p in projects if p.name == target_project]

        all_validation_passed = True
        for p in projects:
            p_dir = Path(p.project_dir)
            report = validate_project(
                p_dir,
                is_node_project=(
                    p.is_node_project if hasattr(p, "is_node_project") else False
                ),
            )
            if report.passed:
                yield emit(
                    6,
                    "Codebase Verification",
                    "success",
                    f"{p.name}: Compiled {report.compiled_files_count} files, "
                    f"0 syntax errors, 0 emoji violations.",
                )
            else:
                all_validation_passed = False
                err_summary = (
                    f"Syntax: {len(report.compile_errors)}, "
                    f"Emoji: {len(report.emoji_violations)}, "
                    f"Lint: {len(report.lint_errors)}, "
                    f"YAML: {len(report.yaml_errors)}, "
                    f"HTML: {len(report.html_errors)}, "
                    f"WCAG: {len(report.wcag_violations)}"
                )
                yield emit(
                    6,
                    "Codebase Verification",
                    "error",
                    f"{p.name} validation failed: {err_summary}",
                    data=report.to_dict(),
                )

        if all_validation_passed:
            yield emit(6, "Codebase Verification", "info",
                       "All projects passed validation. Syncing manifests...")
            sync_result = sync_all_manifests(self.base_dir)
            sync_ok = all(ok for ok, _ in sync_result.values()) if sync_result else False
            sync_msg = "; ".join(
                f"{name}: {msg}" for name, (_, msg) in sync_result.items()
            ) if sync_result else "no manifests processed"
            yield emit(
                6,
                "Codebase Verification",
                "success" if sync_ok else "warning",
                f"Manifest sync completed: {sync_msg}",
            )
        else:
            yield emit(
                6,
                "Codebase Verification",
                "warning",
                "Manifest sync skipped due to validation failures. Fix errors and re-run.",
            )
        self.last_status = "success" if all_validation_passed else "warning"
        return all_validation_passed

    def _single_step_health_matrix(
        self, ctx: _StepContext,
    ) -> Generator[PipelineEvent, None, Any]:
        """Step 7 of :meth:`run_pipeline_single_step`."""
        emit = ctx.emit
        # Step 7: Final Health Report Generation
        yield emit(7, "Health Matrix", "info",
                   "Generating ecosystem health report...")
        final_report = run_full_health_audit(self.base_dir)
        self.last_report = final_report
        self.last_status = "success" if final_report.overall_healthy else "warning"

        status_type = "success" if final_report.overall_healthy else "warning"
        yield emit(
            7,
            "Health Matrix",
            status_type,
            f"Step 7 completed. Ecosystem healthy status: {final_report.overall_healthy}.",
            data=final_report.to_dict(),
        )
        return final_report


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
        progress: PipProgress,
        line: str,
        emit_fn: Callable[..., PipelineEvent],
    ) -> tuple[int, str, PipelineEvent] | None:
        """Parse a raw pip stdout line into a live telemetry progress event.

        Returns ``(completed_count, current_pkg, event)`` for a recognised
        line, else None. Recognition rules live in ``_PIP_PROGRESS_RULES``.
        """
        trimmed = line.strip()
        if not trimmed:
            return None
        for rule in _PIP_PROGRESS_RULES:
            outcome = rule(progress, trimmed)
            if outcome is not None:
                completed_count, current_pkg, msg = outcome
                event = emit_fn(4, "Package Installation", "info", msg, is_progress=True)
                return completed_count, current_pkg, event
        return None

    def _execute_package_installation(
        self,
        projects: list[Any],
        hw: Any,
        emit: Callable[..., PipelineEvent],
    ) -> Generator[PipelineEvent, None, list[str]]:
        """Install node and python dependencies with live streaming progress telemetry."""
        install_failures: list[str] = []

        for p in projects:
            p_dir = Path(p.project_dir)

            if p.is_node_project:
                yield emit(
                    4, "Package Installation", "info",
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
                                4, "Package Installation", "info",
                                f"INSTALLING: {p.name} (npm) - [{npm_line[:100]}]",
                                is_progress=True,
                            )
                except StopIteration as stop:
                    npm_ok, npm_msg = stop.value

                if npm_ok:
                    yield emit(
                        4, "Package Installation", "success",
                        f"npm install completed successfully for {p.name}.",
                        is_progress=False,
                    )
                else:
                    install_failures.append(p.name)
                    yield emit(
                        4, "Package Installation", "error",
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
                4, "Package Installation", "info",
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
                    prog_info = self.parse_pip_line_to_event(
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
                    4, "Package Installation", "success",
                    f"Dependencies installed successfully for {p.name}.",
                    is_progress=False,
                )
            else:
                install_failures.append(p.name)
                yield emit(
                    4, "Package Installation", "error",
                    f"Failed installing dependencies for {p.name}: {inst_msg[:300]}",
                    is_progress=False,
                )
                continue

            desired = PREFERRED_OPENCV_VARIANT.get(p.name)
            if desired:
                yield emit(
                    4, "Package Installation", "info",
                    f"INSTALLING: {p.name} - [Normalizing OpenCV provider to {desired}...]",
                    is_progress=True,
                )
                norm_ok, norm_msg = normalize_opencv_variant(p_dir, desired)
                if norm_ok:
                    yield emit(4, "Package Installation", "success", norm_msg, is_progress=False)
                else:
                    yield emit(
                        4, "Package Installation", "warning",
                        f"OpenCV normalization failed for {p.name}: {norm_msg}",
                        is_progress=False,
                    )

        return install_failures

    def execute_safe_upgrades(
        self,
        projects: list[Any],
        hw: Any,
        emit: Callable[..., PipelineEvent],
    ) -> Generator[PipelineEvent, None, None]:
        """Apply safe dependency upgrades bottom-up across managed projects.

        Each project is classified from its discovery metadata: every
        project that is not a pure Node project gets the Python safe-upgrade
        path, and every project with a ``package.json`` gets the npm path.
        A dual-stack project (for example a Python repo with npm lint
        tooling) therefore receives both.
        """
        for p in projects:
            if p.has_npm_manifest:
                yield from self._upgrade_npm_dependencies(p, emit)
            if not p.is_node_project:
                yield from self._upgrade_python_dependencies(p, hw, emit)

        try:
            sync_manifests_from_venvs(self.base_dir)
        except OSError as sync_err:
            _log.warning("Manifest sync after upgrades failed: %s", sync_err)
            yield emit(
                5, "Dependency Upgrades", "info",
                f"Manifest sync notice: {sync_err}",
            )

    def _upgrade_npm_dependencies(
        self,
        p: Any,
        emit: Callable[..., PipelineEvent],
    ) -> Generator[PipelineEvent, None, None]:
        """Run ``npm update`` for a project with outdated npm packages."""
        p_dir = Path(p.project_dir)
        outdated = _find_npm_outdated(p_dir)
        if not outdated:
            yield emit(
                5, "Dependency Upgrades", "success",
                f"{p.name} npm dependencies are up to date.",
            )
            return

        yield emit(
            5, "Dependency Upgrades", "info",
            f"UPGRADING: {p.name} (npm) - updating {len(outdated)} package(s)...",
        )
        npm_ok, npm_msg = _apply_npm_update(p_dir)
        if npm_ok:
            yield emit(
                5, "Dependency Upgrades", "success",
                f"{p.name}: Successfully updated npm packages.",
            )
        else:
            yield emit(
                5, "Dependency Upgrades", "warning",
                f"{p.name}: npm update encountered issue: {npm_msg[:200]}",
            )

    def _upgrade_python_dependencies(
        self,
        p: Any,
        hw: Any,
        emit: Callable[..., PipelineEvent],
    ) -> Generator[PipelineEvent, None, None]:
        """Apply classified safe pip upgrades to one project's venv."""
        p_dir = Path(p.project_dir)

        pkg_count = venv_package_count(p_dir)
        if pkg_count == 0:
            yield emit(
                5, "Dependency Upgrades", "warning",
                f"{p.name} environment has no installed packages. Skipping upgrades.",
            )
            return

        raw = find_outdated_packages(p_dir)
        if not raw:
            yield emit(
                5, "Dependency Upgrades", "success",
                f"{p.name} dependencies are up to date ({pkg_count} packages).",
            )
            return

        safe, blocked = classify_safe_upgrades(
            p_dir,
            raw,
            cuda_index_url=hw.recommended_torch_index,
        )

        for pkg, reason in blocked:
            yield emit(
                5, "Dependency Upgrades", "warning",
                f"  Blocked: {pkg.name} {pkg.current_version}"
                f" -> {pkg.latest_version} ({reason})",
            )

        if not safe:
            yield emit(
                5, "Dependency Upgrades", "info",
                f"{p.name}: No unconstrained safe upgrades available"
                f" (all {len(blocked)} candidate(s) blocked).",
            )
            return

        yield emit(
            5, "Dependency Upgrades", "info",
            f"UPGRADING: {p.name} - applying {len(safe)} safe upgrade(s)...",
        )

        safe_names = [pkg.name for pkg in safe]
        upgraded_ok, upgrade_msg, rolled_back = _apply_batch_with_rollback(
            p_dir,
            safe_names,
            extra_index_url=hw.recommended_torch_index,
        )

        if upgraded_ok:
            upgraded_list = ", ".join(f"{pkg.name} ({pkg.latest_version})" for pkg in safe[:UPGRADE_SUMMARY_SIZE])
            if len(safe) > UPGRADE_SUMMARY_SIZE:
                upgraded_list += f", +{len(safe) - UPGRADE_SUMMARY_SIZE} more"
            yield emit(
                5, "Dependency Upgrades", "success",
                f"{p.name}: Upgraded {len(safe)} package(s) safely [{upgraded_list}].",
            )
        else:
            rollback_note = " (safely rolled back to previous snapshot)" if rolled_back else ""
            yield emit(
                5, "Dependency Upgrades", "warning",
                f"{p.name}: Upgrade batch failed{rollback_note}: {upgrade_msg[:200]}",
            )
