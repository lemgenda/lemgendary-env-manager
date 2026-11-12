"""Step 6 writes step-5 upgrades back to the manifests only after validation (audit 4.1.4)."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from env_manager.pipeline.events import EventPublisher
from env_manager.pipeline.steps import PipelineRun, RunOptions, codebase_verification

STEPS = "env_manager.pipeline.steps"


def _run(root: Path, *, upgrades_applied: bool, target: str | None = None) -> PipelineRun:
    run = PipelineRun(base_dir=root, publisher=EventPublisher(total_steps=7),
                      options=RunOptions(target_project=target))
    run.projects = [MagicMock(project_dir=str(root / "p"), is_node_project=False)]
    run.upgrades_applied = upgrades_applied
    return run


def _drive(run: PipelineRun, passed: bool) -> tuple[MagicMock, MagicMock]:
    reports = [("p", MagicMock(passed=passed))]
    with patch(f"{STEPS}.iter_workspace_validation", return_value=iter(reports)), \
            patch(f"{STEPS}.sync_manifests_from_venvs", return_value={"p": (True, "ok")}) as venv, \
            patch(f"{STEPS}.sync_all_manifests", return_value={"p": (True, "ok")}) as central:
        list(codebase_verification(run))
    return venv, central


def test_upgrades_are_written_back_after_validation(tmp_path: Path) -> None:
    """Upgrades are written back after validation."""
    venv, central = _drive(_run(tmp_path, upgrades_applied=True, target="p"), passed=True)
    venv.assert_called_once_with(tmp_path, project="p")
    central.assert_called_once_with(tmp_path)


def test_failed_validation_writes_nothing(tmp_path: Path) -> None:
    """Failed validation writes nothing."""
    venv, central = _drive(_run(tmp_path, upgrades_applied=True), passed=False)
    venv.assert_not_called()
    central.assert_not_called()


def test_no_upgrades_means_no_venv_write_back(tmp_path: Path) -> None:
    """No upgrades means no venv write back."""
    venv, central = _drive(_run(tmp_path, upgrades_applied=False), passed=True)
    venv.assert_not_called()
    central.assert_called_once_with(tmp_path)
