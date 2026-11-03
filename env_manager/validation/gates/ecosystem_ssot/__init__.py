"""The ``ecosystem_ssot`` gate: facts from ``ecosystem.json`` are not copied elsewhere.

Per project it checks every text file by its mode (:mod:`.allowlist`):
code, configuration and tests may not spell out a service port, the Kaggle
owner, the docs URL or a list of project folders (:mod:`.literals`);
documentation and records may, but only with the right values
(:mod:`.consistency`). Asset projects (``kind: asset``) hold only
documentation and generated records. For the Tauri GUI it also checks that
the snapshot, the CSP ports, the pipeline steps and the ``ProjectHealth``
type agree with the env-manager's files in the same workspace (:mod:`.gui`).

``ECOSYSTEM_SSOT_SEVERITY`` (``Severity.ERROR``) is the one setting that
decides whether the findings fail validation or are only reported
(``Severity.WARNING``). Checks that cannot run because an optional input is
absent (a GUI that keeps its own step table, a missing OpenAPI file) leave an
``ssot_skipped`` note that is logged and never fails; a source file that
exists but cannot be parsed is an ``ssot_unreadable`` finding and fails.
"""

from pathlib import Path

from env_manager._logging import get_logger
from env_manager.ecosystem import Ecosystem, ecosystem
from env_manager.validation.gate import Severity
from env_manager.validation.gates.ecosystem_ssot.gui import (
    SKIPPED,
    GuiContext,
    is_tauri_gui,
    run_gui_checks,
)
from env_manager.validation.gates.ecosystem_ssot.scan import scan_project_literals
from env_manager.validation.models import FileValidationViolation
from env_manager.workspace import workspace_root

_log = get_logger(__name__)

ECOSYSTEM_SSOT_SEVERITY = Severity.ERROR
"""``Severity.ERROR`` makes findings fail validation; ``WARNING`` only reports them."""


def is_asset_project(project_dir: Path, eco: Ecosystem) -> bool:
    """True when the folder (or a legacy name of it) is a ``kind: asset`` project."""
    return any(
        project.kind == "asset"
        and project_dir.name in (project.folder, *project.legacy_folders)
        for project in eco.projects
    )


def collect_ssot_findings(project_dir: Path) -> list[FileValidationViolation]:
    """Return every finding and ``ssot_skipped`` note for one project."""
    eco = ecosystem()
    found = scan_project_literals(project_dir, eco, is_asset_project(project_dir, eco))
    if is_tauri_gui(project_dir):
        env_dir = workspace_root() / eco.self_folder()
        found.extend(run_gui_checks(GuiContext(project_dir, env_dir, eco)))
    return found


def run_ecosystem_ssot(project_dir: Path) -> list[FileValidationViolation]:
    """Return the failing single-source-of-truth findings for one project.

    ``ssot_skipped`` notes are logged and left out: a skipped optional check
    is not a failure.
    """
    found = collect_ssot_findings(project_dir)
    for note in found:
        if note.violation_type == SKIPPED:
            _log.info("ecosystem_ssot: %s", note.message)
    return [f for f in found if f.violation_type != SKIPPED]


def absent_projects(root: Path | None = None) -> list[str]:
    """Validated project folders that are not checked out in the workspace."""
    base = workspace_root() if root is None else root
    return [p.folder for p in ecosystem().validated_projects() if not (base / p.folder).is_dir()]


def scan_workspace(root: Path | None = None) -> dict[str, list[FileValidationViolation]]:
    """Run the gate on every validated project present in the workspace.

    A validated project that is not checked out is logged and left out; it
    is not a finding.
    """
    base = workspace_root() if root is None else root
    for folder in absent_projects(base):
        _log.warning("ecosystem_ssot: project %s is not in %s; not scanned", folder, base)
    return {
        project.folder: run_ecosystem_ssot(base / project.folder)
        for project in ecosystem().validated_projects()
        if (base / project.folder).is_dir()
    }


__all__ = [
    "ECOSYSTEM_SSOT_SEVERITY",
    "absent_projects",
    "collect_ssot_findings",
    "is_asset_project",
    "run_ecosystem_ssot",
    "scan_workspace",
]
