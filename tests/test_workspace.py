"""Workspace root resolution and the hermetic test fixture."""

from pathlib import Path

import pytest

from env_manager import workspace
from env_manager.workspace import ENV_MANAGER_DIR, WORKSPACE_ROOT_ENV, workspace_root


def test_default_root_is_parent_of_this_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(WORKSPACE_ROOT_ENV)
    assert workspace_root() == ENV_MANAGER_DIR.parent
    assert ENV_MANAGER_DIR == Path(workspace.__file__).resolve().parent.parent


def test_env_override_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WORKSPACE_ROOT_ENV, str(tmp_path))
    assert workspace_root() == tmp_path.resolve()


def test_fixture_root_lives_in_tmp_path(fake_workspace: Path, tmp_path: Path) -> None:
    assert workspace_root() == fake_workspace.resolve()
    assert fake_workspace.is_relative_to(tmp_path)
    assert (fake_workspace / "lemgendary-docs" / "README.md").is_file()
