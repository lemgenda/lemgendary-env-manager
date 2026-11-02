"""Upload checks for bug reports: count, size, declared type and magic bytes."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final

from starlette.datastructures import UploadFile

from env_manager.bug_report.config import BugReportSettings
from env_manager.bug_report.models import Attachment, BugReportFields, BugReportSubmission

HTTP_PAYLOAD_TOO_LARGE: Final[int] = 413
HTTP_UNPROCESSABLE: Final[int] = 422


class UploadRejectedError(Exception):
    """An upload broke a limit. ``status_code`` is 413 for size, 422 otherwise."""

    def __init__(self, field: str, message: str, status_code: int = HTTP_UNPROCESSABLE) -> None:
        super().__init__(message)
        self.field = field
        self.message = message
        self.status_code = status_code

    def detail(self) -> list[dict[str, object]]:
        """FastAPI-style error detail with the offending field."""
        kind = "too_large" if self.status_code == HTTP_PAYLOAD_TOO_LARGE else "invalid_upload"
        return [{"loc": ["body", self.field], "msg": self.message, "type": kind}]


def _is_png(data: bytes) -> bool:
    return data.startswith(b"\x89PNG\r\n\x1a\n")


def _is_webp(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def _is_webm(data: bytes) -> bool:
    return data.startswith(b"\x1a\x45\xdf\xa3")


def _is_mp4(data: bytes) -> bool:
    return len(data) >= 8 and data[4:8] == b"ftyp"


@dataclass(frozen=True)
class MediaType:
    """An accepted content type: archive file extension and signature check."""

    extension: str
    matches: Callable[[bytes], bool]


SCREENSHOT_TYPES: Final[dict[str, MediaType]] = {
    "image/png": MediaType("png", _is_png),
    "image/webp": MediaType("webp", _is_webp),
}
RECORDING_TYPES: Final[dict[str, MediaType]] = {
    "video/webm": MediaType("webm", _is_webm),
    "video/mp4": MediaType("mp4", _is_mp4),
}
TELEMETRY_TYPE: Final[str] = "text/csv"


def _base_type(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


async def _read_capped(upload: UploadFile, field: str, limit: int) -> bytes:
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise UploadRejectedError(
            field,
            f"File exceeds the {limit // (1024 * 1024)} MB limit.",
            HTTP_PAYLOAD_TOO_LARGE,
        )
    if not data:
        raise UploadRejectedError(field, "File is empty.")
    return data


def _media(field: str, content_type: str | None, data: bytes,
           accepted: dict[str, MediaType]) -> MediaType:
    declared = _base_type(content_type)
    media = accepted.get(declared)
    if media is None:
        allowed = ", ".join(accepted)
        raise UploadRejectedError(field, f"Unsupported type '{declared}'. Allowed: {allowed}.")
    if not media.matches(data):
        raise UploadRejectedError(
            field, f"File content does not match the declared type '{declared}'."
        )
    return media


def _check_total(field: str, used: int, limit: int) -> None:
    if used > limit:
        raise UploadRejectedError(
            field,
            f"Report exceeds the {limit // (1024 * 1024)} MB total limit.",
            HTTP_PAYLOAD_TOO_LARGE,
        )


async def collect_submission(
    fields: BugReportFields,
    screenshots: Sequence[UploadFile],
    telemetry: UploadFile | None,
    recording: UploadFile | None,
    settings: BugReportSettings,
) -> BugReportSubmission:
    """Read and check every upload; raise ``UploadRejectedError`` on the first problem."""
    if len(screenshots) > settings.max_screenshots:
        raise UploadRejectedError(
            "screenshot", f"At most {settings.max_screenshots} screenshots are allowed."
        )
    used = 0

    shots: list[Attachment] = []
    for index, upload in enumerate(screenshots, start=1):
        data = await _read_capped(upload, "screenshot", settings.max_screenshot_bytes)
        used += len(data)
        _check_total("screenshot", used, settings.max_total_bytes)
        media = _media("screenshot", upload.content_type, data, SCREENSHOT_TYPES)
        shots.append(Attachment(f"screenshot_{index}.{media.extension}", data))

    telemetry_file: Attachment | None = None
    if telemetry is not None:
        data = await _read_capped(telemetry, "telemetry", settings.max_telemetry_bytes)
        used += len(data)
        _check_total("telemetry", used, settings.max_total_bytes)
        declared = _base_type(telemetry.content_type)
        if declared != TELEMETRY_TYPE:
            raise UploadRejectedError(
                "telemetry", f"Unsupported type '{declared}'. Allowed: {TELEMETRY_TYPE}."
            )
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise UploadRejectedError("telemetry", "Telemetry must be UTF-8 text.") from exc
        telemetry_file = Attachment("telemetry.csv", data)

    recording_file: Attachment | None = None
    if recording is not None:
        data = await _read_capped(recording, "recording", settings.max_recording_bytes)
        used += len(data)
        _check_total("recording", used, settings.max_total_bytes)
        media = _media("recording", recording.content_type, data, RECORDING_TYPES)
        recording_file = Attachment(f"recording.{media.extension}", data)

    return BugReportSubmission(
        fields=fields,
        screenshots=tuple(shots),
        telemetry=telemetry_file,
        recording=recording_file,
    )
