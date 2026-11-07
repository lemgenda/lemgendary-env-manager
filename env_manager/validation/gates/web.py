"""Static HTML gates: W3C html-validate and pa11y WCAG 2.2 AA."""

import re
import subprocess
from pathlib import Path

from env_manager.validation.models import FileValidationViolation, tool_failure, unexplained_exit
from env_manager.validation.tooling import resolve_tool_cmd, run_tool


def run_html_validate(project_dir: Path) -> list[FileValidationViolation]:
    """Run html-validate (W3C HTML validator) on static HTML files."""
    violations: list[FileValidationViolation] = []

    html_files = [
        f for f in project_dir.rglob("*.html")
        if not any(skip in f.parts for skip in (".venv", "node_modules", ".git"))
    ]
    if not html_files:
        return violations

    try:
        cmd = (
            resolve_tool_cmd("html-validate", "html-validate")
            + ["--formatter", "text"]
            + [str(f) for f in html_files]
        )
        proc = run_tool(cmd, cwd=project_dir, timeout=120)
        combined = proc.stdout + proc.stderr
        for line in combined.splitlines():
            m = re.match(r"^\s*(\d+):(\d+)\s+(error|warning)\s+(.+?)\s+(.+)$", line.strip())
            if m:
                violations.append(
                    FileValidationViolation(
                        file_path=str(project_dir),
                        line_number=int(m.group(1)),
                        violation_type=f"html_{m.group(3)}",
                        message=f"{m.group(4)} — {m.group(5)}",
                    )
                )
        violations.extend(unexplained_exit("html-validate", project_dir, proc, len(violations)))
    except (OSError, subprocess.SubprocessError) as exc:
        violations.append(tool_failure("html-validate", project_dir, str(exc)))
    return violations


def run_pa11y_wcag(project_dir: Path) -> list[FileValidationViolation]:
    """Run pa11y WCAG 2.2 AA accessibility audit against static HTML files."""
    violations: list[FileValidationViolation] = []

    html_files = [
        f for f in project_dir.rglob("*.html")
        if not any(skip in f.parts for skip in (".venv", "node_modules", ".git", "dist"))
        and f.stat().st_size < 512 * 1024
    ]
    if not html_files:
        return violations

    audit_targets = [f for f in html_files if f.name == "index.html"] or html_files[:1]
    for html_file in audit_targets:
        try:
            cmd = [
                *resolve_tool_cmd("pa11y", "pa11y"),
                "--standard", "WCAG2AA",
                "--reporter", "cli",
                html_file.resolve().as_uri(),
            ]
            proc = run_tool(cmd, timeout=60)
            combined = proc.stdout + proc.stderr
            found_before = len(violations)
            current_issue: str | None = None
            for raw_line in combined.splitlines():
                line = raw_line.strip()
                if line.startswith(("Error:", "Warning:", "Notice:")):
                    current_issue = line
                elif current_issue and line.startswith("("):
                    violations.append(
                        FileValidationViolation(
                            file_path=str(html_file),
                            line_number=0,
                            violation_type="wcag_aa_violation",
                            message=f"{current_issue} {line}",
                        )
                    )
                    current_issue = None
            violations.extend(unexplained_exit(
                "pa11y", html_file, proc, len(violations) - found_before,
            ))
        except (OSError, subprocess.SubprocessError) as exc:
            violations.append(tool_failure("pa11y", html_file, str(exc)))

    return violations
