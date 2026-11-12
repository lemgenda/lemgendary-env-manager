"""Active venv detection works with POSIX symlinked interpreters (audit 4.1.1)."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from env_manager import venv_manager
from env_manager.pipeline.venvs import (
    get_active_venv,
    is_inside_active_venv,
    process_runs_from_venv,
)


def _base_interpreter() -> str:
    """An absolute interpreter path that lives outside every test venv."""
    return str(Path(sys.base_prefix).resolve() / "python3.12")


def test_active_venv_comes_from_sys_prefix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The venv is found even when sys.executable resolves outside it."""
    venv = tmp_path / ".venv"
    venv.mkdir()
    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setattr(sys, "executable", _base_interpreter())
    assert get_active_venv() == venv.resolve()
    assert is_inside_active_venv(venv)


def test_no_active_venv_outside_a_venv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With prefix equal to base_prefix the process runs from no venv."""
    monkeypatch.setattr(sys, "prefix", sys.base_prefix)
    assert get_active_venv() is None
    assert not is_inside_active_venv(tmp_path / ".venv")


def test_other_venv_is_not_the_active_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A sibling venv is never mistaken for the running one."""
    running = tmp_path / "a" / ".venv"
    other = tmp_path / "b" / ".venv"
    running.mkdir(parents=True)
    other.mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(running))
    assert not is_inside_active_venv(other)


def test_process_argv0_in_venv_matches_without_resolving_the_leaf(tmp_path: Path) -> None:
    """A Linux process reports a resolved exe but argv[0] still names the venv."""
    venv = tmp_path / ".venv"
    (venv / "bin").mkdir(parents=True)
    candidates = [_base_interpreter(), str(venv / "bin" / "python")]
    assert process_runs_from_venv(candidates, venv)


def test_process_outside_venv_does_not_match(tmp_path: Path) -> None:
    """A base interpreter and a relative argv[0] never match."""
    venv = tmp_path / ".venv"
    venv.mkdir()
    assert not process_runs_from_venv([_base_interpreter(), "python", ""], venv)


def test_find_processes_in_venv_uses_cmdline(tmp_path: Path) -> None:
    """find_processes_in_venv keeps processes whose argv[0] is in the venv."""
    venv = tmp_path / ".venv"
    (venv / "bin").mkdir(parents=True)
    inside = SimpleNamespace(
        pid=1, info={"exe": _base_interpreter(), "cmdline": [str(venv / "bin" / "python")]},
    )
    outside = SimpleNamespace(
        pid=2, info={"exe": _base_interpreter(), "cmdline": ["python", "-m", "x"]},
    )
    no_cmdline = SimpleNamespace(pid=3, info={"exe": None, "cmdline": None})
    with patch.object(venv_manager.psutil, "process_iter",
                      return_value=[inside, outside, no_cmdline]):
        found = venv_manager.find_processes_in_venv(venv)
    assert [p.pid for p in found] == [1]


def test_create_venv_refuses_to_replace_the_running_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """create_venv keeps the active venv intact instead of deleting it."""
    project = tmp_path / "proj"
    venv = project / ".venv"
    venv.mkdir(parents=True)
    monkeypatch.setattr(sys, "prefix", str(venv))
    with patch.object(venv_manager, "is_venv_valid", return_value=True), \
            patch.object(venv_manager, "_remove_existing_venv") as remove:
        ok, _message = venv_manager.create_venv(project)
    assert ok
    remove.assert_not_called()
