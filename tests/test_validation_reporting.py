"""Docs suite isolation, changelog audit and complete step 6 reporting (audit 4.2.5)."""

import subprocess
from pathlib import Path
from unittest.mock import patch

from env_manager.cli_views.update import validation_lines
from env_manager.ecosystem import ecosystem
from env_manager.pipeline.events import EventPublisher
from env_manager.pipeline.steps import PipelineRun, RunOptions, codebase_verification
from env_manager.validation.domain import docs_hub, project_hygiene
from env_manager.validation.domain.project_hygiene import tracked_changes
from env_manager.validation.models import FileValidationViolation, ProjectValidationReport
from env_manager.validator import iter_workspace_validation

UNITTEST_FAILURES = (
    "..F.E\n"
    "======================================================================\n"
    "FAIL: test_toc (test_documentation.TestDocs.test_toc)\n"
    "----------------------------------------------------------------------\n"
    "ERROR: test_corpus (test_ai_helper_corpus.TestCorpus.test_corpus)\n"
    "FAILED (failures=1, errors=1)\n"
)


def _report(**buckets: list[FileValidationViolation]) -> ProjectValidationReport:
    fields = {name: [] for name in (
        "compile_errors", "emoji_violations", "lint_errors", "yaml_errors", "json_errors",
        "html_errors", "wcag_violations", "domain_errors",
    )}
    fields.update(buckets)
    return ProjectValidationReport(project_name="p", compiled_files_count=1,
                                   passed=not any(buckets.values()), **fields)


def _violation(kind: str) -> FileValidationViolation:
    return FileValidationViolation("f", 1, kind, f"{kind} message")


def test_docs_suite_runs_in_a_subprocess_with_the_docs_venv(tmp_path: Path) -> None:
    """The suite runs in the docs folder with the docs venv, not in-process."""
    (tmp_path / "tests").mkdir()
    venv_python = tmp_path / ".venv" / "Scripts" / "python.exe"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    done = subprocess.CompletedProcess([], 1, stdout="", stderr=UNITTEST_FAILURES)
    with patch.object(docs_hub, "get_venv_python_path", return_value=venv_python), \
            patch.object(docs_hub, "run_tool", return_value=done) as run:
        found = docs_hub.audit_documentation_suite(tmp_path)
    assert run.call_args.args[0][0] == str(venv_python)
    assert run.call_args.args[0][1:4] == ["-m", "unittest", "discover"]
    assert run.call_args.kwargs["cwd"] == tmp_path
    assert [v.message.split("]")[0] for v in found] == ["[FAIL", "[ERROR"]


def test_crashed_docs_suite_fails_closed(tmp_path: Path) -> None:
    """A non-zero exit with no FAIL/ERROR header is a tool failure."""
    (tmp_path / "tests").mkdir()
    crashed = subprocess.CompletedProcess([], 1, stdout="", stderr="No module named yaml")
    with patch.object(docs_hub, "run_tool", return_value=crashed):
        found = docs_hub.audit_documentation_suite(tmp_path)
    assert [v.violation_type for v in found] == ["tool_error"]


def test_untracked_files_are_not_changes() -> None:
    """``??`` lines are skipped; renames keep the new path; leading spaces are kept."""
    porcelain = " M env_manager/cli.py\n?? scratch.txt\nR  old.py -> new.py\nM  README.md\n"
    assert tracked_changes(porcelain) == ["env_manager/cli.py", "new.py", "README.md"]


def _audit(project: Path, status: str, last_commit: str) -> list[str]:
    (project / "README.md").write_text("# P\n\n## Changelog\n", encoding="utf-8")

    def git(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        out = status if cmd[1] == "status" else last_commit
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    with patch.object(project_hygiene, "run_tool", side_effect=git):
        return [v.violation_type for v in project_hygiene.audit_readme_changelog(project)]


def test_only_untracked_files_fall_back_to_the_last_commit(tmp_path: Path) -> None:
    """Untracked files alone do not count; the last commit is checked instead."""
    assert not _audit(tmp_path, "?? notes.txt\n", "README.md\nenv_manager/cli.py\n")


def test_last_commit_without_readme_is_flagged(tmp_path: Path) -> None:
    """A clean tree whose last commit skipped the changelog is flagged."""
    assert _audit(tmp_path, "", "env_manager/cli.py\n") == ["readme_changelog_unupdated"]


def test_failure_summary_names_every_bucket() -> None:
    """JSON and domain findings appear in the summary and the update view."""
    report = _report(json_errors=[_violation("json")], domain_errors=[_violation("domain")])
    assert "JSON: 1" in report.failure_summary()
    assert "Domain: 1" in report.failure_summary()
    lines = validation_lines("p", report)
    assert "JSON: 1" in lines[0]
    assert any("domain message" in line for line in lines[1:])


def test_step6_validates_every_validated_project(fake_workspace: Path) -> None:
    """Step 6 checks the ecosystem's validated projects, not only venv projects."""
    run = PipelineRun(base_dir=fake_workspace, publisher=EventPublisher(total_steps=7),
                      options=RunOptions())
    run.projects = []
    reports = [("a", _report()), ("b", _report(domain_errors=[_violation("domain")]))]
    with patch("env_manager.pipeline.steps.iter_workspace_validation",
               return_value=iter(reports)) as walk:
        events = list(codebase_verification(run))
    walk.assert_called_once_with(fake_workspace, None)
    messages = [e.message for e in events]
    assert any(m.startswith("a: Compiled") for m in messages)
    assert any(m.startswith("b validation failed") and "Domain: 1" in m for m in messages)


def test_workspace_walk_covers_every_validated_project(fake_workspace: Path) -> None:
    """The streaming walk yields one report per validated project present on disk."""
    expected = [p.folder for p in ecosystem().validated_projects()
                if (fake_workspace / p.folder).exists()]
    with patch("env_manager.validator.validate_project", return_value=_report()):
        walked = [name for name, _report in iter_workspace_validation(fake_workspace)]
    assert walked == expected
    assert len(walked) > 1
