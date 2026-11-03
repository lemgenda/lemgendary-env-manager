"""Manifest downgrade guard tests (review finding D-06).

Every test builds a throw-away workspace in a temporary directory; the real
sibling repositories are never read or written.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from env_manager.requirements_manager import (
    DRIFT_PREFIX,
    find_package_json_drift,
    find_requirement_drift,
    merge_npm_manifest,
    sync_all_manifests,
    sync_manifest_to_project,
)


class _TempWorkspace(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp_dir)
        self.base = Path(self._tmp_dir)
        self.central_dir = self.base / "lemgendary-env-manager" / "requirements"
        self.central_dir.mkdir(parents=True)
        self.sibling = self.base / "lemgendary-datasets"
        self.sibling.mkdir()

    def write(self, path: Path, text: str) -> Path:
        """Write ``text`` to ``path`` and return the path."""
        path.write_text(text, encoding="utf-8")
        return path


class TestRequirementDowngradeGuard(_TempWorkspace):
    """Requirements sync refuses writes that would downgrade a sibling."""
    def test_central_lower_pin_is_not_written_and_reported(self) -> None:
        """A lower central pin is not written and is reported as drift."""
        central = self.write(self.central_dir / "requirements-datasets.txt",
                             "torch==2.14.1+cu126\nfastapi==0.162.0\n")
        sibling_text = "torch==2.15.0\nfastapi==0.162.0\n"
        target = self.write(self.sibling / "requirements.txt", sibling_text)

        ok, msg = sync_manifest_to_project(central, self.sibling)

        self.assertFalse(ok)
        self.assertTrue(msg.startswith(DRIFT_PREFIX), msg)
        self.assertIn("torch", msg)
        self.assertNotIn("fastapi", msg)
        self.assertEqual(target.read_text(encoding="utf-8"), sibling_text)
        self.assertEqual(central.read_text(encoding="utf-8"),
                         "torch==2.14.1+cu126\nfastapi==0.162.0\n")

    def test_central_higher_pin_is_written(self) -> None:
        """A higher central pin is written."""
        central = self.write(self.central_dir / "requirements-datasets.txt", "torch==2.16.0\n")
        target = self.write(self.sibling / "requirements.txt", "torch==2.15.0\n")

        ok, msg = sync_manifest_to_project(central, self.sibling)

        self.assertTrue(ok, msg)
        self.assertEqual(target.read_text(encoding="utf-8"), "torch==2.16.0\n")

    def test_equal_pins_are_written(self) -> None:
        """Equal pins are written."""
        central = self.write(self.central_dir / "requirements-datasets.txt", "numpy==2.5.4\n")
        target = self.write(self.sibling / "requirements.txt", "numpy==2.5.4\n")

        ok, msg = sync_manifest_to_project(central, self.sibling)

        self.assertTrue(ok, msg)
        self.assertEqual(target.read_text(encoding="utf-8"), "numpy==2.5.4\n")

    def test_missing_sibling_file_is_created(self) -> None:
        """A missing sibling requirements file is created."""
        central = self.write(self.central_dir / "requirements-datasets.txt", "numpy==2.5.4\n")

        ok, msg = sync_manifest_to_project(central, self.sibling)

        self.assertTrue(ok, msg)
        self.assertTrue((self.sibling / "requirements.txt").exists())

    def test_loosened_range_and_dropped_pin_and_removed_package_are_drift(self) -> None:
        """A loosened range, a dropped pin and a removed package all count as drift."""
        central = self.write(self.central_dir / "c.txt",
                             "transformers>=5.0.0,<6.0.0\npillow\nscipy>=1.0\n")
        sibling = self.write(self.sibling / "requirements.txt",
                             "transformers==5.22.1\npillow==13.0.0\nscipy==1.18.1\npytest==9.2.0\n")

        drift = find_requirement_drift(central, sibling)

        joined = " | ".join(drift)
        self.assertEqual(len(drift), 4, joined)
        for name in ("transformers", "pillow", "scipy", "pytest"):
            self.assertIn(name, joined)

    def test_markers_are_compared_per_marker(self) -> None:
        """Pins are compared per environment marker."""
        central = self.write(self.central_dir / "c.txt",
                             "MetaTrader5==5.0.50; sys_platform == 'win32'\n")
        sibling = self.write(self.sibling / "requirements.txt",
                             "MetaTrader5==5.0.45; sys_platform == 'win32'\n")

        self.assertEqual(find_requirement_drift(central, sibling), [])

    def test_upgrade_with_extra_central_package_is_not_drift(self) -> None:
        """An extra central package is not drift."""
        central = self.write(self.central_dir / "c.txt", "numpy==2.6.0\nhttpx2==2.16.0\n")
        sibling = self.write(self.sibling / "requirements.txt", "numpy==2.5.4\n")

        self.assertEqual(find_requirement_drift(central, sibling), [])


class TestPackageJsonDowngradeGuard(_TempWorkspace):
    """package.json sync refuses writes that would downgrade a sibling."""
    def setUp(self) -> None:
        super().setUp()
        self.gui = self.base / "lemgendary-ai-studio-gui"
        self.gui.mkdir()

    def test_older_central_package_json_is_not_copied(self) -> None:
        """An older central package.json is not copied."""
        central = {"version": "2.10.12", "devDependencies": {"vite": "^6.4.4"}}
        sibling = {"version": "2.10.13", "devDependencies": {"vite": "^6.4.4", "jsdom": "25.0.1"}}
        self.write(self.central_dir / "lemgendary-ai-studio-gui.package.json", json.dumps(central))
        target = self.write(self.gui / "package.json", json.dumps(sibling))

        results = sync_all_manifests(self.base)

        ok, msg = results["lemgendary-ai-studio-gui (npm)"]
        self.assertFalse(ok)
        self.assertTrue(msg.startswith(DRIFT_PREFIX), msg)
        self.assertIn("jsdom", msg)
        self.assertNotIn("version", msg)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), sibling)

    def test_newer_central_package_json_is_copied_keeping_sibling_version(self) -> None:
        """A newer central package.json is copied; the sibling keeps its own version."""
        central = {"name": "gui", "devDependencies": {"vite": "^6.5.0"}}
        self.write(self.central_dir / "lemgendary-ai-studio-gui.package.json", json.dumps(central))
        sibling = {"name": "gui", "version": "2.10.13", "devDependencies": {"vite": "^6.4.4"}}
        target = self.write(self.gui / "package.json", json.dumps(sibling))

        results = sync_all_manifests(self.base)

        ok, msg = results["lemgendary-ai-studio-gui (npm)"]
        self.assertTrue(ok, msg)
        written = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(written, {**central, "version": "2.10.13"})
        self.assertEqual(list(written), ["name", "version", "devDependencies"])

    def test_differing_version_is_not_npm_drift(self) -> None:
        """The project's own release version is not dependency drift."""
        drift = find_package_json_drift(
            {"version": "1.0.0", "dependencies": {"react": "^18.3.1"}},
            {"version": "2.10.37", "dependencies": {"react": "^18.3.1"}},
        )
        self.assertEqual(drift, [])

    def test_merge_keeps_sibling_version_and_drops_central_version(self) -> None:
        """The merged manifest carries the sibling's version, never the central one."""
        self.assertEqual(
            merge_npm_manifest({"version": "9.9.9", "a": 1}, {"a": 0, "version": "2.0.0"}),
            {"a": 1, "version": "2.0.0"},
        )
        self.assertEqual(merge_npm_manifest({"version": "9.9.9", "a": 1}, {}), {"a": 1})

    def test_npm_drift_detects_lower_dependency(self) -> None:
        """npm drift detects a dependency the central manifest lowers."""
        drift = find_package_json_drift(
            {"dependencies": {"react": "^18.2.0"}},
            {"dependencies": {"react": "^18.3.1"}},
        )
        self.assertEqual(len(drift), 1)
        self.assertIn("react", drift[0])


if __name__ == "__main__":
    unittest.main()
