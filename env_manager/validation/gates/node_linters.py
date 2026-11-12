"""Node-based lint gates: markdownlint-cli2, ESLint and the TypeScript compiler."""

import json
import subprocess
from pathlib import Path
from typing import Any

from env_manager.validation.models import FileValidationViolation, tool_failure, unexplained_exit
from env_manager.validation.tooling import regex_violations, resolve_tool_cmd, run_tool
from env_manager.workspace import ENV_MANAGER_DIR

ESLINT_SEVERITY_ERROR = 2
"""ESLint message severity for errors (1 is a warning)."""


def run_markdownlint(project_dir: Path) -> list[FileValidationViolation]:
    """Run markdownlint-cli on Markdown files using local binary or npx."""
    violations: list[FileValidationViolation] = []

    skip_dirs = {
        ".git", ".venv", "node_modules", "__pycache__", "dist", "build", "target",
        ".pytest_cache", "raw-sets", "checkpoints", "weights", ".agents", "data",
        "scratch", ".cache",
    }
    md_files = [
        f for f in project_dir.rglob("*.md")
        if not any(part in skip_dirs for part in f.parts)
    ]
    if not md_files:
        return violations

    mgr_dir = ENV_MANAGER_DIR
    cfg = None
    for candidate in [
        project_dir / ".markdownlint.json",
        mgr_dir / ".markdownlint.json",
        project_dir.parent / ".markdownlint.json",
        project_dir.parent / "lemgendary-docs" / ".markdownlint.json",
    ]:
        if candidate.exists():
            cfg = str(candidate)
            break

    cmd = resolve_tool_cmd("markdownlint", "markdownlint-cli")
    if cfg:
        cmd.extend(["-c", cfg])
    cmd.extend([str(f) for f in md_files])

    try:
        proc = run_tool(cmd, timeout=120)
        violations.extend(regex_violations(
            proc.stdout + proc.stderr,
            r"^(?P<path>.+?):(?P<line>\d+)(?::\d+)?\s+(?P<rule>MD\d+/\S+)\s+(?P<msg>.+)$",
            "markdownlint/",
        ))
        violations.extend(unexplained_exit("markdownlint", project_dir, proc, len(violations)))
    except (OSError, subprocess.SubprocessError) as exc:
        violations.append(tool_failure("markdownlint", project_dir, str(exc)))
    return violations


ESLINT_SEVERITY_WARNING = 1
"""ESLint message severity for warnings. Projects lint with ``--max-warnings 0``,
so a warning fails validation just like an error."""

FLAT_ESLINT_CONFIGS = (
    "eslint.config.js", "eslint.config.mjs", "eslint.config.cjs",
    "eslint.config.ts", "eslint.config.mts", "eslint.config.cts",
)
"""ESLint v9 flat configs. ESLint rejects ``--ext`` when one of these is used."""

LEGACY_ESLINT_CONFIGS = (
    ".eslintrc.json", ".eslintrc.js", ".eslintrc.cjs",
    ".eslintrc.yaml", ".eslintrc.yml", ".eslintrc",
)
"""Legacy eslintrc configs, which need ``--ext`` to lint TypeScript files."""

LEGACY_ESLINT_EXTENSIONS = ".js,.jsx,.ts,.tsx"

_ESLINT_VIOLATION_TYPES = {
    ESLINT_SEVERITY_ERROR: "eslint_error",
    ESLINT_SEVERITY_WARNING: "eslint_warning",
}


def _eslint_report_violations(
    report: list[dict[str, Any]], project_dir: Path,
) -> list[FileValidationViolation]:
    """Turn the error and warning messages of an ESLint JSON report into violations."""
    return [
        FileValidationViolation(
            file_path=entry.get("filePath", str(project_dir)),
            line_number=msg.get("line", 0),
            violation_type=_ESLINT_VIOLATION_TYPES[msg["severity"]],
            message=msg.get("message", "").strip(),
        )
        for entry in report
        for msg in entry.get("messages", [])
        if msg.get("severity") in _ESLINT_VIOLATION_TYPES
    ]


def eslint_config_args(project_dir: Path) -> list[str] | None:
    """Extra ESLint arguments for the project's config style, or None without a config.

    A flat config needs no extra arguments; a legacy eslintrc config gets
    ``--ext`` so TypeScript files are linted.
    """
    if any((project_dir / name).exists() for name in FLAT_ESLINT_CONFIGS):
        return []
    if any((project_dir / name).exists() for name in LEGACY_ESLINT_CONFIGS):
        return ["--ext", LEGACY_ESLINT_EXTENSIONS]
    return None


def run_eslint(project_dir: Path) -> list[FileValidationViolation]:
    """Run ESLint on JS/TS source files for a Node.js project.

    Uses ``--format json`` so parsing is exact. The previous compact-format
    regex + broad "any line with ' error '" fallback produced false positives
    from unrelated tool output (Browserslist warnings, deprecation notices).
    """
    violations: list[FileValidationViolation] = []

    config_args = eslint_config_args(project_dir)
    if config_args is None:
        return violations

    src_dir = project_dir / "src"
    lint_target = str(src_dir) if src_dir.exists() else str(project_dir)

    try:
        cmd = [
            *resolve_tool_cmd("eslint", "eslint", project_dir=project_dir),
            lint_target,
            *config_args,
            "--format", "json",
        ]
        proc = run_tool(cmd, cwd=project_dir, timeout=180)

        # ESLint may emit non-JSON noise before the JSON block (Browserslist,
        # deprecation notices, etc.). Find the first '[' and parse from there.
        stdout = proc.stdout or ""
        idx = stdout.find("[")
        if idx < 0:
            # Nothing parseable: ESLint failed to start or crashed.
            detail = (proc.stderr or stdout).strip() or f"exit code {proc.returncode}"
            return [tool_failure("eslint", project_dir, f"no JSON report: {detail}")]

        try:
            violations.extend(_eslint_report_violations(json.loads(stdout[idx:]), project_dir))
        except json.JSONDecodeError as exc:
            return [tool_failure("eslint", project_dir, f"unreadable JSON report: {exc}")]
        # Exit 2 is an ESLint crash or config error, even if a report was printed.
        if proc.returncode not in (0, 1):
            violations.append(tool_failure(
                "eslint", project_dir,
                f"exit code {proc.returncode}: {(proc.stderr or '').strip()}",
            ))
    except (OSError, subprocess.SubprocessError) as exc:
        violations.append(tool_failure("eslint", project_dir, str(exc)))
    return violations


TSCONFIG_FILES = ("tsconfig.json", "tsconfig.node.json")
"""TypeScript projects checked with ``tsc --noEmit``; the node config covers
build tooling such as ``vite.config.ts``."""


def run_typescript_check(project_dir: Path) -> list[FileValidationViolation]:
    """Run ``tsc --noEmit`` for each TypeScript config the project has."""
    violations: list[FileValidationViolation] = []
    for name in TSCONFIG_FILES:
        if (project_dir / name).exists():
            violations.extend(_run_tsc(project_dir, name))
    return violations


def _run_tsc(project_dir: Path, tsconfig: str) -> list[FileValidationViolation]:
    """Run ``tsc -p <tsconfig> --noEmit`` and report its errors."""
    violations: list[FileValidationViolation] = []
    try:
        cmd = [
            *resolve_tool_cmd("tsc", "typescript", project_dir=project_dir),
            "-p", tsconfig, "--noEmit",
        ]
        proc = run_tool(cmd, cwd=project_dir, timeout=180)
        if proc.returncode != 0:
            violations.extend(regex_violations(
                proc.stdout + proc.stderr,
                r"^(?P<path>.+?)\((?P<line>\d+),\d+\):\s+error\s+TS\d+:\s+(?P<msg>.+)$",
                "typescript_error",
            ))
        violations.extend(unexplained_exit("tsc", project_dir, proc, len(violations)))
    except (OSError, subprocess.SubprocessError) as exc:
        violations.append(tool_failure("tsc", project_dir, str(exc)))
    return violations
