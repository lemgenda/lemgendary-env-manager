"""Which files may hold ecosystem facts, and how strictly each file is checked.

Every scanned file gets a :class:`FileMode`:

- ``SKIP``: the registry itself, its snapshots, generated schemas, lockfiles,
  changelogs and test fixtures. Not scanned.
- ``CONSISTENCY``: human documentation (Markdown, HTML, reStructuredText
  outside changelogs), the model and dataset registries that cite Kaggle
  references and docs pages, and every file of a data-only asset project.
  A port, Kaggle owner or docs URL may be written out, but only with the
  value from ``ecosystem.json``; a wrong value is a finding.
- ``STRICT``: code, configuration and tests. Any literal fact is a finding.
"""

import re
from collections.abc import Iterator
from enum import Enum
from pathlib import PurePosixPath

ALLOWED_FILES = frozenset({
    "ecosystem/ecosystem.json",
    "ecosystem/ecosystem.schema.json",
    "src/generated/ecosystem.json",
    "src-tauri/tauri.conf.json",
})
"""Project-relative paths of the ecosystem file, its schema, the GUI snapshot and the
Tauri config (its CSP must spell out the ports; the CSP parity check covers it)."""

ALLOWED_NAMES = frozenset({
    "REVIEW_REPORT.md", "openapi.json", "package-lock.json", "npm-shrinkwrap.json",
    "pnpm-lock.yaml",
})
"""File names allowed anywhere: review reports, generated API schemas and lockfiles."""

LOCKFILE_SUFFIX = ".lock"
"""Suffix of lockfiles (``Cargo.lock``, ``poetry.lock``, ``yarn.lock``, ``uv.lock``)."""

TEST_DIRS = frozenset({"tests", "test", "__tests__"})
"""Directory names that hold tests."""

FIXTURES_DIR = "fixtures"
"""Directory name of test fixtures (allowed below a test directory)."""

DOCUMENTATION_SUFFIXES = frozenset({".md", ".markdown", ".html", ".htm", ".rst"})
"""Suffixes of human documentation, checked for consistency only."""

RECORD_FILES = frozenset({
    "unified_data.yaml", "unified_models_v2.yaml",
    "canonical_capabilities.json", "canonical_capabilities.yaml",
})
"""Registries whose records cite Kaggle references and docs pages (consistency only)."""

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


class FileMode(Enum):
    """How a file is checked: not at all, for consistent values, or for any literal."""

    SKIP = "skip"
    CONSISTENCY = "consistency"
    STRICT = "strict"


def file_mode(relative: PurePosixPath, asset_project: bool = False) -> FileMode:
    """The check mode of a project-relative file (see the module docstring)."""
    if is_allowlisted(relative):
        return FileMode.SKIP
    if is_test_file(relative):
        return FileMode.STRICT
    if (
        asset_project
        or relative.suffix.lower() in DOCUMENTATION_SUFFIXES
        or relative.name in RECORD_FILES
    ):
        return FileMode.CONSISTENCY
    return FileMode.STRICT


def is_test_file(relative: PurePosixPath) -> bool:
    """True for a file below a test directory or named like a test module."""
    name = relative.name
    return (
        bool(TEST_DIRS.intersection(relative.parts[:-1]))
        or name.startswith("test_")
        or ".test." in name
        or ".spec." in name
    )


def is_allowlisted(relative: PurePosixPath) -> bool:
    """True when the project-relative file may hold ecosystem facts literally."""
    return (
        relative.as_posix() in ALLOWED_FILES
        or relative.name in ALLOWED_NAMES
        or relative.suffix == LOCKFILE_SUFFIX
        or is_changelog_file(relative)
        or in_test_fixtures(relative)
    )


def is_changelog_file(relative: PurePosixPath) -> bool:
    """True for a ``CHANGELOG`` file, which is history and allowed whole."""
    return relative.name.upper().startswith("CHANGELOG")


def in_test_fixtures(relative: PurePosixPath) -> bool:
    """True for a file inside a ``fixtures`` directory below a test directory."""
    parts = relative.parts[:-1]
    return any(
        part == FIXTURES_DIR and TEST_DIRS.intersection(parts[:index])
        for index, part in enumerate(parts)
    )


def is_readme(relative: PurePosixPath) -> bool:
    """True for a Markdown README, whose changelog section is history."""
    return relative.name.upper().startswith("README") and relative.suffix.lower() == ".md"


def scannable_lines(relative: PurePosixPath, text: str) -> Iterator[tuple[int, str]]:
    """Yield ``(line_number, line)`` pairs to scan, skipping README changelog sections."""
    skip_level = 0
    check_sections = is_readme(relative)
    for number, line in enumerate(text.splitlines(), start=1):
        heading = _HEADING.match(line) if check_sections else None
        if heading:
            level = len(heading.group(1))
            if skip_level and level <= skip_level:
                skip_level = 0
            if not skip_level and "changelog" in heading.group(2).lower():
                skip_level = level
        if not skip_level:
            yield number, line
