"""Docs-only, test-only and tooling-only changes need no changelog entry (R2-E3)."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from env_manager.validation.domain import project_hygiene
from env_manager.validation.domain.changelog_scope import needs_changelog_entry

EXEMPT_PATHS = (
    "REVIEW_REPORT.md",
    "docs/guide.html",
    "MD-Papers/paper.md",
    "tests/test_cli.py",
    "tests/fixtures/sample.json",
    "src/__tests__/App.tsx",
    "src/App.test.tsx",
    "conftest.py",
    ".github/workflows/ci.yml",
    ".githooks/pre-commit",
    ".pylintrc",
    ".markdownlint.json",
    ".yamllint.yaml",
    "eslint.config.js",
    ".gitignore",
)

COVERED_PATHS = (
    "env_manager/cli.py",
    "src/App.tsx",
    "pyproject.toml",
    "package.json",
    "requirements.txt",
    "requirements/runtime_env.yaml",
    "ecosystem/ecosystem.json",
    "lemgendary_env_manager.ps1",
    "papers/paper.html",
    "docs",
)


@pytest.mark.parametrize("path", EXEMPT_PATHS)
def test_docs_tests_and_tooling_need_no_entry(path: str) -> None:
    """Docs, tests and tooling files are exempt from the changelog rule."""
    assert not needs_changelog_entry(path)


@pytest.mark.parametrize("path", COVERED_PATHS)
def test_source_manifests_and_config_need_an_entry(path: str) -> None:
    """Source, manifest, requirements and runtime config files need an entry."""
    assert needs_changelog_entry(path)


def test_readme_itself_needs_no_entry() -> None:
    """The README holds the changelog, so changing it alone needs nothing more."""
    assert not needs_changelog_entry("README.md")


def test_windows_separators_are_understood() -> None:
    """Backslash paths classify like their forward-slash form."""
    assert not needs_changelog_entry("tests\\unit\\helpers.py")
    assert needs_changelog_entry("env_manager\\cli.py")


def _audit_last_commit(project: Path, last_commit: str) -> list[str]:
    (project / "README.md").write_text("# P\n\n## Changelog\n", encoding="utf-8")

    def git(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        out = "" if cmd[1] == "status" else last_commit
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    with patch.object(project_hygiene, "run_tool", side_effect=git):
        return [v.violation_type for v in project_hygiene.audit_readme_changelog(project)]


def test_docs_and_tooling_only_commit_passes(tmp_path: Path) -> None:
    """A last commit touching only docs, tests and tooling needs no README entry."""
    commit = "REVIEW_REPORT.md\ntests/test_cli.py\n.github/workflows/ci.yml\n.pylintrc\n"
    assert not _audit_last_commit(tmp_path, commit)


def test_mixed_commit_without_readme_is_flagged(tmp_path: Path) -> None:
    """One source file among docs changes still needs the README entry."""
    commit = "REVIEW_REPORT.md\ntests/test_cli.py\nenv_manager/cli.py\n"
    assert _audit_last_commit(tmp_path, commit) == ["readme_changelog_unupdated"]


def test_manifest_commit_with_readme_passes(tmp_path: Path) -> None:
    """A manifest change with a README changelog update passes."""
    assert not _audit_last_commit(tmp_path, "requirements.txt\nREADME.md\n")
