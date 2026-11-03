"""Platform adapter: opens the mail client and reveals a file in the file manager.

The bug report service depends on the ``PlatformAdapter`` protocol only.
``SystemPlatformAdapter`` is the real implementation; tests pass a fake.
"""

from __future__ import annotations

import logging
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Final, Protocol
from urllib.parse import quote

from env_manager.utils import run_command_simple

_log = logging.getLogger("env_manager.bug_report.platform")

_LAUNCH_TIMEOUT_S: Final[int] = 15


class PlatformAdapter(Protocol):
    """Operating system actions used after a bug report archive is saved."""

    def open_mail_client(self, recipient: str, subject: str, body: str) -> bool:
        """Open the default mail client with a new message. Return True on success."""
        ...

    def reveal_in_file_manager(self, path: Path) -> bool:
        """Show ``path`` selected in the system file manager. Return True on success."""
        ...


def build_mailto_url(recipient: str, subject: str, body: str) -> str:
    """Return a ``mailto:`` URL with percent-encoded subject and body."""
    return (
        f"mailto:{quote(recipient, safe='@')}"
        f"?subject={quote(subject, safe='')}&body={quote(body, safe='')}"
    )


class SystemPlatformAdapter:
    """Real adapter for Windows, macOS and Linux."""

    def __init__(self, platform: str = sys.platform) -> None:
        self._platform = platform

    def open_mail_client(self, recipient: str, subject: str, body: str) -> bool:
        """Open the default mail client on a ``mailto:`` link; return True if it opened."""
        url = build_mailto_url(recipient, subject, body)
        try:
            opened = webbrowser.open(url)
        except webbrowser.Error as exc:
            _log.warning("Could not open the mail client: %s", exc)
            return False
        if not opened:
            _log.warning("No handler is registered for mailto: links.")
        return opened

    def reveal_in_file_manager(self, path: Path) -> bool:
        """Show ``path`` in the platform file manager; return True if it launched."""
        command = self._reveal_command(path)
        try:
            result = run_command_simple(command, timeout=_LAUNCH_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError) as exc:
            _log.warning("Could not open the file manager (%s): %s", command[0], exc)
            return False
        if self._platform == "win32":
            # explorer.exe exits with 1 even when the window opened, so its
            # exit code carries no information. A launch without OSError counts.
            _log.debug("explorer exited with %s", result.returncode)
            return True
        if result.returncode != 0:
            _log.warning(
                "%s exited with %s: %s",
                command[0],
                result.returncode,
                result.stderr.strip(),
            )
            return False
        return True

    def _reveal_command(self, path: Path) -> list[str]:
        if self._platform == "win32":
            return ["explorer", f"/select,{path}"]
        if self._platform == "darwin":
            return ["open", "-R", str(path)]
        # xdg-open has no "select file" mode; open the containing folder.
        return ["xdg-open", str(path.parent)]
