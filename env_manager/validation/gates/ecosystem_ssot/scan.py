"""Scan one project's files for ecosystem facts, by file mode.

Code, configuration and tests (``STRICT``) may not spell out any fact.
Documentation and records (``CONSISTENCY``) may, but only with the values
from ``ecosystem.json``. See :mod:`.allowlist` for how a file gets its mode.
"""

from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from env_manager.ecosystem import Ecosystem
from env_manager.validation.files import TEXT_FILE_EXTENSIONS, iter_project_files
from env_manager.validation.gates.ecosystem_ssot.allowlist import (
    FileMode,
    file_mode,
    scannable_lines,
)
from env_manager.validation.gates.ecosystem_ssot.consistency import (
    LegacyFolderRule,
    consistency_rules,
)
from env_manager.validation.gates.ecosystem_ssot.literals import (
    Line,
    ProjectListRule,
    literal_rules,
)
from env_manager.validation.models import FileValidationViolation, read_error


def _strict_findings(
    path: str, lines: Sequence[Line], eco: Ecosystem,
) -> list[FileValidationViolation]:
    found: list[FileValidationViolation] = []
    for rule in literal_rules(eco):
        found.extend(rule.scan(path, lines))
    found.extend(ProjectListRule.for_ecosystem(eco).scan(path, lines))
    found.extend(LegacyFolderRule.for_ecosystem(eco).scan(path, lines))
    return found


def _consistency_findings(
    path: str, lines: Sequence[Line], eco: Ecosystem,
) -> list[FileValidationViolation]:
    return [finding for rule in consistency_rules(eco) for finding in rule.scan(path, lines)]


def scan_file(
    path: Path, relative: PurePosixPath, eco: Ecosystem, mode: FileMode = FileMode.STRICT,
) -> list[FileValidationViolation]:
    """Return the findings in one file (README changelog sections excluded).

    ``mode`` is ``STRICT`` or ``CONSISTENCY``; ``SKIP`` files are never read.
    """
    check = _consistency_findings if mode is FileMode.CONSISTENCY else _strict_findings
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [read_error(path, exc)]
    return check(str(path), list(scannable_lines(relative, text)), eco)


def scan_project_literals(
    project_dir: Path, eco: Ecosystem, asset_project: bool = False,
) -> list[FileValidationViolation]:
    """Return the findings in every scanned text file of the project.

    In an asset project (``kind: asset``) every file is documentation or
    generated records, so every file is checked for consistency only.
    """
    found: list[FileValidationViolation] = []
    for path in iter_project_files(project_dir):
        if path.suffix.lower() not in TEXT_FILE_EXTENSIONS:
            continue
        relative = PurePosixPath(path.relative_to(project_dir).as_posix())
        mode = file_mode(relative, asset_project)
        if mode is not FileMode.SKIP:
            found.extend(scan_file(path, relative, eco, mode))
    return found
