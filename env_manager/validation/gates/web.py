"""Static HTML gates: W3C html-validate and pa11y WCAG 2.2 AA.

Both gates read the same file list (:func:`html_files`), built by the shared
project walker so build output, venvs and hidden folders are skipped the
same way as in every other per-file gate. Both parse JSON reports, so each
finding names the exact ``.html`` file it came from.
"""

import json
import subprocess
from pathlib import Path
from typing import Any

from env_manager.validation.files import iter_project_files
from env_manager.validation.models import FileValidationViolation, tool_failure, unexplained_exit
from env_manager.validation.tooling import resolve_tool_cmd, run_tool

HTML_VALIDATE_SEVERITIES = {2: "html_error", 1: "html_warning"}
"""html-validate JSON severity to violation type."""

PA11Y_TIMEOUT_S = 120
"""Upper bound for one pa11y page audit."""


def html_files(project_dir: Path) -> list[Path]:
    """Every ``.html`` file a static HTML gate checks, in a stable order."""
    return sorted(p for p in iter_project_files(project_dir) if p.suffix == ".html")


def _json_payload(text: str, opener: str) -> Any:
    """Parse the JSON document in ``text`` that starts at the first ``opener``.

    Raises ``ValueError`` when there is none or it does not parse.
    """
    start = text.find(opener)
    if start < 0:
        raise ValueError("no JSON report in tool output")
    return json.loads(text[start:])


def _unreadable_report(
    tool: str, target: Path, proc: subprocess.CompletedProcess, exc: ValueError,
) -> FileValidationViolation:
    """Tool failure for output that holds no readable JSON report."""
    detail = (proc.stderr or proc.stdout or "").strip() or str(exc)
    return tool_failure(tool, target, f"exit code {proc.returncode}: {detail}")


def parse_html_validate_json(stdout: str) -> list[FileValidationViolation]:
    """Turn ``html-validate --formatter json`` output into per-file violations."""
    report = _json_payload(stdout, "[") if stdout.strip() else []
    return [
        FileValidationViolation(
            file_path=str(entry.get("filePath", "")),
            line_number=int(msg.get("line") or 0),
            violation_type=HTML_VALIDATE_SEVERITIES[msg["severity"]],
            message=f"{msg.get('ruleId', 'unknown')} - {msg.get('message', '')}".strip(),
        )
        for entry in report
        for msg in entry.get("messages", [])
        if msg.get("severity") in HTML_VALIDATE_SEVERITIES
    ]


def run_html_validate(project_dir: Path) -> list[FileValidationViolation]:
    """Run html-validate (W3C HTML validator) on every static HTML file."""
    files = html_files(project_dir)
    if not files:
        return []
    cmd = [
        *resolve_tool_cmd("html-validate", "html-validate"),
        "--formatter", "json",
        *(str(f) for f in files),
    ]
    try:
        proc = run_tool(cmd, cwd=project_dir, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        return [tool_failure("html-validate", project_dir, str(exc))]
    try:
        violations = parse_html_validate_json(proc.stdout or "")
    except ValueError as exc:
        return [_unreadable_report("html-validate", project_dir, proc, exc)]
    for v in violations:
        if v.file_path and not Path(v.file_path).is_absolute():
            v.file_path = str(project_dir / v.file_path)
    return violations + unexplained_exit("html-validate", project_dir, proc, len(violations))


def parse_pa11y_json(stdout: str, html_file: Path) -> list[FileValidationViolation]:
    """Turn ``pa11y --reporter json`` output for one page into violations."""
    issues = _json_payload(stdout, "[") if stdout.strip() else []
    return [
        FileValidationViolation(
            file_path=str(html_file),
            line_number=0,
            violation_type="wcag_aa_violation",
            message=f"{issue.get('code', '')}: {issue.get('message', '')} "
                    f"({issue.get('selector', '')})".strip(),
        )
        for issue in issues
    ]


def _audit_page(html_file: Path) -> list[FileValidationViolation]:
    """Run pa11y on one page and report its issues, or a tool failure."""
    cmd = [
        *resolve_tool_cmd("pa11y", "pa11y"),
        "--standard", "WCAG2AA",
        "--reporter", "json",
        html_file.resolve().as_uri(),
    ]
    try:
        proc = run_tool(cmd, timeout=PA11Y_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as exc:
        return [tool_failure("pa11y", html_file, str(exc))]
    try:
        found = parse_pa11y_json(proc.stdout or "", html_file)
    except ValueError as exc:
        return [_unreadable_report("pa11y", html_file, proc, exc)]
    return found + unexplained_exit("pa11y", html_file, proc, len(found))


def run_pa11y_wcag(project_dir: Path) -> list[FileValidationViolation]:
    """Run a pa11y WCAG 2.2 AA audit on every static HTML file."""
    violations: list[FileValidationViolation] = []
    for html_file in html_files(project_dir):
        violations.extend(_audit_page(html_file))
    return violations
