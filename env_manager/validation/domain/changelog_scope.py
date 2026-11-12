"""Which changed paths need a README changelog entry (single source of truth).

The README changelog gate asks for an entry whenever a change touches
something a release ships: source code, manifests, requirements, runtime
or service configuration, scripts, assets. Three kinds of change are
exempt, because they do not change what a release does:

* docs: Markdown files other than ``README.md`` (the changelog itself), and
  anything under a top-level ``docs/`` folder;
* tests: anything under a ``tests``/``test``/``__tests__`` folder, and test
  files by name (``test_*.py``, ``*_test.py``, ``conftest.py``,
  ``*.test.*``, ``*.spec.*``);
* tooling: CI and hook folders (``.github``, ``.githooks``, ``.husky``,
  ``.vscode``) and the lint, format and git configuration files listed in
  :data:`TOOLING_FILE_PATTERNS`.

Anything not matched is treated as requiring an entry, so a new kind of
file is covered by the gate until it is listed here. ``pyproject.toml`` and
``package.json`` stay covered even though they hold tool settings, because
they are the dependency manifests.
"""

from fnmatch import fnmatchcase
from pathlib import PurePosixPath

README_NAME = "readme.md"
"""Lower-cased name of the file that holds the changelog."""

DOCS_SUFFIXES = frozenset({".md"})
"""Documentation file types, wherever they live (``README.md`` excepted)."""

DOCS_TOP_FOLDERS = frozenset({"docs"})
"""Top-level folders whose whole content is documentation."""

TEST_FOLDERS = frozenset({"tests", "test", "__tests__"})
"""Folder names, at any depth, whose content is tests and fixtures."""

TEST_FILE_PATTERNS = ("test_*.py", "*_test.py", "conftest.py", "*.test.*", "*.spec.*")
"""Test files by name, wherever they live."""

TOOLING_TOP_FOLDERS = frozenset({".github", ".githooks", ".husky", ".vscode"})
"""Top-level CI, git hook and editor folders."""

TOOLING_FILE_PATTERNS = (
    ".gitignore", ".gitattributes", ".editorconfig",
    ".pylintrc", "pylintrc", "ruff.toml", ".ruff.toml", "pyrightconfig.json",
    ".yamllint", ".yamllint.yaml", ".yamllint.yml",
    ".markdownlint*", ".eslintrc*", "eslint.config.*", ".eslintignore",
    ".prettierrc*", ".prettierignore", ".htmlvalidate*", ".pa11yci*",
    "PSScriptAnalyzerSettings.psd1",
)
"""Lint, format and git configuration files by name, wherever they live."""


def _posix(path: str) -> PurePosixPath:
    return PurePosixPath(path.replace("\\", "/"))


def is_readme(path: str) -> bool:
    """True for a ``README.md`` (any case), the file the changelog lives in."""
    return _posix(path).name.lower() == README_NAME


def is_docs_change(path: str) -> bool:
    """A documentation file other than ``README.md``."""
    posix = _posix(path)
    if not posix.name or is_readme(path):
        return False
    in_docs_folder = len(posix.parts) > 1 and posix.parts[0] in DOCS_TOP_FOLDERS
    return in_docs_folder or posix.suffix.lower() in DOCS_SUFFIXES


def is_test_change(path: str) -> bool:
    """A test, fixture or test configuration file."""
    posix = _posix(path)
    if not posix.name:
        return False
    in_test_folder = any(part in TEST_FOLDERS for part in posix.parent.parts)
    return in_test_folder or any(fnmatchcase(posix.name, pat) for pat in TEST_FILE_PATTERNS)


def is_tooling_change(path: str) -> bool:
    """A CI, git hook, editor, lint, format or git configuration file."""
    posix = _posix(path)
    if not posix.name:
        return False
    in_tooling_folder = len(posix.parts) > 1 and posix.parts[0] in TOOLING_TOP_FOLDERS
    return in_tooling_folder or any(
        fnmatchcase(posix.name, pat) for pat in TOOLING_FILE_PATTERNS
    )


def needs_changelog_entry(path: str) -> bool:
    """True when changing ``path`` requires a README changelog entry."""
    if is_readme(path):
        return False
    return not (is_docs_change(path) or is_test_change(path) or is_tooling_change(path))
