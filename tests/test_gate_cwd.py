"""Every external gate runs its tool from the project directory.

Tools resolve config-relative paths against the process working directory
(yamllint's ``ignore-from-file: .gitignore`` crashed when ``validate`` ran
from the non-git workspace root), so the result must not depend on where
``validate`` was launched.
"""

import subprocess
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

import pytest

from env_manager.validation.gates import data_formats, node_linters, powershell, web
from env_manager.validation.models import TOOL_ERROR, FileValidationViolation

YAMLLINT_CONFIG = "extends: default\nignore-from-file: .gitignore\n"
PAGE = "<!DOCTYPE html><html lang=\"en\"><head><title>x</title></head><body></body></html>\n"


def _elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a project and move the process cwd to a sibling, non-project folder."""
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    return project


def test_yamllint_gitignore_config_from_foreign_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A config reading ``.gitignore`` works although the cwd has no ``.gitignore``."""
    project = _elsewhere(tmp_path, monkeypatch)
    (project / ".yamllint.yaml").write_text(YAMLLINT_CONFIG, encoding="utf-8")
    (project / ".gitignore").write_text("ignored.yaml\n", encoding="utf-8")
    (project / "ok.yaml").write_text("---\nkey: value\n", encoding="utf-8")
    found = data_formats.run_yamllint(project)
    assert TOOL_ERROR not in [v.violation_type for v in found], found


def _markdownlint(project: Path) -> list[FileValidationViolation]:
    (project / "README.md").write_text("# Title\n", encoding="utf-8")
    with patch.object(node_linters, "resolve_tool_cmd", return_value=["markdownlint"]):
        return node_linters.run_markdownlint(project)


def _pa11y(project: Path) -> list[FileValidationViolation]:
    (project / "index.html").write_text(PAGE, encoding="utf-8")
    with patch.object(web, "resolve_tool_cmd", return_value=["pa11y"]):
        return web.run_pa11y_wcag(project)


def _psscriptanalyzer(project: Path) -> list[FileValidationViolation]:
    (project / "a.ps1").write_text("Write-Output 1\n", encoding="utf-8")
    with patch.object(powershell.platform, "system", return_value="Windows"), \
            patch.object(powershell, "preferred_shell", return_value="pwsh"):
        return powershell.run_psscriptanalyzer(project)


def _yamllint(project: Path) -> list[FileValidationViolation]:
    (project / "a.yaml").write_text("---\nkey: value\n", encoding="utf-8")
    return data_formats.run_yamllint(project)


@pytest.mark.parametrize(("module", "gate"), [
    (node_linters, _markdownlint),
    (web, _pa11y),
    (powershell, _psscriptanalyzer),
    (data_formats, _yamllint),
])
def test_gate_runs_tool_in_project_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    module: object,
    gate: Callable[[Path], list[FileValidationViolation]],
) -> None:
    """The tool subprocess gets the project directory as its cwd."""
    project = _elsewhere(tmp_path, monkeypatch)
    done = subprocess.CompletedProcess([], 0, stdout="[]", stderr="")
    with patch.object(module, "run_tool", return_value=done) as run:
        gate(project)
    assert run.call_args_list
    assert all(call.kwargs["cwd"] == project for call in run.call_args_list)
