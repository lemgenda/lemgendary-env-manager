"""The ``ecosystem_ssot`` gate: facts from ``ecosystem.json`` are not copied elsewhere.

Per project it reports literal service ports, the Kaggle owner, the docs
URL and lists of project folders outside the allowlist
(:mod:`.allowlist`). For the Tauri GUI it also checks that the snapshot,
the CSP ports, the pipeline steps and the ``ProjectHealth`` type agree
with the env-manager's files in the same workspace (:mod:`.gui`).

``ECOSYSTEM_SSOT_SEVERITY`` is the one setting that decides whether the
findings fail validation (``Severity.ERROR``) or are only reported
(``Severity.WARNING``).
"""

from pathlib import Path

from env_manager.ecosystem import ecosystem
from env_manager.validation.gate import Severity
from env_manager.validation.gates.ecosystem_ssot.gui import (
    GuiContext,
    is_tauri_gui,
    run_gui_checks,
)
from env_manager.validation.gates.ecosystem_ssot.scan import scan_project_literals
from env_manager.validation.models import FileValidationViolation
from env_manager.workspace import workspace_root

ECOSYSTEM_SSOT_SEVERITY = Severity.WARNING
"""Set to ``Severity.ERROR`` to make ``ecosystem_ssot`` findings fail validation."""


def run_ecosystem_ssot(project_dir: Path) -> list[FileValidationViolation]:
    """Return every ecosystem single-source-of-truth finding for one project."""
    eco = ecosystem()
    found = scan_project_literals(project_dir, eco)
    if is_tauri_gui(project_dir):
        env_dir = workspace_root() / eco.self_folder()
        found.extend(run_gui_checks(GuiContext(project_dir, env_dir, eco)))
    return found


def scan_workspace(root: Path | None = None) -> dict[str, list[FileValidationViolation]]:
    """Run the gate on every validated project present in the workspace."""
    base = workspace_root() if root is None else root
    return {
        project.folder: run_ecosystem_ssot(base / project.folder)
        for project in ecosystem().validated_projects()
        if (base / project.folder).is_dir()
    }


__all__ = ["ECOSYSTEM_SSOT_SEVERITY", "run_ecosystem_ssot", "scan_workspace"]
