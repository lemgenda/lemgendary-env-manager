"""Editable project install, pip detection and a portable GUI verify script (audit 4.1.5)."""

import json
import subprocess
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

from env_manager import bootstrap, venv_manager
from env_manager.pipeline import install, steps
from env_manager.pipeline.install import EDITABLE_INSTALL_STAGE, InstallResult, InstallWarning
from env_manager.pipeline.events import PipelineEvent
from env_manager.venv_manager import install_project_editable, is_installable_project

ENV_MANAGER_DIR = Path(__file__).resolve().parents[1]


def _emit(level: str, message: str, **kwargs: object) -> PipelineEvent:
    return MagicMock(status=level, message=message, data=kwargs.get("data"))


def _collect[R](gen: Generator[PipelineEvent, None, R]) -> tuple[list[PipelineEvent], R]:
    events: list[PipelineEvent] = []
    try:
        while True:
            events.append(next(gen))
    except StopIteration as stop:
        return events, stop.value


def test_only_projects_with_a_build_file_are_installable(tmp_path: Path) -> None:
    """Only projects with a build file are installable."""
    assert not is_installable_project(tmp_path)
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    assert is_installable_project(tmp_path)


def test_editable_install_runs_pip_without_deps(tmp_path: Path) -> None:
    """Editable install runs pip without deps."""
    done = subprocess.CompletedProcess([], 0, "", "")
    with patch.object(venv_manager, "is_venv_valid", return_value=True), \
            patch.object(venv_manager, "run_pip_with_recovery", return_value=done) as pip:
        ok, _msg = install_project_editable(tmp_path)
    assert ok
    assert pip.call_args.args[1] == [
        "install", "--no-deps", "--no-build-isolation", "-e", str(tmp_path),
    ]


def test_editable_install_reports_pip_failure(tmp_path: Path) -> None:
    """Editable install reports pip failure."""
    failed = subprocess.CompletedProcess([], 1, "", "backend missing")
    with patch.object(venv_manager, "is_venv_valid", return_value=True), \
            patch.object(venv_manager, "run_pip_with_recovery", return_value=failed):
        ok, msg = install_project_editable(tmp_path)
    assert not ok
    assert msg == "backend missing"


def _install_with(
    project_dir: Path, editable_ok: bool,
) -> tuple[list[PipelineEvent], InstallResult, MagicMock]:
    (project_dir / "requirements.txt").write_text("rich==15.1.0\n", encoding="utf-8")
    project = MagicMock(project_dir=str(project_dir), is_node_project=False)
    project.name = "proj"

    def python_ok(*_args: object) -> Generator[PipelineEvent, None, bool]:
        yield _emit("info", "installed")
        return True

    def opencv(*_args: object) -> Generator[PipelineEvent, None, None]:
        yield _emit("info", "opencv")

    with patch.object(install, "_install_python", side_effect=python_ok), \
            patch.object(install, "_normalize_opencv", side_effect=opencv), \
            patch.object(install, "install_project_editable",
                         return_value=(editable_ok, "msg")) as editable:
        emit = MagicMock(side_effect=_emit)
        events, result = _collect(install.install_packages([project], MagicMock(), emit))
    return events, result, editable


def test_pipeline_installs_the_project_after_requirements(tmp_path: Path) -> None:
    """Pipeline installs the project after requirements."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    _events, result, editable = _install_with(tmp_path, editable_ok=True)
    assert result == InstallResult()
    editable.assert_called_once_with(tmp_path)


def test_failed_editable_install_is_a_structured_warning(tmp_path: Path) -> None:
    """A failed editable install warns; it does not fail the project install (R2-E2)."""
    (tmp_path / "setup.py").write_text("", encoding="utf-8")
    events, result, _editable = _install_with(tmp_path, editable_ok=False)
    warning = InstallWarning("proj", EDITABLE_INSTALL_STAGE, "msg")
    assert result.failures == []
    assert result.warnings == [warning]
    warned = [event for event in events if event.status == "warning"]
    assert [event.data for event in warned] == [{"install_warning": warning.to_dict()}]


def test_health_matrix_summarizes_install_warnings(tmp_path: Path) -> None:
    """Step 7 lists the install warnings as data and reports a warning status."""
    warning = InstallWarning("proj", EDITABLE_INSTALL_STAGE, "no backend")
    run = MagicMock(base_dir=tmp_path, install_failures=[], install_warnings=[warning])
    run.step.return_value = _emit
    report = MagicMock(overall_healthy=True)
    with patch.object(steps, "run_full_health_audit", return_value=report):
        events, outcome = _collect(steps.health_matrix(run))
    warned = [event for event in events if event.status == "warning"]
    assert [event.data for event in warned] == [{"install_warnings": [warning.to_dict()]}]
    assert outcome.status == "warning"


def test_projects_without_a_build_file_skip_the_editable_install(tmp_path: Path) -> None:
    """Projects without a build file skip the editable install."""
    _events, result, editable = _install_with(tmp_path, editable_ok=False)
    assert result == InstallResult()
    editable.assert_not_called()


def test_pip3_alone_counts_as_pip() -> None:
    """Pip3 alone counts as pip."""
    def which(name: str) -> str | None:
        return "/x" if name == "pip3" else None

    with patch.object(bootstrap.shutil, "which", side_effect=which):
        assert bootstrap.check_pip()


def test_pip_module_counts_as_pip() -> None:
    """Pip module counts as pip."""
    ok = subprocess.CompletedProcess([], 0, "pip 26.0", "")
    with patch.object(bootstrap.shutil, "which", return_value=None), \
            patch.object(bootstrap, "run_command_simple", return_value=ok) as run:
        assert bootstrap.check_pip()
    assert run.call_args.args[0][1:] == ["-m", "pip", "--version"]


def test_missing_pip_is_reported() -> None:
    """Missing pip is reported."""
    with patch.object(bootstrap.shutil, "which", return_value=None), \
            patch.object(bootstrap, "run_command_simple", side_effect=OSError("no python")):
        assert not bootstrap.check_pip()


def test_gui_verify_script_is_cross_platform() -> None:
    """Gui verify script is cross platform."""
    manifest = ENV_MANAGER_DIR / "requirements" / "lemgendary-ai-studio-gui.package.json"
    verify = json.loads(manifest.read_text(encoding="utf-8"))["scripts"]["verify"]
    assert verify.startswith("node -e ")
    assert "\\" not in verify
    for part in ("'Scripts'", "'bin'", "'lem-env.exe'", "'lem-env'", "process.platform"):
        assert part in verify
