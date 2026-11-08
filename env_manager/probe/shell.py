"""PowerShell invocation shared by the Windows probes."""

import shutil
import subprocess

from env_manager.utils import run_command_simple


def preferred_shell() -> str:
    """Return 'pwsh' if working PowerShell 7 is available, else 'powershell'."""
    pwsh_path = shutil.which("pwsh")
    if pwsh_path and "WindowsApps" not in pwsh_path:
        return "pwsh"
    return "powershell"


def ps_quote(value: str) -> str:
    """Return ``value`` as a single-quoted PowerShell literal (``'`` doubled)."""
    return "'" + value.replace("'", "''") + "'"


def run_powershell(command: str, timeout: int) -> subprocess.CompletedProcess:
    """Run ``command`` non-interactively in the preferred PowerShell, without a profile.

    Raises what ``subprocess.run`` raises (``OSError``, ``SubprocessError``,
    including ``TimeoutExpired``); callers handle and log them.
    """
    cmd = [preferred_shell(), "-NoProfile", "-NonInteractive", "-Command", command]
    return run_command_simple(cmd, timeout=timeout, use_pip_env=True)
