"""Code validation and zero-emoji verification module.

Performs bytecode compilation (py_compile) and zero-emoji compliance audits
across all source files in the project ecosystem.
"""

import py_compile
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class FileValidationViolation:
    """Represents a violation found in a file."""
    file_path: str
    line_number: int
    violation_type: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert violation to dictionary."""
        return asdict(self)


@dataclass
class ProjectValidationReport:
    """Validation report for a project."""
    project_name: str
    compiled_files_count: int
    compile_errors: List[FileValidationViolation]
    emoji_violations: List[FileValidationViolation]
    passed: bool

    def to_dict(self) -> Dict[str, Any]:
        """Convert report to dictionary."""
        return asdict(self)


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
    "\U0001F100-\U0001F251"  # Enclosed Alphanumeric and Ideographic Supplement
    "]+",
    flags=re.UNICODE,
)


def scan_file_for_emojis(file_path: Path) -> List[FileValidationViolation]:
    """Audit single file for emoji presence."""
    violations: List[FileValidationViolation] = []
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
    except Exception as exc:
        violations.append(
            FileValidationViolation(
                file_path=str(file_path),
                line_number=0,
                violation_type="read_error",
                message=str(exc),
            )
        )
    return violations


def compile_python_file(file_path: Path) -> Optional[FileValidationViolation]:
    """Compile single Python file and catch syntax errors."""
    try:
        py_compile.compile(str(file_path), doraise=True)
        return None
    except py_compile.PyCompileError as err:
        exc_val = getattr(err, "exc_value", None)
        line_num = getattr(exc_val, "lineno", 0) if exc_val else 0
        return FileValidationViolation(
            file_path=str(file_path),
            line_number=line_num or 0,
            violation_type="syntax_error",
            message=str(err),
        )
    except Exception as exc:
        return FileValidationViolation(
            file_path=str(file_path),
            line_number=0,
            violation_type="compilation_error",
            message=str(exc),
        )


def validate_project(project_dir: Path) -> ProjectValidationReport:
    """Execute complete validation suite across all files in a project."""
    compile_errors: List[FileValidationViolation] = []
    emoji_violations: List[FileValidationViolation] = []
    compiled_count = 0

    scannable_extensions = {".py", ".ps1", ".json", ".yaml", ".yml", ".md", ".toml", ".ts", ".tsx", ".html", ".css"}
    skip_dirs = {".git", ".venv", "node_modules", "__pycache__", "dist", "build", "target", ".pytest_cache"}

    for root, dirs, files in Path(project_dir).walk():
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for f in files:
            f_path = root / f
            ext = f_path.suffix.lower()

            if ext == ".py":
                compiled_count += 1
                err = compile_python_file(f_path)
                if err:
                    compile_errors.append(err)

            if ext in scannable_extensions:
                em_errs = scan_file_for_emojis(f_path)
                emoji_violations.extend(em_errs)

    passed = len(compile_errors) == 0 and len(emoji_violations) == 0
    return ProjectValidationReport(
        project_name=project_dir.name,
        compiled_files_count=compiled_count,
        compile_errors=compile_errors,
        emoji_violations=emoji_violations,
        passed=passed,
    )
