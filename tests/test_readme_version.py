"""The README title version must match the latest changelog entry."""

import re
import unittest
from pathlib import Path

README = Path(__file__).resolve().parent.parent / "README.md"


class TestReadmeVersion(unittest.TestCase):
    """Keep the README header and changelog version in sync."""

    def test_header_matches_latest_changelog(self) -> None:
        text = README.read_text(encoding="utf-8")
        header = re.search(r"^# .*\(v(\d+\.\d+\.\d+)", text, re.MULTILINE)
        latest = re.search(r"^### v(\d+\.\d+\.\d+)", text, re.MULTILINE)
        self.assertIsNotNone(header, "README title has no version")
        self.assertIsNotNone(latest, "README changelog has no version entry")
        self.assertEqual(header.group(1), latest.group(1))


if __name__ == "__main__":
    unittest.main()
