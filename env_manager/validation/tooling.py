"""Locating the external lint tools that the gates run (shell, Python, Node binaries)."""

import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from env_manager.utils import run_command_simple
from env_manager.validation.models import FileValidationViolation
from env_manager.workspace import ENV_MANAGER_DIR


def preferred_shell() -> str:
    """Return 'pwsh' when PowerShell 7 is available, else 'powershell'."""
    if shutil.which("pwsh"):
        return "pwsh"
    return "powershell"


def get_env_manager_python() -> str | None:
    """Return the path to the env-manager .venv Python executable."""
    env_mgr_dir = ENV_MANAGER_DIR
    if platform.system().lower() == "windows":
        py = env_mgr_dir / ".venv" / "Scripts" / "python.exe"
    else:
        py = env_mgr_dir / ".venv" / "bin" / "python"
    if py.exists():
        return str(py)
    return sys.executable  # fallback to current interpreter


def resolve_tool_cmd(
    tool_name: str, npx_pkg: str, project_dir: Path | None = None,
) -> list[str]:
    """Resolve local binary from project or env-manager node_modules/.bin, else npx.

    The npx fallback runs with ``--no``: it uses an already installed package
    and never downloads one at validation time. A missing tool then makes npx
    exit non-zero, which the gate reports as a tool failure.
    """
    mgr_dir = ENV_MANAGER_DIR
    candidate_dirs: list[Path] = []
    if project_dir and (project_dir / "node_modules" / ".bin").exists():
        candidate_dirs.append(project_dir / "node_modules" / ".bin")
    candidate_dirs.append(mgr_dir / "node_modules" / ".bin")

    for bin_dir in candidate_dirs:
        candidates = [
            bin_dir / f"{tool_name}.cmd",
            bin_dir / f"{tool_name}.exe",
            bin_dir / tool_name,
        ]
        for c in candidates:
            if c.exists():
                return [str(c)]

    global_bin = (
        shutil.which(f"{tool_name}.cmd")
        or shutil.which(f"{tool_name}.exe")
        or shutil.which(tool_name)
    )
    if global_bin:
        return [global_bin]

    npx = shutil.which("npx") or ("npx.cmd" if sys.platform == "win32" else "npx")
    return [npx, "--no", npx_pkg]


def regex_violations(
    output: str, pattern: str, kind_prefix: str,
) -> list[FileValidationViolation]:
    """Turn each tool output line matching ``pattern`` into a violation.

    ``pattern`` names its groups ``path``, ``line``, ``msg`` and, optionally,
    ``rule``. The violation type is ``kind_prefix`` followed by the rule.
    """
    matches = (re.match(pattern, line.strip()) for line in output.splitlines())
    return [
        FileValidationViolation(
            file_path=m["path"],
            line_number=int(m["line"]),
            violation_type=f"{kind_prefix}{m.groupdict().get('rule') or ''}",
            message=m["msg"].strip(),
        )
        for m in matches
        if m
    ]


def run_tool(
    cmd: list[str], timeout: int, cwd: Path | None = None, use_pip_env: bool = False,
) -> subprocess.CompletedProcess[str]:
    """Run a lint tool with captured UTF-8 output, no console window and no check.

    Raises ``OSError`` or ``subprocess.SubprocessError`` (including a
    timeout); every gate turns those into a ``tool_failure``.
    """
    return run_command_simple(cmd, timeout=timeout, cwd=cwd, use_pip_env=use_pip_env)
