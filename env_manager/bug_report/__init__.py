"""Bug reports: validate uploads, save a ZIP in the workspace, hand off to the mail client."""

from env_manager.bug_report.config import DEFAULT_SETTINGS, BugReportSettings
from env_manager.bug_report.models import (
    Attachment,
    BugReportFields,
    BugReportResponse,
    BugReportSubmission,
)
from env_manager.bug_report.platform import PlatformAdapter, SystemPlatformAdapter
from env_manager.bug_report.service import BugReportService, ReportsDirError
from env_manager.bug_report.uploads import UploadRejectedError, collect_submission

__all__ = [
    "DEFAULT_SETTINGS",
    "Attachment",
    "BugReportFields",
    "BugReportResponse",
    "BugReportService",
    "BugReportSettings",
    "BugReportSubmission",
    "PlatformAdapter",
    "ReportsDirError",
    "SystemPlatformAdapter",
    "UploadRejectedError",
    "collect_submission",
]
