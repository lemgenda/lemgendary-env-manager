"""Files and lines where ecosystem facts may be written out literally.

Everything else that spells out a service port, the Kaggle owner, the docs
URL or a list of project folders should read ``ecosystem.json`` instead.
"""

import re
from collections.abc import Iterator
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

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


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
