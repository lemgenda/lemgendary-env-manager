"""Builds the bug report ZIP: ``report.csv`` plus the uploaded attachments."""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime
from typing import Final

from env_manager.bug_report.models import BugReportSubmission

REPORT_COLUMNS: Final[tuple[str, ...]] = (
    "timestamp",
    "report_id",
    "email",
    "module",
    "current_tab",
    "app_version",
    "description",
    "screenshot_count",
    "has_recording",
    "has_telemetry",
)

# A cell starting with one of these runs as a formula in spreadsheet apps.
_FORMULA_PREFIXES: Final[tuple[str, ...]] = ("=", "+", "-", "@", "\t", "\r")


def _safe_cell(value: str) -> str:
    """Prefix formula-like text with an apostrophe so spreadsheets show it as text."""
    return f"'{value}" if value.startswith(_FORMULA_PREFIXES) else value


def build_report_csv(submission: BugReportSubmission, report_id: str, created: datetime) -> bytes:
    """Return ``report.csv`` (header row + one row) as UTF-8 bytes."""
    fields = submission.fields
    row = (
        created.isoformat(),
        report_id,
        _safe_cell(fields.email),
        _safe_cell(fields.module),
        _safe_cell(fields.current_tab),
        _safe_cell(fields.app_version),
        _safe_cell(fields.description),
        str(len(submission.screenshots)),
        str(submission.recording is not None).lower(),
        str(submission.telemetry is not None).lower(),
    )
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(REPORT_COLUMNS)
    writer.writerow(row)
    return buffer.getvalue().encode("utf-8")


def build_archive(submission: BugReportSubmission, report_id: str, created: datetime) -> bytes:
    """Return the complete ZIP archive in memory."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("report.csv", build_report_csv(submission, report_id, created))
        if submission.telemetry is not None:
            archive.writestr(submission.telemetry.name, submission.telemetry.data)
        for shot in submission.screenshots:
            archive.writestr(shot.name, shot.data)
        if submission.recording is not None:
            archive.writestr(submission.recording.name, submission.recording.data)
    return buffer.getvalue()
