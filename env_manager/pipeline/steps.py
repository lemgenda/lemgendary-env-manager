"""The pipeline steps, shared by the full run and the single-step run.

Each step is a generator over ``PipelineEvent`` that returns a
``StepOutcome``. Inputs that a later step needs (hardware profile, project
list) are kept on ``PipelineRun``: the full run computes them once, a
single-step run computes what its step needs.

Step numbers, ids and names come from ``pipeline_steps`` in
``ecosystem.json``; :data:`STEPS` maps each to its handler by id.
"""

from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from env_manager.bootstrap import verify_prerequisites
from env_manager.ecosystem import EcosystemFileError, ecosystem, ecosystem_file
from env_manager.health_checker import run_full_health_audit
from env_manager.pipeline.events import EventPublisher, PipelineEvent, StepEmitter
from env_manager.pipeline.install import install_packages
from env_manager.pipeline.upgrades import execute_safe_upgrades
from env_manager.pipeline.venvs import is_same_venv, purge_node_modules
from env_manager.requirements_manager import sync_all_manifests
from env_manager.system_probe import HardwareProfile, probe_hardware
from env_manager.upgrade.manifest_sync import sync_manifests_from_venvs
from env_manager.validator import ProjectValidationReport, validate_project
from env_manager.venv_manager import (
    ProjectVenvInfo,
    create_venv,
    discover_projects,
    is_venv_valid,
)


@dataclass(frozen=True)
class RunOptions:
    """What the caller asked for."""

    target_project: str | None = None
    clean: bool = False
    full_run: bool = False


@dataclass
class PipelineRun:
    """State shared by the steps of one run."""

    base_dir: Path
    publisher: EventPublisher
    options: RunOptions
    active_venv: Path | None = None
    hw: HardwareProfile | None = None
    projects: list[ProjectVenvInfo] | None = None
    install_failures: list[str] = field(default_factory=list)
    upgrades_applied: bool = False

    def step(self, number: int, name: str | None = None) -> StepEmitter:
        """Emitter for events of step ``number``, named as in ``ecosystem.json`` by default."""
        return self.publisher.step(number, name or ecosystem().step(number).name)

    def hardware(self) -> HardwareProfile:
        """The hardware profile, probed on first use."""
        if self.hw is None:
            self.hw = probe_hardware()
        return self.hw

    def discover(self) -> list[ProjectVenvInfo]:
        """Discover the managed projects again (limited to the target) and keep them."""
        projects = discover_projects(self.base_dir)
        target = self.options.target_project
        if target:
            projects = [p for p in projects if p.name == target]
        self.projects = projects
        return projects

    def managed_projects(self) -> list[ProjectVenvInfo]:
        """The projects found by an earlier step, else a fresh discovery."""
        return self.projects if self.projects is not None else self.discover()


@dataclass(frozen=True)
class StepOutcome:
    """A step's return value and the ``last_status`` a single-step run reports."""

    value: Any
    status: str


StepEvents = Generator[PipelineEvent, None, StepOutcome]
Step = Callable[[PipelineRun], StepEvents]


def _status(ok: bool) -> str:
    return "success" if ok else "warning"


def hardware_discovery(run: PipelineRun) -> StepEvents:
    """Step 1: probe the system hardware and accelerators."""
    emit = run.step(1)
    yield emit("info", "Probing system hardware and accelerators...")
    run.hw = probe_hardware()
    hw = run.hw
    accel_names = [a.name for a in hw.accelerators] or ["None (CPU Fallback)"]
    yield emit(
        "success",
        f"Detected platform {hw.os_name} ({hw.architecture}) with backend: "
        f"{hw.primary_backend.upper()} [{', '.join(accel_names)}].",
        data=hw.to_dict(),
    )
    return StepOutcome(hw, "success")


def toolchain_audit(run: PipelineRun) -> StepEvents:
    """Step 2: verify the Python, Git and npm toolchains."""
    emit = run.step(2)
    yield emit("info", "Verifying Python, Git, and NPM toolchains...")
    boot = verify_prerequisites()
    ok = boot.python_valid and boot.git_installed
    if ok:
        yield emit(
            "success", f"Python {boot.python_version} and Git verified successfully.",
            data=boot.to_dict(),
        )
    else:
        yield emit(
            "error",
            f"Toolchain prerequisites missing: {', '.join(boot.missing_prerequisites)}",
            data=boot.to_dict(),
        )
    return StepOutcome(boot, _status(ok))


def _node_project_events(
    run: PipelineRun, project: ProjectVenvInfo, emit: StepEmitter,
) -> Generator[PipelineEvent, None, None]:
    p_dir = Path(project.project_dir)
    clean = run.options.clean
    nm_dir = p_dir / "node_modules"
    if clean and nm_dir.exists():
        yield emit("info", f"Purging existing node_modules for {project.name} (clean install)...")
        purge_error = purge_node_modules(nm_dir)
        if purge_error:
            yield emit(
                "error", f"Failed to purge node_modules for {project.name}: {purge_error}",
            )
    if project.node_modules_present and not clean:
        yield emit("info", f"Existing node_modules verified for {project.name}.")
    else:
        yield emit("info", f"node_modules will be installed freshly for {project.name} in Step 4.")


def _python_project_events(
    run: PipelineRun, project: ProjectVenvInfo, emit: StepEmitter,
) -> Generator[PipelineEvent, None, None]:
    p_dir = Path(project.project_dir)
    clean = run.options.clean
    if clean and is_same_venv(p_dir / ".venv", run.active_venv):
        yield emit(
            "warning",
            f"Skipping clean purge for active environment {project.name} "
            f"(cannot delete the venv currently executing this pipeline).",
        )
    elif clean or not is_venv_valid(p_dir):
        action_label = "Recreating fresh" if clean else "Creating fresh"
        yield emit("info", f"{action_label} virtual environment for {project.name}...")
        success, msg = create_venv(p_dir)
        if success:
            yield emit("success", f"Virtual environment verified for {project.name}.")
        else:
            yield emit("error", f"Failed to create .venv for {project.name}: {msg}")
    else:
        yield emit("info", f"Existing .venv verified for {project.name}.")


def virtual_environments(run: PipelineRun) -> StepEvents:
    """Step 3: discover projects and create (or, with ``clean``, recreate) their envs."""
    emit = run.step(3)
    yield emit("info", "Discovering managed projects and virtual environments...")
    projects = run.discover()
    for project in projects:
        if project.is_node_project:
            yield from _node_project_events(run, project, emit)
        else:
            yield from _python_project_events(run, project, emit)
    return StepOutcome(projects, "success")


def requirements_install(run: PipelineRun) -> StepEvents:
    """Step 4: sync the central manifests, then install every project's packages."""
    emit = run.step(4)
    yield emit("info", "Synchronizing centralized manifests to projects...")
    sync_results = sync_all_manifests(run.base_dir)
    target = run.options.target_project
    for p_name, (ok, s_msg) in sync_results.items():
        if not target or p_name == target:
            yield emit(_status(ok), s_msg)

    install = run.step(4, "Package Installation")
    yield install("info", "Installing dependencies into target environments...")
    hw = run.hardware()
    failures = yield from install_packages(run.managed_projects(), hw, install)
    run.install_failures.extend(failures)
    return StepOutcome(sync_results, _status(not failures))


def dependency_upgrades(run: PipelineRun) -> StepEvents:
    """Step 5: audit outdated packages and apply the safe upgrades."""
    emit = run.step(5)
    yield emit("info", "Auditing outdated packages and applying safe upgrades bottom-up...")
    hw = run.hardware()
    run.upgrades_applied = yield from execute_safe_upgrades(run.managed_projects(), hw, emit)
    return StepOutcome(None, "success")


def _failure_summary(report: ProjectValidationReport) -> str:
    return (
        f"Syntax: {len(report.compile_errors)}, "
        f"Emoji: {len(report.emoji_violations)}, "
        f"Lint: {len(report.lint_errors)}, "
        f"YAML: {len(report.yaml_errors)}, "
        f"HTML: {len(report.html_errors)}, "
        f"WCAG: {len(report.wcag_violations)}"
    )


def _sync_after_validation(run: PipelineRun, emit: StepEmitter) -> PipelineEvent:
    """Write step 5 upgrades back to the manifests, then copy them to the projects."""
    sync_result: dict[str, tuple[bool, str]] = {}
    if run.upgrades_applied:
        from_venvs = sync_manifests_from_venvs(run.base_dir, project=run.options.target_project)
        sync_result.update({f"{name} (from venv)": res for name, res in from_venvs.items()})
    sync_result.update(sync_all_manifests(run.base_dir))
    sync_ok = all(ok for ok, _ in sync_result.values()) if sync_result else False
    sync_msg = "; ".join(
        f"{name}: {msg}" for name, (_, msg) in sync_result.items()
    ) if sync_result else "no manifests processed"
    return emit(_status(sync_ok), f"Manifest sync completed: {sync_msg}")


def codebase_verification(run: PipelineRun) -> StepEvents:
    """Step 6: validate every project; sync manifests only when all pass."""
    emit = run.step(6)
    yield emit("info", "Running full validation suite (py_compile, emoji, lint, PS1)...")
    all_passed = True
    for project in run.managed_projects():
        report = validate_project(
            Path(project.project_dir), is_node_project=project.is_node_project,
        )
        if report.passed:
            yield emit(
                "success",
                f"{project.name}: Compiled {report.compiled_files_count} files, "
                f"0 syntax errors, 0 emoji violations.",
            )
        else:
            all_passed = False
            yield emit(
                "error", f"{project.name} validation failed: {_failure_summary(report)}",
                data=report.to_dict(),
            )
    if all_passed:
        yield emit("info", "All projects passed validation. Syncing manifests...")
        yield _sync_after_validation(run, emit)
    else:
        yield emit(
            "warning",
            "Manifest sync skipped due to validation failures. Fix errors and re-run.",
        )
    return StepOutcome(all_passed, _status(all_passed))


def health_matrix(run: PipelineRun) -> StepEvents:
    """Step 7: run the full health audit and report the result."""
    emit = run.step(7)
    yield emit("info", "Generating final ecosystem health report...")
    if run.install_failures:
        yield emit(
            "warning",
            f"Dependency installation failed for: {', '.join(run.install_failures)}. "
            f"Fix pip resolver conflicts and re-run.",
        )
    report = run_full_health_audit(run.base_dir)
    done = "Pipeline execution completed." if run.options.full_run else "Step 7 completed."
    yield emit(
        _status(report.overall_healthy),
        f"{done} Ecosystem healthy status: {report.overall_healthy}.",
        data=report.to_dict(),
    )
    return StepOutcome(report, _status(report.overall_healthy))


_HANDLERS: dict[str, Step] = {
    "hardware_discovery": hardware_discovery,
    "toolchain_audit": toolchain_audit,
    "virtual_environments": virtual_environments,
    "requirements_install": requirements_install,
    "dependency_upgrades": dependency_upgrades,
    "codebase_verification": codebase_verification,
    "health_matrix": health_matrix,
}
"""Step handlers by ``pipeline_steps[].id``."""


def _steps_by_number() -> dict[int, Step]:
    """Pair every ``pipeline_steps`` entry with its handler; fail on any mismatch."""
    declared = ecosystem().pipeline_steps
    ids = {step.id for step in declared}
    if ids != set(_HANDLERS):
        raise EcosystemFileError(
            f"pipeline_steps ids {sorted(ids)} in {ecosystem_file()} do not match "
            f"the handlers {sorted(_HANDLERS)}"
        )
    return {step.number: _HANDLERS[step.id] for step in declared}


STEPS: dict[int, Step] = _steps_by_number()
"""Step handlers by number, in run order."""

TOTAL_STEPS = len(STEPS)
"""Steps in the Smart Clean Install Pipeline."""
