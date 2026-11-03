"""Tests for POST /api/bug-report with a fake platform adapter and a temporary workspace."""

from __future__ import annotations

import csv
import io
import json
import shutil
import tempfile
import unittest
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock
from urllib.parse import unquote

from fastapi.testclient import TestClient

from env_manager import server
from env_manager.bug_report import (
    DEFAULT_SETTINGS,
    BugReportFields,
    BugReportService,
    BugReportSettings,
    BugReportSubmission,
    ReportsDirError,
    SystemPlatformAdapter,
)
from env_manager.bug_report.archive import REPORT_COLUMNS
from env_manager.bug_report.platform import build_mailto_url

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 32
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 32
TELEMETRY = b"timestamp,status,source,message\r\n2026-11-03T10:00:00Z,ok,gui,started\r\n"
DESCRIPTION = 'Clicking "Run", the app froze,\nthen showed an error; steps: open, click, wait.'
FIXED_TIME = datetime(2026, 11, 3, 12, 30, 45, tzinfo=UTC)


class FakePlatform:
    """Records calls instead of opening a mail client or file manager."""

    def __init__(self, mail_ok: bool = True, reveal_ok: bool = True,
                 raise_error: bool = False) -> None:
        self.mail_ok = mail_ok
        self.reveal_ok = reveal_ok
        self.raise_error = raise_error
        self.mail_calls: list[tuple[str, str, str]] = []
        self.reveal_calls: list[Path] = []

    def open_mail_client(self, recipient: str, subject: str, body: str) -> bool:
        """Record the call and return the configured result."""
        self.mail_calls.append((recipient, subject, body))
        if self.raise_error:
            raise OSError("no mail client")
        return self.mail_ok

    def reveal_in_file_manager(self, path: Path) -> bool:
        """Record the call and return the configured result."""
        self.reveal_calls.append(path)
        if self.raise_error:
            raise OSError("no file manager")
        return self.reveal_ok


def _form(**overrides: str) -> dict[str, str]:
    data = {
        "email": "tester@example.com",
        "module": "Training Suite",
        "description": DESCRIPTION,
        "app_version": "v2.10.18",
        "current_tab": "training",
    }
    data.update(overrides)
    return data


class BugReportApiTests(unittest.TestCase):
    """HTTP-level checks on a temporary workspace (not a git repository)."""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp_dir)
        self.root = (Path(self._tmp_dir) / "workspace").resolve()
        self.root.mkdir()
        self.platform = FakePlatform()
        self._use_service(self.platform)
        self.client = TestClient(server.app)

    def _use_service(self, platform: FakePlatform) -> None:
        service = BugReportService(
            self.root, DEFAULT_SETTINGS, platform, clock=lambda: FIXED_TIME
        )
        patcher = mock.patch.object(server, "_bug_report_service", return_value=service)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _post(self, data=None, files=None):
        return self.client.post("/api/bug-report", data=data or _form(), files=files)

    def _zip(self, body: dict) -> zipfile.ZipFile:
        return zipfile.ZipFile(self.root / body["archive_path"])

    # ── happy path ──────────────────────────────────────────────────────

    def test_full_report_is_archived_and_handed_off(self) -> None:
        """A complete report is zipped in the workspace and handed to the mail client."""
        files = [
            ("screenshot", ("a.png", PNG, "image/png")),
            ("screenshot", ("b.webp", WEBP, "image/webp")),
            ("telemetry", ("telemetry.csv", TELEMETRY, "text/csv")),
            ("recording", ("rec.webm", WEBM, "video/webm")),
        ]
        response = self._post(files=files)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()

        self.assertEqual(body["recipient"], "lemgenda.obrt@gmail.com")
        self.assertEqual(body["subject"], "LemGendary AI Studio Bug report")
        self.assertTrue(body["mail_client_opened"])
        self.assertTrue(body["folder_revealed"])
        self.assertRegex(body["archive_name"], r"^bug_report_20261103T123045Z_[0-9a-f]{8}\.zip$")
        self.assertEqual(body["archive_path"], f"bug-reports/{body['archive_name']}")
        self.assertTrue(body["report_id"].startswith(body["archive_name"][-12:-4]))

        with self._zip(body) as archive:
            self.assertEqual(
                sorted(archive.namelist()),
                ["recording.webm", "report.csv", "screenshot_1.png",
                 "screenshot_2.webp", "telemetry.csv"],
            )
            self.assertEqual(archive.read("screenshot_1.png"), PNG)
            self.assertEqual(archive.read("screenshot_2.webp"), WEBP)
            self.assertEqual(archive.read("telemetry.csv"), TELEMETRY)
            self.assertEqual(archive.read("recording.webm"), WEBM)
            rows = list(csv.reader(io.StringIO(archive.read("report.csv").decode("utf-8"),
                                               newline="")))
        self.assertEqual(len(rows), 2)
        self.assertEqual(tuple(rows[0]), REPORT_COLUMNS)
        record = dict(zip(rows[0], rows[1], strict=True))
        self.assertEqual(record["description"], DESCRIPTION)
        self.assertEqual(record["timestamp"], "2026-11-03T12:30:45+00:00")
        self.assertEqual(record["report_id"], body["report_id"])
        self.assertEqual(record["screenshot_count"], "2")
        self.assertEqual(record["has_recording"], "true")
        self.assertEqual(record["has_telemetry"], "true")

        recipient, subject, mail_body = self.platform.mail_calls[0]
        self.assertEqual((recipient, subject), (body["recipient"], body["subject"]))
        self.assertIn(body["archive_name"], mail_body)
        self.assertIn(body["archive_path"], mail_body)
        self.assertEqual(self.platform.reveal_calls, [self.root / body["archive_path"]])

    def test_minimal_report_and_mp4_recording(self) -> None:
        """A report with only the required fields and an MP4 recording is accepted."""
        response = self._post(files=[("recording", ("r.mp4", MP4, "video/mp4"))])
        self.assertEqual(response.status_code, 200, response.text)
        with self._zip(response.json()) as archive:
            self.assertEqual(sorted(archive.namelist()), ["recording.mp4", "report.csv"])

    def test_formula_like_text_is_neutralised(self) -> None:
        """Formula-like text is prefixed with a quote in the CSV."""
        response = self._post(data=_form(module="=HYPERLINK(1)"))
        self.assertEqual(response.status_code, 200, response.text)
        with self._zip(response.json()) as archive:
            rows = list(csv.reader(io.StringIO(archive.read("report.csv").decode("utf-8"))))
        self.assertEqual(rows[1][REPORT_COLUMNS.index("module")], "'=HYPERLINK(1)")

    def test_no_absolute_path_leaves_the_server(self) -> None:
        """Responses carry only workspace-relative paths."""
        response = self._post()
        self.assertEqual(response.status_code, 200, response.text)
        absolute = str(self.root)
        self.assertNotIn(absolute, response.text)
        self.assertNotIn(absolute.replace("\\", "/"), response.text)
        self.assertNotIn(absolute, self.platform.mail_calls[0][2])
        self.assertFalse(Path(response.json()["archive_path"]).is_absolute())

    # ── adapter failures ────────────────────────────────────────────────

    def test_adapter_false_results_are_reported(self) -> None:
        """Adapter calls that return False are reported in the response."""
        self._use_service(FakePlatform(mail_ok=False, reveal_ok=False))
        body = self._post().json()
        self.assertFalse(body["mail_client_opened"])
        self.assertFalse(body["folder_revealed"])
        self.assertTrue((self.root / body["archive_path"]).is_file())

    def test_adapter_errors_do_not_fail_the_request(self) -> None:
        """Adapter exceptions are reported without failing the request."""
        self._use_service(FakePlatform(raise_error=True))
        response = self._post()
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertFalse(body["mail_client_opened"])
        self.assertFalse(body["folder_revealed"])
        self.assertTrue((self.root / body["archive_path"]).is_file())

    # ── field validation (422) ──────────────────────────────────────────

    def _assert_field_error(self, response, field: str, status: int = 422) -> None:
        self.assertEqual(response.status_code, status, response.text)
        locs = [item["loc"] for item in response.json()["detail"]]
        self.assertIn(["body", field], locs)
        self.assertFalse((self.root / "bug-reports").exists())

    def test_invalid_email(self) -> None:
        """An invalid e-mail address is rejected."""
        for email in ("", "not-an-email", "a@b", "a b@c.com"):
            with self.subTest(email=email):
                self._assert_field_error(self._post(data=_form(email=email)), "email")

    def test_short_description_after_trim(self) -> None:
        """A description that is too short after trimming is rejected."""
        padded = "   " + "x" * 49 + "   \n"
        self._assert_field_error(self._post(data=_form(description=padded)), "description")

    def test_missing_fields(self) -> None:
        """Missing required fields are rejected."""
        for field in ("module", "app_version", "current_tab", "description", "email"):
            with self.subTest(field=field):
                data = _form()
                del data[field]
                self._assert_field_error(self._post(data=data), field)

    def test_blank_module(self) -> None:
        """A blank module name is rejected."""
        self._assert_field_error(self._post(data=_form(module="   ")), "module")

    # ── upload validation ───────────────────────────────────────────────

    def test_too_many_screenshots(self) -> None:
        """More screenshots than allowed are rejected."""
        files = [("screenshot", (f"{i}.png", PNG, "image/png")) for i in range(6)]
        self._assert_field_error(self._post(files=files), "screenshot")

    def test_screenshot_wrong_type(self) -> None:
        """A screenshot with an unsupported type is rejected."""
        files = [("screenshot", ("a.jpg", b"\xff\xd8\xff" + b"\x00" * 20, "image/jpeg"))]
        self._assert_field_error(self._post(files=files), "screenshot")

    def test_magic_byte_mismatch(self) -> None:
        """An upload whose bytes do not match its declared type is rejected."""
        cases = {
            "screenshot": ("a.png", WEBP, "image/png"),
            "recording": ("r.webm", MP4, "video/webm"),
        }
        for field, upload in cases.items():
            with self.subTest(field=field):
                self._assert_field_error(self._post(files=[(field, upload)]), field)

    def test_telemetry_must_be_utf8_csv(self) -> None:
        """Telemetry must be a UTF-8 CSV file."""
        for upload in (("t.txt", TELEMETRY, "text/plain"), ("t.csv", b"\xff\xfe\x00", "text/csv")):
            with self.subTest(upload=upload[0]):
                self._assert_field_error(self._post(files=[("telemetry", upload)]), "telemetry")

    def test_empty_upload(self) -> None:
        """An empty upload is rejected."""
        self._assert_field_error(
            self._post(files=[("screenshot", ("a.png", b"", "image/png"))]), "screenshot"
        )

    # ── size limits (413) ───────────────────────────────────────────────

    def test_per_file_size_limits(self) -> None:
        """Each upload kind has its own size limit."""
        small = BugReportSettings(
            max_screenshot_bytes=64, max_telemetry_bytes=64, max_recording_bytes=64
        )
        big = b"\x00" * 65
        cases = {
            "screenshot": ("a.png", PNG + big, "image/png"),
            "telemetry": ("t.csv", TELEMETRY + big, "text/csv"),
            "recording": ("r.webm", WEBM + big, "video/webm"),
        }
        with mock.patch.object(server, "BUG_REPORT_SETTINGS", small):
            for field, upload in cases.items():
                with self.subTest(field=field):
                    self._assert_field_error(self._post(files=[(field, upload)]), field, 413)

    def test_total_size_limit(self) -> None:
        """A report whose uploads exceed the total limit is rejected."""
        small = BugReportSettings(max_total_bytes=100)
        files = [("screenshot", (f"{i}.png", PNG, "image/png")) for i in range(3)]
        with mock.patch.object(server, "BUG_REPORT_SETTINGS", small):
            self._assert_field_error(self._post(files=files), "screenshot", 413)


class BugReportServiceTests(unittest.TestCase):
    """Reports-folder resolution and git safety."""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp_dir)
        self.root = (Path(self._tmp_dir) / "workspace").resolve()
        self.root.mkdir()
        self.submission = BugReportSubmission(fields=BugReportFields(**_form()))

    def _service(self, reports_dir: str) -> BugReportService:
        settings = BugReportSettings(reports_dir=reports_dir)
        return BugReportService(self.root, settings, FakePlatform())

    def test_reports_dir_must_stay_inside_workspace(self) -> None:
        """A reports folder outside the workspace is refused."""
        outside = str(Path(self._tmp_dir).resolve() / "elsewhere")
        for reports_dir in ("..", "../elsewhere", ".", outside):
            with self.subTest(reports_dir=reports_dir), self.assertRaises(ReportsDirError):
                self._service(reports_dir).submit(self.submission)

    def test_folder_outside_git_has_no_ignore_file(self) -> None:
        """Outside a git repo no ``.gitignore`` is written."""
        self._service("bug-reports").submit(self.submission)
        self.assertFalse((self.root / "bug-reports" / ".gitignore").exists())

    def test_folder_inside_git_repo_is_self_ignored(self) -> None:
        """Inside a git repo the reports folder ignores itself."""
        (self.root / "repo" / ".git").mkdir(parents=True)
        result = self._service("repo/bug-reports").submit(self.submission)
        ignore = self.root / "repo" / "bug-reports" / ".gitignore"
        self.assertIn("*", ignore.read_text(encoding="utf-8").splitlines())
        self.assertTrue(result.archive_path.startswith("repo/bug-reports/"))


class PlatformAdapterTests(unittest.TestCase):
    """The real adapter, with the OS calls replaced."""

    def test_mailto_url_encodes_subject_and_body(self) -> None:
        """Subject and body are percent-encoded in the ``mailto:`` URL."""
        url = build_mailto_url("a@b.com", "Bug report", "Line 1\nLine & 2")
        self.assertTrue(url.startswith("mailto:a@b.com?subject=Bug%20report&body="))
        self.assertEqual(unquote(url.split("body=", 1)[1]), "Line 1\nLine & 2")

    def test_open_mail_client_uses_webbrowser(self) -> None:
        """The mail client is opened through ``webbrowser``."""
        with mock.patch("env_manager.bug_report.platform.webbrowser.open",
                        return_value=False) as opener:
            self.assertFalse(SystemPlatformAdapter().open_mail_client("a@b.com", "S", "B"))
        self.assertTrue(opener.call_args.args[0].startswith("mailto:a@b.com"))

    def test_reveal_commands_per_platform(self) -> None:
        """Each platform uses its own reveal command."""
        target = Path("bug-reports") / "x.zip"
        expected = {
            "win32": ["explorer", f"/select,{target}"],
            "darwin": ["open", "-R", str(target)],
            "linux": ["xdg-open", str(target.parent)],
        }
        for platform, command in expected.items():
            with self.subTest(platform=platform), mock.patch(
                "env_manager.bug_report.platform.subprocess.run",
                return_value=mock.Mock(returncode=1, stderr=b"err"),
            ) as run:
                revealed = SystemPlatformAdapter(platform).reveal_in_file_manager(target)
                self.assertEqual(run.call_args.args[0], command)
                # explorer.exe always exits 1; other platforms report the failure.
                self.assertEqual(revealed, platform == "win32")

    def test_reveal_launch_error_returns_false(self) -> None:
        """A reveal command that cannot launch returns False."""
        with mock.patch("env_manager.bug_report.platform.subprocess.run",
                        side_effect=FileNotFoundError("xdg-open")):
            self.assertFalse(SystemPlatformAdapter("linux").reveal_in_file_manager(Path("x")))


class OpenApiTests(unittest.TestCase):
    """The committed ``openapi.json`` matches the live schema."""
    def test_openapi_json_is_current(self) -> None:
        """``openapi.json`` equals ``app.openapi()``."""
        committed = json.loads(
            (Path(server.__file__).resolve().parent.parent / "openapi.json").read_text(
                encoding="utf-8"
            )
        )
        current = server.app.openapi()["paths"]["/api/bug-report"]
        self.assertEqual(committed["paths"].get("/api/bug-report"), current)


if __name__ == "__main__":
    unittest.main()
