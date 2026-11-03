"""Pre-commit hook template and installer (REVIEW_REPORT D-18)."""

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from env_manager.hooks import PRE_COMMIT_TEMPLATE, install_git_hooks

SH = shutil.which("sh")
GIT = shutil.which("git")
needs_sh_and_git = pytest.mark.skipif(not (SH and GIT), reason="needs sh and git")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    """Return ``(project_dir, record_file)`` with a stub POSIX ``lem-env``."""
    project = tmp_path / "lemgendary-datasets"
    project.mkdir()
    _git(project, "init", "-q")
    bin_dir = tmp_path / "lemgendary-env-manager" / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    record = tmp_path / "called.txt"
    stub = bin_dir / "lem-env"
    stub.write_text(f'#!/bin/sh\necho "$@" > "{record.as_posix()}"\n', encoding="utf-8",
                    newline="\n")
    os.chmod(stub, 0o755)
    return project, record


@needs_sh_and_git
@pytest.mark.parametrize("hook_dir", [".githooks", ".git/hooks"])
def test_hook_names_the_project_from_either_location(tmp_path: Path, hook_dir: str) -> None:
    """The copy in ``.git/hooks`` resolves the project name too, not ``.git``."""
    project, record = _workspace(tmp_path)
    hook = project / hook_dir / "pre-commit"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(PRE_COMMIT_TEMPLATE, encoding="utf-8", newline="\n")
    proc = subprocess.run([str(SH), str(hook)], cwd=project, capture_output=True, text=True,
                          check=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert record.read_text(encoding="utf-8").split() == [
        "validate", "--project", "lemgendary-datasets",
    ]


def test_template_supports_posix_venvs() -> None:
    """The hook looks in ``.venv/bin`` as well as ``.venv/Scripts``."""
    assert ".venv/bin" in PRE_COMMIT_TEMPLATE
    assert ".venv/Scripts" in PRE_COMMIT_TEMPLATE
    assert "\r" not in PRE_COMMIT_TEMPLATE


def test_failed_git_config_is_reported(tmp_path: Path) -> None:
    """A failing ``git config core.hooksPath`` is a failed install, not a success."""
    project = tmp_path / "lemgendary-datasets"
    (project / ".git").mkdir(parents=True)
    failed = subprocess.CompletedProcess(args=[], returncode=128, stdout="", stderr="not a repo")
    with patch("env_manager.hooks.subprocess.run", return_value=failed):
        results = install_git_hooks(base_dir=tmp_path)
    assert results == [(
        "lemgendary-datasets", False,
        "git config core.hooksPath failed (exit 128): not a repo",
    )]


@needs_sh_and_git
def test_install_configures_hooks_path(tmp_path: Path) -> None:
    """A successful install sets ``core.hooksPath`` and writes an executable hook."""
    project, _ = _workspace(tmp_path)
    results = install_git_hooks(base_dir=tmp_path)
    assert results == [("lemgendary-datasets", True, "Standardized pre-commit hook installed")]
    assert _git(project, "config", "core.hooksPath").stdout.strip() == ".githooks"
    assert (project / ".githooks" / "pre-commit").read_text(encoding="utf-8") == (
        PRE_COMMIT_TEMPLATE
    )
