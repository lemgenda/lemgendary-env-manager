"""Request and response models for ``POST /api/bug-report``."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from pydantic import BaseModel, Field, field_validator

from env_manager.bug_report.config import DEFAULT_SETTINGS

_EMAIL_RE: Final[re.Pattern[str]] = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")
_MAX_EMAIL_CHARS: Final[int] = 254
_MAX_LABEL_CHARS: Final[int] = 100


class BugReportFields(BaseModel):
    """Text fields of the multipart form."""

    email: str = Field(max_length=_MAX_EMAIL_CHARS)
    module: str = Field(min_length=1, max_length=_MAX_LABEL_CHARS)
    description: str = Field(max_length=DEFAULT_SETTINGS.max_description_chars)
    app_version: str = Field(min_length=1, max_length=_MAX_LABEL_CHARS)
    current_tab: str = Field(min_length=1, max_length=_MAX_LABEL_CHARS)

    @field_validator("email", "module", "description", "app_version", "current_tab", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def _valid_email(cls, value: str) -> str:
        if not _EMAIL_RE.fullmatch(value):
            raise ValueError("Enter a valid email address.")
        return value

    @field_validator("description")
    @classmethod
    def _long_enough(cls, value: str) -> str:
        minimum = DEFAULT_SETTINGS.min_description_chars
        if len(value) < minimum:
            raise ValueError(
                f"Description must be at least {minimum} characters ({len(value)} given)."
            )
        return value


@dataclass(frozen=True)
class Attachment:
    """A validated upload stored in the archive as ``name``."""

    name: str
    data: bytes


@dataclass(frozen=True)
class BugReportSubmission:
    """Everything the service needs to build one archive."""

    fields: BugReportFields
    screenshots: tuple[Attachment, ...] = ()
    telemetry: Attachment | None = None
    recording: Attachment | None = None


class BugReportResponse(BaseModel):
    """Result of a saved bug report."""

    report_id: str
    archive_name: str
    archive_path: str = Field(description="Relative to the workspace root, forward slashes.")
    recipient: str
    subject: str
    mail_client_opened: bool
    folder_revealed: bool
