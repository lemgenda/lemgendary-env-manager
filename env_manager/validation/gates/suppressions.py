"""Gate for diagnostic suppressions forbidden by ROBOTS.md.

Flags inline silencing directives (Python, TypeScript/JavaScript, Rust)
and programmatic Python warning filters. Python is read with ``tokenize``
so only real comments count, not strings that mention a directive.
"""

import ast
import io
import re
import tokenize
from pathlib import Path
from typing import TypeGuard

from env_manager._logging import get_logger
from env_manager.validation.files import iter_project_files
from env_manager.validation.models import FileValidationViolation, read_error

_log = get_logger(__name__)

SUPPRESSION = "suppression"
"""Violation type for a forbidden suppression."""

_PY_COMMENT_DIRECTIVES = re.compile(
    r"#\s*(?:type:\s*ignore|noqa|pylint:\s*disable|pragma:\s*no\s+cover|ruff:\s*noqa)",
    re.IGNORECASE,
)
_WARNING_FILTER_CALLS = frozenset({"filterwarnings", "simplefilter"})

_TS_DIRECTIVES = re.compile(
    r"(?://|/\*)\s*(?:@ts-ignore|@ts-expect-error|@ts-nocheck|eslint-disable)"
)
_RUST_ALLOW = re.compile(r"#!?\[\s*allow\s*\(")

_TS_SUFFIXES = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"})


def _violation(path: Path, line: int, text: str) -> FileValidationViolation:
    return FileValidationViolation(
        file_path=str(path),
        line_number=line,
        violation_type=SUPPRESSION,
        message=f"Forbidden diagnostic suppression: {text.strip()[:120]}",
    )


def _is_ignore_filter(node: ast.AST) -> TypeGuard[ast.Call]:
    """True for ``warnings.filterwarnings("ignore", ...)`` or ``simplefilter``."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if not (isinstance(func, ast.Attribute) and func.attr in _WARNING_FILTER_CALLS
            and isinstance(func.value, ast.Name) and func.value.id == "warnings"):
        return False
    actions = [*node.args[:1], *(kw.value for kw in node.keywords if kw.arg == "action")]
    return any(isinstance(a, ast.Constant) and a.value == "ignore" for a in actions)


def scan_python_source(path: Path, text: str) -> list[FileValidationViolation]:
    """Return suppression comments and ``warnings`` ignore filters in Python ``text``.

    A file that does not parse is left to the compile gate.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError) as exc:
        _log.debug("Suppression scan skips unparsable %s: %s", path, exc)
        return []
    found = [
        _violation(path, tok.start[0], tok.string)
        for tok in tokenize.generate_tokens(io.StringIO(text).readline)
        if tok.type == tokenize.COMMENT and _PY_COMMENT_DIRECTIVES.search(tok.string)
    ]
    lines = text.splitlines()
    found.extend(
        _violation(path, node.lineno, lines[node.lineno - 1])
        for node in ast.walk(tree) if _is_ignore_filter(node)
    )
    return found


def _scan_lines(path: Path, text: str, pattern: re.Pattern[str]) -> list[FileValidationViolation]:
    return [
        _violation(path, idx, line)
        for idx, line in enumerate(text.splitlines(), start=1)
        if pattern.search(line)
    ]


def scan_file_for_suppressions(path: Path) -> list[FileValidationViolation]:
    """Return every forbidden suppression in one source file."""
    suffix = path.suffix.lower()
    if suffix not in _TS_SUFFIXES and suffix not in {".py", ".rs"}:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [read_error(path, exc)]
    if suffix == ".py":
        return scan_python_source(path, text)
    if suffix == ".rs":
        return _scan_lines(path, text, _RUST_ALLOW)
    return _scan_lines(path, text, _TS_DIRECTIVES)


def run_suppression_scan(project_dir: Path) -> list[FileValidationViolation]:
    """Scan every Python, TypeScript/JavaScript and Rust file in the project."""
    found: list[FileValidationViolation] = []
    for path in iter_project_files(project_dir):
        found.extend(scan_file_for_suppressions(path))
    return found
