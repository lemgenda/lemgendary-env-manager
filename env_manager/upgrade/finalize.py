"""After an upgrade: validate the upgraded projects, then write the manifests back.

The manifests are the source of truth for every later install, so the
installed versions only reach them once the upgraded projects pass
validation. ``lem-env update`` and the GUI update job share this step.
"""

from dataclasses import dataclass, field
from pathlib import Path

from env_manager.upgrade.manifest_sync import sync_manifests_from_venvs
from env_manager.upgrade.verify import validate_upgraded_projects
from env_manager.validator import ProjectValidationReport


@dataclass
class UpgradeFinalization:
    """Validation reports and, when they all passed, the manifest sync results."""

    reports: list[tuple[str, ProjectValidationReport]]
    manifest_sync: dict[str, tuple[bool, str]] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """True when every upgraded project passed validation."""
        return all(report.passed for _, report in self.reports)


def finalize_upgrade(base_dir: Path, project: str | None = None) -> UpgradeFinalization:
    """Validate ``project`` (or every project); sync manifests from venvs only on success."""
    reports = validate_upgraded_projects(base_dir, project)
    result = UpgradeFinalization(reports)
    if result.passed:
        result.manifest_sync = sync_manifests_from_venvs(base_dir, project=project)
    return result
