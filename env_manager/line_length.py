"""Line length enforcement and reflow engine for Markdown and Python files.

Enforces:
- Markdown (.md): 80 characters max (matching .markdownlint.json / MD013)
- Python (.py): 100 characters max (matching Pylint and PEP 8 ecosystem rules)
"""

from __future__ import annotations

import difflib
import os
import re
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

from env_manager._logging import get_logger

logger = get_logger(__name__)

DEFAULT_MD_LIMIT = 80
DEFAULT_PY_LIMIT = 100
MIN_ATOMIC_LINK_LEN = 30
MATH_FENCE_DELIM_LEN = 2
PREVIEW_TRUNCATION_LIMIT = 75

SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    "dist",
    "build",
    "target",
    ".pytest_cache",
    ".ruff_cache",
    "checkpoints",
    "weights",
    "raw-sets",
    "data",
    ".agents",
    "scratch",
    ".cache",
}


@dataclass
class LineViolation:
    """Encapsulates a single line length defect."""

    file_path: Path
    line_number: int
    length: int
    limit: int
    line_content: str


@dataclass
class GuardOptions:
    """Options parameterizing line length audit and reflow."""

    md_limit: int = DEFAULT_MD_LIMIT
    py_limit: int = DEFAULT_PY_LIMIT
    ignore_code_blocks: bool = True
    ignore_headings: bool = False
    fix: bool = False
    show_diff: bool = False


def _staged_in_dir(repo_dir: Path) -> list[Path]:
    """Retrieve staged files in a single git repository."""
    try:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.SubprocessError, OSError) as err:
        logger.debug("Failed to query git staged files in %s: %s", repo_dir, err)
        return []

    staged: list[Path] = []
    for line in proc.stdout.splitlines():
        trimmed = line.strip()
        if not trimmed:
            continue
        p = (repo_dir / trimmed).resolve()
        if p.is_file() and p.suffix.lower() in (".md", ".py"):
            staged.append(p)
    return staged


def get_staged_files(repo_root: Path) -> list[Path]:
    """Retrieve staged Markdown and Python files via Git (supports mono/multi-repo)."""
    if (repo_root / ".git").exists():
        return _staged_in_dir(repo_root)

    staged: list[Path] = []
    try:
        for child in repo_root.iterdir():
            if child.is_dir() and (child / ".git").exists():
                staged.extend(_staged_in_dir(child))
    except OSError as err:
        logger.debug("Failed to inspect workspace root for git submodules: %s", err)
    return staged


def is_table_row(line: str) -> bool:
    """Check if line is a Markdown table row."""
    s = line.strip()
    return s.startswith("|") or s.endswith("|") or "|---" in s


def is_math_fence(line: str) -> bool:
    """Check if line opens or closes a display math block."""
    s = line.strip()
    return s == "$$" or s.startswith("$$")


def is_code_fence(line: str) -> bool:
    """Check if line opens or closes a fenced code block."""
    s = line.strip()
    return s.startswith("```") or s.startswith("~~~")


def is_list_item(line: str) -> tuple[bool, str, str]:
    """Check if line is a bullet/numbered list or blockquote."""
    m = re.match(r"^(\s*(?:[-*+]|\d+\.|\>)\s+)(.*)$", line)
    if m:
        return True, m.group(1), m.group(2)
    return False, "", line


def is_heading(line: str) -> bool:
    """Check if line is a Markdown heading."""
    return re.match(r"^\s*#{1,6}\s+", line) is not None


def is_url_or_link_def(line: str) -> bool:
    """Check if line is a URL, link definition, or line with an atomic link URL."""
    s = line.strip()
    if re.match(r"^https?://\S+$", s) or re.match(r"^\[[^\]]+\]:\s*\S+$", s):
        return True
    link_m = re.search(r"\]\(([^\s)]+)\)", line)
    if link_m and len(link_m.group(1)) > MIN_ATOMIC_LINK_LEN:
        remainder = line[: link_m.start() + 2] + line[link_m.end() :]
        if len(remainder) <= DEFAULT_MD_LIMIT:
            return True
    return False


def _check_md_line(
    line: str,
    idx: int,
    path: Path,
    opts: GuardOptions,
) -> LineViolation | None:
    """Check one Markdown line for length violation."""
    if is_table_row(line) or is_url_or_link_def(line):
        return None
    if opts.ignore_headings and is_heading(line):
        return None
    if len(line) > opts.md_limit:
        return LineViolation(
            file_path=path,
            line_number=idx,
            length=len(line),
            limit=opts.md_limit,
            line_content=line,
        )
    return None


def check_markdown_file(path: Path, opts: GuardOptions | None = None) -> list[LineViolation]:
    """Audit a Markdown file for line length violations."""
    options = opts or GuardOptions()
    violations: list[LineViolation] = []
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as err:
        logger.error("Failed to read Markdown file %s: %s", path, err)
        return violations

    in_code = False
    in_math = False
    for idx, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.rstrip("\r\n")
        stripped = line.strip()

        if is_code_fence(line):
            in_code = not in_code
            continue
        if in_code and options.ignore_code_blocks:
            continue

        if is_math_fence(line):
            in_math = not in_math
            if in_math and stripped.endswith("$$") and len(stripped) > MATH_FENCE_DELIM_LEN:
                in_math = False
            continue
        if in_math:
            continue

        v = _check_md_line(line, idx, path, options)
        if v is not None:
            violations.append(v)
    return violations


def check_python_file(path: Path, limit: int = DEFAULT_PY_LIMIT) -> list[LineViolation]:
    """Audit a Python file for line length violations."""
    violations: list[LineViolation] = []
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as err:
        logger.error("Failed to read Python file %s: %s", path, err)
        return violations

    for idx, raw_line in enumerate(content.splitlines(), start=1):
        line = raw_line.rstrip("\r\n")
        if len(line) > limit:
            violations.append(
                LineViolation(
                    file_path=path,
                    line_number=idx,
                    length=len(line),
                    limit=limit,
                    line_content=line,
                )
            )
    return violations


def audit_file(path: Path, opts: GuardOptions | None = None) -> list[LineViolation]:
    """Audit a single target file based on its file extension."""
    options = opts or GuardOptions()
    suffix = path.suffix.lower()
    if suffix == ".md":
        return check_markdown_file(path, options)
    if suffix == ".py":
        return check_python_file(path, limit=options.py_limit)
    return []


def discover_files(targets: list[Path]) -> list[Path]:
    """Recursively collect Markdown and Python files from target paths."""
    discovered: list[Path] = []
    for tgt in targets:
        if tgt.is_file():
            if tgt.suffix.lower() in (".md", ".py"):
                discovered.append(tgt)
        elif tgt.is_dir():
            for root, dirs, files in os.walk(tgt):
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
                for file_name in files:
                    ext = os.path.splitext(file_name)[1].lower()
                    if ext in (".md", ".py"):
                        discovered.append(Path(root) / file_name)
    return sorted(discovered)


def _wrap_prose_line(line: str, width: int) -> list[str]:
    """Wrap a prose or list line with proper indent preservation."""
    if len(line) <= width or is_table_row(line) or is_url_or_link_def(line) or is_heading(line):
        return [line]

    is_list, prefix, content = is_list_item(line)
    if is_list:
        sub_indent = " " * len(prefix)
        wrapped = textwrap.wrap(
            content,
            width=width - len(prefix),
            break_long_words=False,
            break_on_hyphens=False,
        )
        if not wrapped:
            return [line]
        return [prefix + wrapped[0]] + [sub_indent + s for s in wrapped[1:]]

    indent_m = re.match(r"^(\s*)", line)
    indent = indent_m.group(1) if indent_m else ""
    wrapped = textwrap.wrap(
        line.strip(),
        width=width - len(indent),
        break_long_words=False,
        break_on_hyphens=False,
    )
    if not wrapped:
        return [line]
    return [indent + w for w in wrapped]


def reflow_markdown(content: str, width: int = DEFAULT_MD_LIMIT) -> str:
    """Reflow Markdown text content to fit within ``width`` columns."""
    lines = content.splitlines(keepends=False)
    out_lines: list[str] = []

    in_code = False
    in_math = False
    for line in lines:
        stripped = line.strip()

        if is_code_fence(line):
            in_code = not in_code
            out_lines.append(line)
            continue
        if in_code:
            out_lines.append(line)
            continue

        if is_math_fence(line):
            in_math = not in_math
            if in_math and stripped.endswith("$$") and len(stripped) > MATH_FENCE_DELIM_LEN:
                in_math = False
            out_lines.append(line)
            continue
        if in_math:
            out_lines.append(line)
            continue

        if not stripped:
            out_lines.append("")
            continue

        out_lines.extend(_wrap_prose_line(line, width=width))

    ending = "\n" if content.endswith("\n") else ""
    return "\n".join(out_lines) + ending


def reflow_python(content: str, width: int = DEFAULT_PY_LIMIT) -> str:
    """Reflow comments and multiline docstrings in Python source."""
    lines = content.splitlines(keepends=False)
    out_lines: list[str] = []

    in_doc = False
    delim = ""

    for line in lines:
        stripped = line.strip()

        if '"""' in stripped or "'''" in stripped:
            d = '"""' if '"""' in stripped else "'''"
            if not in_doc:
                if stripped.count(d) == 1:
                    in_doc, delim = True, d
            elif d == delim:
                in_doc = False
            out_lines.append(line)
            continue

        if in_doc:
            if len(line) > width and not stripped.startswith("@"):
                indent_m = re.match(r"^(\s*)", line)
                indent = indent_m.group(1) if indent_m else ""
                wrapped = textwrap.wrap(
                    stripped,
                    width=width - len(indent),
                    break_long_words=False,
                    break_on_hyphens=False,
                )
                if wrapped:
                    out_lines.extend([indent + w for w in wrapped])
                    continue
            out_lines.append(line)
            continue

        if stripped.startswith("#") and len(line) > width:
            indent_m = re.match(r"^(\s*)#\s*", line)
            if indent_m:
                prefix = indent_m.group(0)
                comment_text = line[len(prefix):]
                wrapped = textwrap.wrap(
                    comment_text,
                    width=width - len(prefix),
                    break_long_words=False,
                    break_on_hyphens=False,
                )
                if wrapped:
                    out_lines.extend([prefix + w for w in wrapped])
                    continue

        out_lines.append(line)

    ending = "\n" if content.endswith("\n") else ""
    return "\n".join(out_lines) + ending


def process_file(
    path: Path,
    opts: GuardOptions | None = None,
) -> bool:
    """Format a target file; returns True if reflowing modified content."""
    options = opts or GuardOptions()
    try:
        original = path.read_text(encoding="utf-8")
    except OSError as err:
        logger.error("Failed to read file %s: %s", path, err)
        return False

    suffix = path.suffix.lower()
    if suffix == ".md":
        reflowed = reflow_markdown(original, width=options.md_limit)
    elif suffix == ".py":
        reflowed = reflow_python(original, width=options.py_limit)
    else:
        return False

    if reflowed == original:
        return False

    if options.show_diff:
        diff = difflib.unified_diff(
            original.splitlines(),
            reflowed.splitlines(),
            fromfile=f"a/{path.name}",
            tofile=f"b/{path.name}",
            lineterm="",
        )
        print(f"\n--- {path} diff ---")
        for d in diff:
            print(d)

    if options.fix:
        try:
            path.write_text(reflowed, encoding="utf-8", newline="\n")
        except OSError as err:
            logger.error("Failed to write formatted file %s: %s", path, err)
            return False

    return True


def run_check(files: list[Path], opts: GuardOptions, repo_root: Path) -> int:
    """Execute check command against a list of files."""
    violations: list[LineViolation] = []
    for f in files:
        violations.extend(audit_file(f, opts=opts))

    if not violations:
        print(f"[LINE-GUARD] Checked {len(files)} file(s): ALL COMPLIANT (0 violations).")
        return 0

    print(f"\n[LINE-GUARD] Defect detected: {len(violations)} line length violation(s):")
    for v in violations:
        rel = (
            v.file_path.relative_to(repo_root)
            if v.file_path.is_relative_to(repo_root)
            else v.file_path
        )
        preview = (
            v.line_content[:PREVIEW_TRUNCATION_LIMIT] + "..."
            if len(v.line_content) > PREVIEW_TRUNCATION_LIMIT
            else v.line_content
        )
        print(f"  {rel}:{v.line_number}: line length {v.length} > {v.limit} | {preview}")

    print("\n[REMEDIATION] Run 'python tools/line_length_guard.py fix' to auto-wrap lines.")
    return 1


def run_fix(files: list[Path], opts: GuardOptions, repo_root: Path) -> int:
    """Execute fix / reflow command against a list of files."""
    modified_count = 0
    for f in files:
        changed = process_file(f, opts=opts)
        if changed:
            modified_count += 1
            action = "Formatted" if opts.fix else "Needs formatting"
            rel = f.relative_to(repo_root) if f.is_relative_to(repo_root) else f
            print(f"[{action}] {rel}")

    if modified_count == 0:
        print(f"[LINE-GUARD] Checked {len(files)} file(s): all lines within bounds.")
        return 0

    if not opts.fix:
        print(
            f"\n[LINE-GUARD] {modified_count} file(s) require reflow. "
            "Re-run with 'fix' (or '--fix') to apply changes."
        )
        return 1

    print(f"\n[LINE-GUARD] Successfully formatted {modified_count} file(s).")
    return 0
