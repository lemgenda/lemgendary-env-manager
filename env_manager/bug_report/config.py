"""Bug report settings: the single place in this repository for these values.

The GUI never decides who receives a report or where archives are stored;
it reads ``recipient`` and ``subject`` from the endpoint response.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

MIB: Final[int] = 1024 * 1024


@dataclass(frozen=True)
class BugReportSettings:
    """Recipient, subject, storage folder and upload limits for bug reports.

    ``reports_dir`` is relative to the workspace root (the folder that holds
    all LemGendary projects). It must stay inside the workspace root.
    """

    recipient: str = "lemgenda.obrt@gmail.com"
    subject: str = "LemGendary AI Studio Bug report"
    reports_dir: str = "bug-reports"
    min_description_chars: int = 50
    max_description_chars: int = 10_000
    max_screenshots: int = 5
    max_screenshot_bytes: int = 5 * MIB
    max_telemetry_bytes: int = 2 * MIB
    max_recording_bytes: int = 50 * MIB
    max_total_bytes: int = 80 * MIB


DEFAULT_SETTINGS: Final[BugReportSettings] = BugReportSettings()
