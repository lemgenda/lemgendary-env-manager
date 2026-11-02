"""Bug report service: saves the archive, then opens the mail client and file manager.

Mail clients cannot receive attachments through ``mailto:``, so the message
body tells the user which archive to attach and where it is.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from env_manager.bug_report.archive import build_archive
from env_manager.bug_report.config import BugReportSettings
from env_manager.bug_report.models import BugReportResponse, BugReportSubmission
from env_manager.bug_report.platform import PlatformAdapter

_log = logging.getLogger("env_manager.bug_report.service")

_SELF_IGNORE: Final[str] = "# Bug report archives are local only.\n*\n"


class ReportsDirError(ValueError):
    """The configured reports folder is not a safe place inside the workspace."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _inside_git_repo(path: Path) -> bool:
    return any((candidate / ".git").exists() for candidate in (path, *path.parents))


class BugReportService:
    """Creates bug report archives under ``<workspace>/<settings.reports_dir>``."""

    def __init__(
        self,
        workspace_root: Path,
        settings: BugReportSettings,
        platform: PlatformAdapter,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._root = workspace_root.resolve()
        self._settings = settings
        self._platform = platform
        self._clock = clock

    def reports_dir(self) -> Path:
        """Resolve the reports folder; it must be relative and stay in the workspace."""
        configured = Path(self._settings.reports_dir)
        if configured.is_absolute():
            raise ReportsDirError("reports_dir must be relative to the workspace root.")
        resolved = (self._root / configured).resolve()
        if resolved == self._root or not resolved.is_relative_to(self._root):
            raise ReportsDirError("reports_dir must be a folder inside the workspace root.")
        return resolved

    def _prepare_reports_dir(self) -> Path:
        folder = self.reports_dir()
        folder.mkdir(parents=True, exist_ok=True)
        if _inside_git_repo(folder):
            ignore_file = folder / ".gitignore"
            if not ignore_file.exists():
                ignore_file.write_text(_SELF_IGNORE, encoding="utf-8")
                _log.info("Reports folder is inside a git repository; added %s", ignore_file.name)
        return folder

    def mail_body(self, archive_name: str, archive_path: str, report_id: str) -> str:
        """Plain-text message body naming the archive to attach (relative path only)."""
        return (
            "Please attach the bug report archive to this email before sending.\n"
            "\n"
            f"Archive: {archive_name}\n"
            f"Location: {archive_path} (inside the LemGendary workspace folder)\n"
            f"Report ID: {report_id}\n"
        )

    def submit(self, submission: BugReportSubmission) -> BugReportResponse:
        """Save the archive, then try the platform steps; their failures only clear flags."""
        created = self._clock()
        report_id = uuid.uuid4().hex
        archive_name = f"bug_report_{created.strftime('%Y%m%dT%H%M%SZ')}_{report_id[:8]}.zip"

        folder = self._prepare_reports_dir()
        archive_file = folder / archive_name
        archive_file.write_bytes(build_archive(submission, report_id, created))
        archive_path = archive_file.relative_to(self._root).as_posix()
        _log.info("Saved bug report %s to %s", report_id, archive_path)

        settings = self._settings
        body = self.mail_body(archive_name, archive_path, report_id)
        try:
            mail_opened = self._platform.open_mail_client(
                settings.recipient, settings.subject, body
            )
        except OSError as exc:
            _log.warning("Opening the mail client failed: %s", exc)
            mail_opened = False
        try:
            revealed = self._platform.reveal_in_file_manager(archive_file)
        except OSError as exc:
            _log.warning("Revealing the archive failed: %s", exc)
            revealed = False

        return BugReportResponse(
            report_id=report_id,
            archive_name=archive_name,
            archive_path=archive_path,
            recipient=settings.recipient,
            subject=settings.subject,
            mail_client_opened=mail_opened,
            folder_revealed=revealed,
        )
