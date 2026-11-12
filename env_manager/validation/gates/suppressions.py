"""Gate for diagnostic suppressions forbidden by ROBOTS.md.

Flags inline silencing directives (Python, TypeScript/JavaScript, Rust),
programmatic Python warning filters, silent Python ``except`` blocks, empty
JS/TS ``catch`` blocks, PowerShell ``SilentlyContinue``/``Ignore`` error
actions and rule disables in linter config files. Python is read with
``tokenize`` and ``ast`` so only real code and comments count, not strings
that mention a directive.
"""

import ast
import io
import re
import tokenize
from pathlib import Path
from typing import TypeGuard

from env_manager._logging import get_logger
from env_manager.validation.files import iter_project_files
from env_manager.validation.gates.config_disables import scan_config_disables
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

_EMPTY_CATCH = re.compile(
    r"\bcatch\s*(?:\([^)]*\))?\s*\{\s*(?:(?://[^\n]*\n|/\*.*?\*/)\s*)*\}",
    re.DOTALL,
)
"""A JS/TS ``catch`` block that is empty or holds only comments."""

_PS_SUFFIXES = frozenset({".ps1", ".psm1"})
_PS_SILENT_ERRORS = re.compile(
    r"-(?:ErrorAction|EA)\s*:?\s*['\"]?(?:SilentlyContinue|Ignore)\b"
    r"|\$ErrorActionPreference\s*=\s*['\"]?(?:SilentlyContinue|Ignore)\b",
    re.IGNORECASE,
)
"""PowerShell error actions that drop errors without a trace."""


def _violation(path: Path, line: int, text: str) -> FileValidationViolation:
    return FileValidationViolation(
        file_path=str(path),
        line_number=line,
        violation_type=SUPPRESSION,
        message=f"Forbidden diagnostic suppression: {text.strip()[:120]}",
    )


def _is_warning_filter_call(func: ast.expr) -> bool:
    """``warnings.filterwarnings`` / ``simplefilter``, or the bare imported name."""
    if isinstance(func, ast.Name):
        return func.id in _WARNING_FILTER_CALLS
    return (isinstance(func, ast.Attribute) and func.attr in _WARNING_FILTER_CALLS
            and isinstance(func.value, ast.Name) and func.value.id == "warnings")


def _is_ignore_filter(node: ast.AST) -> TypeGuard[ast.Call]:
    """True for ``filterwarnings("ignore", ...)`` or ``simplefilter("ignore")``."""
    if not isinstance(node, ast.Call) or not _is_warning_filter_call(node.func):
        return False
    actions = [*node.args[:1], *(kw.value for kw in node.keywords if kw.arg == "action")]
    return any(isinstance(a, ast.Constant) and a.value == "ignore" for a in actions)


def _is_silent_statement(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Pass) or (
        isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant)
        and stmt.value.value is Ellipsis
    )


def is_silent_handler(node: ast.AST) -> TypeGuard[ast.ExceptHandler]:
    """True for an ``except`` block whose body is only ``pass`` or ``...``.

    Such a block swallows the error with no log, no re-raise and no
    fallback value.
    """
    return isinstance(node, ast.ExceptHandler) and all(
        _is_silent_statement(stmt) for stmt in node.body
    )


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
    for node in ast.walk(tree):
        if _is_ignore_filter(node):
            found.append(_violation(path, node.lineno, lines[node.lineno - 1]))
        elif is_silent_handler(node):
            found.append(_violation(path, node.lineno, f"silent except: {lines[node.lineno - 1]}"))
    return found


def _scan_lines(path: Path, text: str, pattern: re.Pattern[str]) -> list[FileValidationViolation]:
    return [
        _violation(path, idx, line)
        for idx, line in enumerate(text.splitlines(), start=1)
        if pattern.search(line)
    ]


def scan_script_source(path: Path, text: str) -> list[FileValidationViolation]:
    """Return JS/TS directive comments and empty ``catch`` blocks."""
    found = _scan_lines(path, text, _TS_DIRECTIVES)
    found.extend(
        _violation(path, text.count("\n", 0, m.start()) + 1, f"empty catch: {m.group(0)}")
        for m in _EMPTY_CATCH.finditer(text)
    )
    return found


def scan_powershell_source(path: Path, text: str) -> list[FileValidationViolation]:
    """Return PowerShell code lines that silence errors (comment lines are skipped)."""
    return [
        _violation(path, idx, line)
        for idx, line in enumerate(text.splitlines(), start=1)
        if not line.lstrip().startswith("#") and _PS_SILENT_ERRORS.search(line)
    ]


def scan_file_for_suppressions(path: Path) -> list[FileValidationViolation]:
    """Return every forbidden suppression in one source or config file."""
    suffix = path.suffix.lower()
    if suffix not in _TS_SUFFIXES | _PS_SUFFIXES | {".py", ".rs"}:
        return scan_config_disables(path)
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [read_error(path, exc)]
    if suffix == ".py":
        return scan_python_source(path, text)
    if suffix == ".rs":
        return _scan_lines(path, text, _RUST_ALLOW)
    if suffix in _PS_SUFFIXES:
        return scan_powershell_source(path, text)
    return scan_script_source(path, text) + scan_config_disables(path)


def run_suppression_scan(project_dir: Path) -> list[FileValidationViolation]:
    """Scan every source file and linter config file in the project."""
    found: list[FileValidationViolation] = []
    for path in iter_project_files(project_dir):
        found.extend(scan_file_for_suppressions(path))
    return found
