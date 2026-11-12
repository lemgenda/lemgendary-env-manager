"""Per-file gates: Python bytecode compilation and the zero-emoji rule."""

import re
from dataclasses import dataclass, field
from pathlib import Path

from env_manager.validation.files import is_text_source, iter_project_files
from env_manager.validation.models import FileValidationViolation

# Comprehensive emoji regex range (Unicode 15.0 ranges)
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # Emoticons
    "\U0001F300-\U0001F5FF"  # Misc Symbols and Pictographs
    "\U0001F680-\U0001F6FF"  # Transport and Map
    "\U0001F700-\U0001F77F"  # Alchemical Symbols
    "\U0001F780-\U0001F7FF"  # Geometric Shapes Extended
    "\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
    "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
    "\U0001FA00-\U0001FA6F"  # Chess Symbols
    "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
    "\U00002702-\U000027B0"  # Dingbats
    "\U00002300-\U000023FF"  # Misc Technical (hourglass, watch, play and stop buttons)
    "\U00002600-\U000026FF"  # Misc Symbols (sun, warning sign, check boxes, zap)
    "\U00002B00-\U00002BFF"  # Misc Symbols and Arrows (stars, big circles, heavy arrows)
    "\U0001F100-\U0001F251"  # Enclosed Alphanumeric and Ideographic Supplement
    "]+",
    flags=re.UNICODE,
)


def scan_file_for_emojis(file_path: Path) -> list[FileValidationViolation]:
    """Audit single file for emoji presence."""
    violations: list[FileValidationViolation] = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        for idx, line in enumerate(content.splitlines(), start=1):
            if EMOJI_PATTERN.search(line):
                violations.append(
                    FileValidationViolation(
                        file_path=str(file_path),
                        line_number=idx,
                        violation_type="emoji_violation",
                        message="Emoji character detected in source file.",
                    )
                )
    except OSError as exc:
        violations.append(
            FileValidationViolation(
                file_path=str(file_path),
                line_number=0,
                violation_type="read_error",
                message=str(exc),
            )
        )
    return violations


def compile_python_file(file_path: Path) -> FileValidationViolation | None:
    """Compile one Python file in memory and report a syntax error.

    The source is compiled with the built-in ``compile``, so no bytecode is
    written and validation leaves no ``__pycache__`` behind.
    """
    try:
        source = file_path.read_bytes()
        compile(source, str(file_path), "exec", dont_inherit=True)
        return None
    except SyntaxError as err:
        return FileValidationViolation(
            file_path=str(file_path),
            line_number=err.lineno or 0,
            violation_type="syntax_error",
            message=f"{err.msg} ({file_path.name}, line {err.lineno})",
        )
    except (OSError, ValueError) as exc:
        return FileValidationViolation(
            file_path=str(file_path),
            line_number=0,
            violation_type="compilation_error",
            message=str(exc),
        )


@dataclass
class SourceScan:
    """Result of the per-file compile and emoji walk over one project."""

    compiled_files_count: int = 0
    compile_errors: list[FileValidationViolation] = field(default_factory=list)
    emoji_violations: list[FileValidationViolation] = field(default_factory=list)


def scan_sources(project_dir: Path) -> SourceScan:
    """Compile every Python file and scan every text file for emoji."""
    scan = SourceScan()
    for path in iter_project_files(project_dir):
        ext = path.suffix.lower()
        if ext == ".py":
            scan.compiled_files_count += 1
            err = compile_python_file(path)
            if err:
                scan.compile_errors.append(err)
        if is_text_source(path):
            scan.emoji_violations.extend(scan_file_for_emojis(path))
    return scan
