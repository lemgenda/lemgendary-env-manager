"""Editable project install, pip detection and a portable GUI verify script (audit 4.1.5)."""

import json
import subprocess
from collections.abc import Generator
from pathlib import Path
from unittest.mock import MagicMock, patch

from env_manager import bootstrap, venv_manager
from env_manager.pipeline import install
from env_manager.pipeline.events import PipelineEvent
from env_manager.venv_manager import install_project_editable, is_installable_project

ENV_MANAGER_DIR = Path(__file__).resolve().parents[1]


def _emit(level: str, message: str, **_kwargs: object) -> PipelineEvent:
    return MagicMock(level=level, message=message)


def _drain(gen: Generator[PipelineEvent, None, list[str]]) -> list[str]:
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        return stop.value


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


def _install_with(project_dir: Path, editable_ok: bool) -> tuple[list[str], MagicMock]:
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
        failures = _drain(install.install_packages([project], MagicMock(), MagicMock()))
    return failures, editable


def test_pipeline_installs_the_project_after_requirements(tmp_path: Path) -> None:
    """Pipeline installs the project after requirements."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    failures, editable = _install_with(tmp_path, editable_ok=True)
    assert failures == []
    editable.assert_called_once_with(tmp_path)


def test_failed_editable_install_is_an_install_failure(tmp_path: Path) -> None:
    """Failed editable install is an install failure."""
    (tmp_path / "setup.py").write_text("", encoding="utf-8")
    failures, _editable = _install_with(tmp_path, editable_ok=False)
    assert failures == ["proj"]


def test_projects_without_a_build_file_skip_the_editable_install(tmp_path: Path) -> None:
    """Projects without a build file skip the editable install."""
    failures, editable = _install_with(tmp_path, editable_ok=False)
    assert failures == []
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
