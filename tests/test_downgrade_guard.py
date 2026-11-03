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
        path.write_text(text, encoding="utf-8")
        return path


class TestRequirementDowngradeGuard(_TempWorkspace):
    def test_central_lower_pin_is_not_written_and_reported(self) -> None:
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
        central = self.write(self.central_dir / "requirements-datasets.txt", "torch==2.16.0\n")
        target = self.write(self.sibling / "requirements.txt", "torch==2.15.0\n")

        ok, msg = sync_manifest_to_project(central, self.sibling)

        self.assertTrue(ok, msg)
        self.assertEqual(target.read_text(encoding="utf-8"), "torch==2.16.0\n")

    def test_equal_pins_are_written(self) -> None:
        central = self.write(self.central_dir / "requirements-datasets.txt", "numpy==2.5.4\n")
        target = self.write(self.sibling / "requirements.txt", "numpy==2.5.4\n")

        ok, msg = sync_manifest_to_project(central, self.sibling)

        self.assertTrue(ok, msg)
        self.assertEqual(target.read_text(encoding="utf-8"), "numpy==2.5.4\n")

    def test_missing_sibling_file_is_created(self) -> None:
        central = self.write(self.central_dir / "requirements-datasets.txt", "numpy==2.5.4\n")

        ok, msg = sync_manifest_to_project(central, self.sibling)

        self.assertTrue(ok, msg)
        self.assertTrue((self.sibling / "requirements.txt").exists())

    def test_loosened_range_and_dropped_pin_and_removed_package_are_drift(self) -> None:
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
        central = self.write(self.central_dir / "c.txt",
                             "MetaTrader5==5.0.50; sys_platform == 'win32'\n")
        sibling = self.write(self.sibling / "requirements.txt",
                             "MetaTrader5==5.0.45; sys_platform == 'win32'\n")

        self.assertEqual(find_requirement_drift(central, sibling), [])

    def test_upgrade_with_extra_central_package_is_not_drift(self) -> None:
        central = self.write(self.central_dir / "c.txt", "numpy==2.6.0\nhttpx2==2.16.0\n")
        sibling = self.write(self.sibling / "requirements.txt", "numpy==2.5.4\n")

        self.assertEqual(find_requirement_drift(central, sibling), [])


class TestPackageJsonDowngradeGuard(_TempWorkspace):
    def setUp(self) -> None:
        super().setUp()
        self.gui = self.base / "lemgendary-ai-studio-gui"
        self.gui.mkdir()

    def test_older_central_package_json_is_not_copied(self) -> None:
        central = {"version": "2.10.12", "devDependencies": {"vite": "^6.4.4"}}
        sibling = {"version": "2.10.13", "devDependencies": {"vite": "^6.4.4", "jsdom": "25.0.1"}}
        self.write(self.central_dir / "lemgendary-ai-studio-gui.package.json", json.dumps(central))
        target = self.write(self.gui / "package.json", json.dumps(sibling))

        results = sync_all_manifests(self.base)

        ok, msg = results["lemgendary-ai-studio-gui (npm)"]
        self.assertFalse(ok)
        self.assertTrue(msg.startswith(DRIFT_PREFIX), msg)
        self.assertIn("jsdom", msg)
        self.assertIn("version", msg)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), sibling)

    def test_newer_central_package_json_is_copied(self) -> None:
        central = {"version": "2.10.14", "devDependencies": {"vite": "^6.5.0"}}
        self.write(self.central_dir / "lemgendary-ai-studio-gui.package.json", json.dumps(central))
        sibling = {"version": "2.10.13", "devDependencies": {"vite": "^6.4.4"}}
        target = self.write(self.gui / "package.json", json.dumps(sibling))

        results = sync_all_manifests(self.base)

        ok, msg = results["lemgendary-ai-studio-gui (npm)"]
        self.assertTrue(ok, msg)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), central)

    def test_npm_drift_detects_lower_dependency(self) -> None:
        drift = find_package_json_drift(
            {"dependencies": {"react": "^18.2.0"}},
            {"dependencies": {"react": "^18.3.1"}},
        )
        self.assertEqual(len(drift), 1)
        self.assertIn("react", drift[0])


class TestRealCentralManifestsHaveNoDrift(unittest.TestCase):
    """Read-only check: the committed central manifests are not behind the siblings."""

    def test_central_is_not_behind_siblings(self) -> None:
        env_mgr = Path(__file__).resolve().parent.parent
        base = env_mgr.parent
        central = env_mgr / "requirements"
        pairs = {
            "lemgendary-training-suite": "requirements-training.txt",
            "lemgendary-datasets": "requirements-datasets.txt",
            "lemgendary-env-manager": "requirements-env-manager.txt",
            "lemgendary-docs": "requirements-documentation.txt",
        }
        for project, manifest in pairs.items():
            sibling = base / project / "requirements.txt"
            if not sibling.exists():
                continue
            with self.subTest(project=project):
                self.assertEqual(find_requirement_drift(central / manifest, sibling), [])


if __name__ == "__main__":
    unittest.main()
