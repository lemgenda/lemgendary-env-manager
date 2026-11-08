"""PowerShell gate: PSScriptAnalyzer."""

import os
import platform
import subprocess
import tempfile
from pathlib import Path

from env_manager._logging import get_logger
from env_manager.validation.models import FileValidationViolation, tool_failure, unexplained_exit
from env_manager.validation.tooling import preferred_shell, run_tool

_log = get_logger(__name__)


PSSA_FIELD_COUNT = 4
"""Fields per PSScriptAnalyzer record: path, line, severity, message."""


def parse_pssa_line(line: str) -> FileValidationViolation | None:
    """Parse one tab-separated ``path, line, severity, message`` record, or return None."""
    parts = line.strip().split("\t", PSSA_FIELD_COUNT - 1)
    if len(parts) != PSSA_FIELD_COUNT:
        return None
    file_path, line_text, severity, message = parts
    try:
        line_number = int(line_text)
    except ValueError as exc:
        _log.debug("Unparsable PSScriptAnalyzer line %r: %s", line, exc)
        return None
    return FileValidationViolation(
        file_path=file_path,
        line_number=line_number,
        violation_type=f"pssa_{severity.strip().lower()}",
        message=message.strip(),
    )


def _build_pssa_command(list_file: str) -> str:
    """Build the PowerShell snippet to execute Invoke-ScriptAnalyzer."""
    pssa = (
        Path(__file__).resolve().parent.parent.parent.parent
        / ".venv"
        / "tools"
        / "powershell"
        / "PSScriptAnalyzer"
        / "1.25.0"
        / "PSScriptAnalyzer.psd1"
    )
    prefix = f"Import-Module '{pssa}'; " if pssa.exists() else ""
    return (
        f"{prefix}$files = Get-Content -Path '{list_file}' -Encoding UTF8; "
        "$files | Where-Object { Test-Path -LiteralPath $_ } | ForEach-Object { "
        "    Invoke-ScriptAnalyzer -Path $_ -Severity Warning,Error "
        "} | ForEach-Object { "
        "    \"$($_.ScriptPath)`t$($_.Line)`t$($_.Severity)`t$($_.Message)\" "
        "}"
    )


def run_psscriptanalyzer(project_dir: Path) -> list[FileValidationViolation]:
    """Run PSScriptAnalyzer on all PowerShell scripts in the project.

    Prefers ``pwsh`` (PowerShell 7) when available. Writes the file list to a
    temp file rather than concatenating every path into a single command line,
    which was prone to hitting the Windows ``CreateProcess`` 32K-character
    limit on projects with many scripts.
    """
    violations: list[FileValidationViolation] = []
    if platform.system().lower() != "windows":
        return violations

    ps1_files = [
        f for f in project_dir.rglob("*.ps1")
        if not any(skip in f.parts for skip in (".venv", "node_modules", ".git"))
    ]
    if not ps1_files:
        return violations

    shell = preferred_shell()

    # Write file list to a temp file with one absolute path per line.
    fd, list_file = tempfile.mkstemp(prefix="pssa_files_", suffix=".txt")
    try:
        with open(fd, "w", encoding="utf-8", closefd=True) as fh:
            for p in ps1_files:
                fh.write(str(p) + "\n")

        # Single-quoted path is safe because we control the temp filename.
        ps_cmd = _build_pssa_command(list_file)

        try:
            proc = run_tool(
                [shell, "-NoProfile", "-NonInteractive", "-Command", ps_cmd], timeout=180,
            )
        except FileNotFoundError:
            # pwsh not on PATH despite shutil.which finding it earlier; fall
            # back to Windows PowerShell.
            proc = run_tool(["powershell", "-NoProfile", "-Command", ps_cmd], timeout=180)

        combined = proc.stdout + proc.stderr
        if (
            "CommandNotFoundException" in combined
            or ("Invoke-ScriptAnalyzer" in combined and "is not recognized" in combined.lower())
        ):
            return [tool_failure(
                "PSScriptAnalyzer", project_dir,
                "module not installed (Install-Module PSScriptAnalyzer -Scope CurrentUser)",
            )]

        for line in combined.splitlines():
            violation = parse_pssa_line(line)
            if violation is not None:
                violations.append(violation)
        violations.extend(
            unexplained_exit("PSScriptAnalyzer", project_dir, proc, len(violations))
        )
    except (OSError, subprocess.SubprocessError) as exc:
        violations.append(tool_failure("PSScriptAnalyzer", project_dir, str(exc)))
    finally:
        try:
            os.unlink(list_file)
        except OSError as exc:
            _log.debug("Failed to unlink temporary PSScriptAnalyzer file %s: %s", list_file, exc)

    return violations
