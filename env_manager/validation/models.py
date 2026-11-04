"""Violation and report records shared by every validation gate, plus the fail-closed helpers."""

import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from env_manager._logging import get_logger

_log = get_logger(__name__)


# Failures reading a JSON/YAML manifest and walking it: unreadable file
# (OSError), invalid JSON or encoding (ValueError), invalid YAML
# (yaml.YAMLError), and a document whose shape differs from the expected
# schema (AttributeError for a non-mapping node, TypeError for a wrong value
# type). Each is reported as a manifest violation.
MANIFEST_READ_ERRORS = (OSError, ValueError, yaml.YAMLError, AttributeError, TypeError)


@dataclass
class FileValidationViolation:
    """Represents a violation found in a file."""
    file_path: str
    line_number: int
    violation_type: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a plain dict for JSON output."""
        return asdict(self)


@dataclass
class ProjectValidationReport:
    """Validation report for a project."""
    project_name: str
    compiled_files_count: int
    compile_errors: list[FileValidationViolation]
    emoji_violations: list[FileValidationViolation]
    lint_errors: list[FileValidationViolation]
    yaml_errors: list[FileValidationViolation]
    json_errors: list[FileValidationViolation]
    html_errors: list[FileValidationViolation]
    wcag_violations: list[FileValidationViolation]
    domain_errors: list[FileValidationViolation]
    passed: bool
    warnings: list[FileValidationViolation] = field(default_factory=list)
    """Findings of warning-severity gates; they do not affect ``passed``."""

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a plain dict for JSON output."""
        return asdict(self)


TOOL_ERROR = "tool_error"
"""Violation type for a lint tool that is missing or crashed: the report fails closed."""


def tool_failure(tool: str, target: Path | str, detail: str) -> FileValidationViolation:
    """Return a violation recording that ``tool`` could not check ``target``.

    A missing or broken tool must fail validation, never report a pass.
    """
    _log.warning("%s could not check %s: %s", tool, target, detail)
    return FileValidationViolation(
        file_path=str(target),
        line_number=0,
        violation_type=TOOL_ERROR,
        message=f"{tool} did not run: {detail[:300]}",
    )


def unexplained_exit(
    tool: str, target: Path | str, proc: subprocess.CompletedProcess, parsed: int,
) -> list[FileValidationViolation]:
    """Return a tool failure when ``proc`` exited non-zero but reported no parsable finding.

    Linters exit non-zero when they find problems; a non-zero exit with
    nothing parsed means the tool itself failed (missing module or package,
    bad config, crash).
    """
    if proc.returncode == 0 or parsed:
        return []
    detail = (proc.stderr or proc.stdout or "").strip() or "no output"
    return [tool_failure(tool, target, f"exit code {proc.returncode}: {detail}")]


def read_error(path: Path, exc: OSError) -> FileValidationViolation:
    """Return the violation for a file that a gate could not read."""
    return FileValidationViolation(
        file_path=str(path), line_number=0, violation_type="read_error", message=str(exc),
    )
