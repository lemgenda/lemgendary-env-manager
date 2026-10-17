"""Unit tests for health audit, version drift detection, and manifest coverage."""

from __future__ import annotations

import unittest

from env_manager.health_checker import (
    ManifestCoverageEntry,
    PinInfo,
    ProjectHealth,
    UpgradeStatus,
    VersionDriftEntry,
)


class TestHealthChecker(unittest.TestCase):
    """Verifies health audit data structures, version drift entries, and pin reconciliation."""

    def test_pin_info_serialization(self) -> None:
        """Verify PinInfo pin types and dictionary conversion."""
        pin = PinInfo(pin_type="exact", specifier="==2.1.0", installed="2.1.0")
        d = pin.to_dict()
        self.assertEqual(d["pin_type"], "exact")
        self.assertEqual(d["specifier"], "==2.1.0")
        self.assertEqual(d["installed"], "2.1.0")

    def test_project_health_status(self) -> None:
        """Verify ProjectHealth properties and healthy verdict."""
        health = ProjectHealth(
            name="lemgendary-training-suite",
            project_dir="/path/to/project",
            venv_exists=True,
            python_version="3.12.12",
            total_required=15,
            total_installed=15,
            missing_packages=[],
            installed_packages={"torch": "2.1.0"},
            is_healthy=True,
        )
        self.assertTrue(health.is_healthy)
        self.assertEqual(len(health.missing_packages), 0)
        d = health.to_dict()
        self.assertEqual(d["name"], "lemgendary-training-suite")

    def test_version_drift_entry_detection(self) -> None:
        """Verify VersionDriftEntry correctly flags version drift across projects."""
        pin_a = PinInfo(pin_type="exact", specifier="==1.0.0", installed="1.0.0")
        pin_b = PinInfo(pin_type="exact", specifier="==2.0.0", installed="2.0.0")

        drift_entry = VersionDriftEntry(
            package_name="common-pkg",
            versions={"project-a": "1.0.0", "project-b": "2.0.0"},
            pins={"project-a": pin_a, "project-b": pin_b},
            upgrades={
                "project-a": UpgradeStatus(current="1.0.0", latest="2.0.0", safe=True),
                "project-b": UpgradeStatus(current="2.0.0", latest="2.0.0", safe=True),
            },
            has_drift=True,
            has_pin_mismatch=True,
            projects_declared=2,
        )

        self.assertTrue(drift_entry.has_drift)
        self.assertTrue(drift_entry.has_pin_mismatch)
        d = drift_entry.to_dict()
        self.assertTrue(drift_entry.upgrades["project-a"].is_outdated)
        self.assertFalse(drift_entry.upgrades["project-b"].is_outdated)

    def test_manifest_coverage_entry(self) -> None:
        """Verify ManifestCoverageEntry reconciliation calculations."""
        cov = ManifestCoverageEntry(
            project_name="lemgendary-datasets",
            declared_count=20,
            installed_count=25,
            missing=[],
            extra=["transitive-pkg-1", "transitive-pkg-2"],
            platform_skipped=[],
        )
        self.assertEqual(len(cov.missing), 0)
        self.assertEqual(len(cov.extra), 2)
        d = cov.to_dict()
        self.assertEqual(d["project_name"], "lemgendary-datasets")


if __name__ == "__main__":
    unittest.main()
