"""Scan one project's files for literal ecosystem facts."""

from pathlib import Path, PurePosixPath

from env_manager.ecosystem import Ecosystem
from env_manager.validation.files import TEXT_FILE_EXTENSIONS, iter_project_files
from env_manager.validation.gates.ecosystem_ssot.allowlist import is_allowlisted, scannable_lines
from env_manager.validation.gates.ecosystem_ssot.literals import (
    ProjectListRule,
    literal_rules,
)
from env_manager.validation.models import FileValidationViolation, read_error


def scan_file(
    path: Path, relative: PurePosixPath, eco: Ecosystem,
) -> list[FileValidationViolation]:
    """Return the literal facts in one file (README changelog sections excluded)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [read_error(path, exc)]
    lines = list(scannable_lines(relative, text))
    found: list[FileValidationViolation] = []
    for rule in literal_rules(eco):
        found.extend(rule.scan(str(path), lines))
    found.extend(ProjectListRule.for_ecosystem(eco).scan(str(path), lines))
    return found


def scan_project_literals(project_dir: Path, eco: Ecosystem) -> list[FileValidationViolation]:
    """Return the literal facts in every non-allowlisted text file of the project."""
    found: list[FileValidationViolation] = []
    for path in iter_project_files(project_dir):
        relative = PurePosixPath(path.relative_to(project_dir).as_posix())
        if path.suffix.lower() in TEXT_FILE_EXTENSIONS and not is_allowlisted(relative):
            found.extend(scan_file(path, relative, eco))
    return found
