"""Every version string in the repository must match the README title."""

import json
import re
import tomllib
import unittest
from pathlib import Path

import env_manager
from env_manager.server import app

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"


def _readme_header_version() -> str:
    text = README.read_text(encoding="utf-8")
    header = re.search(r"^# .*\(v(\d+\.\d+\.\d+)", text, re.MULTILINE)
    assert header is not None, "README title has no version"
    return header.group(1)


class TestReadmeVersion(unittest.TestCase):
    """Keep the README header and changelog version in sync."""

    def test_header_matches_latest_changelog(self) -> None:
        """The README title version equals the newest changelog entry."""
        text = README.read_text(encoding="utf-8")
        header = re.search(r"^# .*\(v(\d+\.\d+\.\d+)", text, re.MULTILINE)
        latest = re.search(r"^### v(\d+\.\d+\.\d+)", text, re.MULTILINE)
        self.assertIsNotNone(header, "README title has no version")
        self.assertIsNotNone(latest, "README changelog has no version entry")
        self.assertEqual(header.group(1), latest.group(1))


    def test_every_version_string_matches_the_readme(self) -> None:
        """Every version string in the repo equals the README version."""
        expected = _readme_header_version()
        with open(ROOT / "pyproject.toml", "rb") as handle:
            pyproject_version = tomllib.load(handle)["project"]["version"]
        package_versions = {
            path: json.loads((ROOT / path).read_text(encoding="utf-8"))["version"]
            for path in ("package.json", "requirements/lemgendary-env-manager.package.json")
        }
        openapi_version = json.loads(
            (ROOT / "openapi.json").read_text(encoding="utf-8")
        )["info"]["version"]
        banner = re.search(
            r"Hub \(v(\d+\.\d+\.\d+)\)",
            (ROOT / "lemgendary_env_manager.ps1").read_text(encoding="utf-8"),
        )
        self.assertIsNotNone(banner, "PowerShell banner has no version")
        assert banner is not None
        found = {
            "env_manager.__version__": env_manager.__version__,
            "pyproject.toml": pyproject_version,
            "FastAPI app.version": app.version,
            "openapi.json": openapi_version,
            "lemgendary_env_manager.ps1": banner.group(1),
            **package_versions,
        }
        for source, version in found.items():
            self.assertEqual(version, expected, f"{source} is {version}, README says {expected}")


if __name__ == "__main__":
    unittest.main()
