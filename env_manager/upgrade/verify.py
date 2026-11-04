"""Post-upgrade bookkeeping: tallying apply events and re-validating the projects."""

from dataclasses import dataclass
from pathlib import Path

from env_manager.upgrade.models import UpgradeEvent
from env_manager.validator import ProjectValidationReport, validate_project
from env_manager.venv_manager import discover_projects


@dataclass
class UpgradeTally:
    """Counts of upgraded, failed and blocked events from one apply run."""

    upgraded: int = 0
    failed: int = 0
    blocked: int = 0

    def record(self, event: UpgradeEvent) -> None:
        """Count ``event`` if its status is one of the tallied ones."""
        if event.status == "upgraded":
            self.upgraded += 1
        elif event.status == "failed":
            self.failed += 1
        elif event.status == "blocked":
            self.blocked += 1

    def summary(self) -> str:
        """Return e.g. ``"3 upgraded, 1 blocked"``; zero blocked or failed are omitted."""
        text = f"{self.upgraded} upgraded"
        if self.blocked:
            text += f", {self.blocked} blocked"
        if self.failed:
            text += f", {self.failed} failed"
        return text


def validate_upgraded_projects(
    base_dir: Path, project: str | None = None,
) -> list[tuple[str, ProjectValidationReport]]:
    """Validate every discovered project, or only ``project``, after an upgrade."""
    return [
        (p.name, validate_project(
            Path(p.project_dir), is_node_project=getattr(p, "is_node_project", False),
        ))
        for p in discover_projects(base_dir)
        if not project or p.name == project
    ]
