"""Gate for hardcoded absolute paths, forbidden by ROBOTS.md.

Flags Windows drive-letter paths, file URLs that spell out a path, and
POSIX home directories in text files. References must resolve relative to the project instead.
"""

import re
from pathlib import Path

from env_manager.validation.files import iter_project_files
from env_manager.validation.models import FileValidationViolation, read_error

ABSOLUTE_PATH = "absolute_path"
"""Violation type for a hardcoded absolute path."""

_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9_])[A-Za-z]:[\\/]{1,2}[A-Za-z0-9_.$~-]{2,}"  # Windows drive path
    r"|\bfile:///[A-Za-z/]"                                  # file URL with a literal path
    r"|(?<![\w.~-])/(?:home|Users)/[A-Za-z0-9_.-]+"          # POSIX home directory
)

PATH_SCAN_EXTENSIONS = frozenset({
    ".py", ".ps1", ".psm1", ".sh", ".bat", ".cmd", ".ts", ".tsx", ".js", ".jsx", ".mjs",
    ".cjs", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".md", ".html", ".css",
    ".ipynb", ".rs",
})
"""Text file types checked for absolute paths."""


def scan_file_for_absolute_paths(path: Path) -> list[FileValidationViolation]:
    """Return one violation per line of ``path`` that holds an absolute path."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [read_error(path, exc)]
    found: list[FileValidationViolation] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        match = _ABSOLUTE_PATH.search(line)
        if match:
            found.append(FileValidationViolation(
                file_path=str(path),
                line_number=idx,
                violation_type=ABSOLUTE_PATH,
                message=f"Hardcoded absolute path: {line.strip()[:120]}",
            ))
    return found


def run_absolute_path_scan(project_dir: Path) -> list[FileValidationViolation]:
    """Scan every text file in the project for hardcoded absolute paths."""
    found: list[FileValidationViolation] = []
    for path in iter_project_files(project_dir):
        if path.suffix.lower() in PATH_SCAN_EXTENSIONS:
            found.extend(scan_file_for_absolute_paths(path))
    return found
