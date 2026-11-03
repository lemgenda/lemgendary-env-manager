"""The ``ecosystem_ssot`` gate: literal ecosystem facts and GUI parity checks.

Every fact is read from the ecosystem registry at runtime, so this file
holds no port, owner, URL or folder list itself.
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from env_manager.ecosystem import REPO_ECOSYSTEM_FILE, ecosystem
from env_manager.validation import registry
from env_manager.validation.gate import Bucket, Gate, ProjectContext, Severity
from env_manager.validation.gates.ecosystem_ssot import (
    ECOSYSTEM_SSOT_SEVERITY,
    run_ecosystem_ssot,
    scan_workspace,
)
from env_manager.validation.gates.ecosystem_ssot.gui import (
    CSP_PORTS,
    PIPELINE_STEPS,
    PROJECT_HEALTH,
    SKIPPED,
    SNAPSHOT_MISMATCH,
    SNAPSHOT_PATH,
    TAURI_CONFIG_PATH,
)
from env_manager.validation.gates.ecosystem_ssot.literals import (
    DOCS_URL_LITERAL,
    KAGGLE_OWNER_LITERAL,
    PORT_LITERAL,
    PROJECT_LIST_LITERAL,
)
from env_manager.validation.models import FileValidationViolation
from env_manager.validator import run_gates, validate_project

ECO = ecosystem()
PORTS = sorted(svc.port for svc in ECO.services)
PORT = PORTS[0]
FOLDERS = [p.folder for p in ECO.projects]
GUI_FOLDER = next(p.folder for p in ECO.projects if p.is_node)
DEV_PORT = 1420


def _types(found: list[FileValidationViolation]) -> list[str]:
    return sorted(v.violation_type for v in found)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture(name="project")
def fixture_project(fake_workspace: Path) -> Path:
    """A plain (non-GUI) project in the fake workspace."""
    return fake_workspace / ECO.self_folder()


@pytest.fixture(name="gui")
def fixture_gui(fake_workspace: Path) -> Path:
    """A GUI checkout whose Tauri config, snapshot and source agree with the registry."""
    gui = fake_workspace / GUI_FOLDER
    sources = " ".join(f"http://127.0.0.1:{port} ws://localhost:{port}" for port in PORTS)
    policy = {"connect-src": f"'self' ipc: {sources}"}
    dev_policy = {"connect-src": f"'self' ws://localhost:{DEV_PORT} {sources}"}
    config = {
        "build": {"devUrl": f"http://localhost:{DEV_PORT}"},
        "app": {"security": {"csp": policy, "devCsp": dev_policy}},
    }
    _write(gui / TAURI_CONFIG_PATH, json.dumps(config, indent=2))
    source = REPO_ECOSYSTEM_FILE.read_text(encoding="utf-8")
    _write(fake_workspace / ECO.self_folder() / "ecosystem" / "ecosystem.json", source)
    _write(gui / SNAPSHOT_PATH, source)
    _write(gui / "src" / "contracts.ts", "export interface ProjectHealth {\n  name: string;\n"
           "  is_healthy?: boolean;\n}\n")
    schema = {"properties": {"name": {}, "is_healthy": {}}}
    openapi = {"components": {"schemas": {"ProjectHealth": schema}}}
    _write(fake_workspace / ECO.self_folder() / "openapi.json", json.dumps(openapi))
    return gui


# --- literal facts ---------------------------------------------------------


def test_literal_port_is_flagged(project: Path) -> None:
    """A service port written as ``host:port`` or ``port=`` is a finding."""
    _write(project / "a.py", f'URL = "http://127.0.0.1:{PORT}"\nserve(port={PORT})\n')
    found = run_ecosystem_ssot(project)
    assert _types(found) == [PORT_LITERAL, PORT_LITERAL]
    assert [v.line_number for v in found] == [1, 2]


def test_port_number_without_port_context_passes(project: Path) -> None:
    """A number that happens to equal a port but is not used as one is not flagged."""
    _write(project / "a.md", f"Trained on {PORT} images and {PORT}0 more.\n")
    _write(project / "a.ts", f"const row = {{ count: {PORT}, total: {PORT}0 }};\n")
    assert not run_ecosystem_ssot(project)


def test_kaggle_owner_and_docs_url_are_flagged(project: Path) -> None:
    """The Kaggle owner and the docs URL are findings outside the registry."""
    owner, url = ECO.identity.kaggle_owner, ECO.identity.docs_url
    _write(project / "a.yaml", f"ref: kaggle://{owner}/data\ndocs: {url}/index.html\n")
    assert _types(run_ecosystem_ssot(project)) == [DOCS_URL_LITERAL, KAGGLE_OWNER_LITERAL]


def test_project_list_of_three_nearby_folders_is_flagged_once(project: Path) -> None:
    """Three folders on nearby lines form one list finding."""
    _write(project / "a.py", "NAMES = [\n" + "".join(f'    "{f}",\n' for f in FOLDERS[:3]) + "]\n")
    found = run_ecosystem_ssot(project)
    assert _types(found) == [PROJECT_LIST_LITERAL]
    assert [v.line_number for v in found] == [2]


def test_two_folders_or_distant_mentions_are_not_a_list(project: Path) -> None:
    """Fewer than three folders, or mentions far apart, are not a list."""
    _write(project / "two.py", f'PAIR = ["{FOLDERS[0]}", "{FOLDERS[1]}"]\n')
    spread = "".join(f"{f}\n\n\n\n" for f in FOLDERS[:3])
    _write(project / "spread.md", spread)
    assert not run_ecosystem_ssot(project)


@pytest.mark.parametrize("relative", [
    "ecosystem/ecosystem.json",
    "REVIEW_REPORT.md",
    "openapi.json",
    "package-lock.json",
    "src-tauri/Cargo.lock",
    "CHANGELOG.md",
    "tests/fixtures/services.json",
])
def test_allowlisted_files_are_not_scanned(project: Path, relative: str) -> None:
    """Registry files, reports, lockfiles, changelogs and test fixtures may hold facts."""
    _write(project / relative, f'{{"port": {PORT}}}\n')
    assert not run_ecosystem_ssot(project)


def test_fixtures_outside_tests_are_scanned(project: Path) -> None:
    """A ``fixtures`` folder that is not under a test folder is not allowlisted."""
    _write(project / "fixtures" / "a.json", f'{{"port": {PORT}}}\n')
    assert _types(run_ecosystem_ssot(project)) == [PORT_LITERAL]


def test_readme_changelog_section_is_skipped(project: Path) -> None:
    """Only the README changelog section is history; other sections are scanned."""
    readme = (
        "# Project\n\n"
        f"Listens on port {PORT}.\n\n"
        "## Changelog\n\n### v1.0.0\n\n"
        f"- Moved to port {PORT}.\n\n"
        "## Usage\n\n"
        f"Open http://localhost:{PORT}.\n"
    )
    _write(project / "README.md", readme)
    assert [v.line_number for v in run_ecosystem_ssot(project)] == [3, 13]


# --- GUI parity --------------------------------------------------------------


def test_consistent_gui_has_no_findings(gui: Path) -> None:
    """Snapshot, CSP, derived steps and ProjectHealth all agree: no findings."""
    assert not run_ecosystem_ssot(gui)


def test_stale_snapshot_is_flagged(gui: Path) -> None:
    """A snapshot that differs from the source file is a finding."""
    _write(gui / SNAPSHOT_PATH, json.dumps({"schema_version": 1}))
    assert SNAPSHOT_MISMATCH in _types(run_ecosystem_ssot(gui))


def test_csp_port_drift_is_flagged_and_dev_port_ignored(gui: Path) -> None:
    """A missing service port in one policy is a finding; the dev server port is not."""
    path = gui / TAURI_CONFIG_PATH
    config = json.loads(path.read_text(encoding="utf-8"))
    config["app"]["security"]["csp"]["connect-src"] = f"'self' http://127.0.0.1:{PORT}"
    _write(path, json.dumps(config, indent=2))
    found = [v for v in run_ecosystem_ssot(gui) if v.violation_type == CSP_PORTS]
    assert len(found) == 1
    assert found[0].message.startswith("csp ")
    assert found[0].line_number > 0


def test_own_pipeline_table_is_skipped_and_reported(gui: Path) -> None:
    """While the GUI keeps its own step table the count check is skipped with a note."""
    table = "export const PIPELINE_STEPS: Step[] = [\n  {\n    num: 1,\n  },\n];\n"
    _write(gui / "src" / "components" / "Steps.tsx", table)
    found = run_ecosystem_ssot(gui)
    assert _types(found) == [SKIPPED]
    assert "own PIPELINE_STEPS table (1 steps" in found[0].message


def test_derived_step_count_drift_is_flagged(gui: Path) -> None:
    """With derived steps, a snapshot step count that differs from the registry is a finding."""
    snapshot = json.loads(REPO_ECOSYSTEM_FILE.read_text(encoding="utf-8"))
    snapshot["pipeline_steps"] = snapshot["pipeline_steps"][:1]
    _write(gui / SNAPSHOT_PATH, json.dumps(snapshot))
    assert _types(run_ecosystem_ssot(gui)) == [SNAPSHOT_MISMATCH, PIPELINE_STEPS]


def test_project_health_key_drift_is_flagged(gui: Path) -> None:
    """A GUI ``ProjectHealth`` key missing from the OpenAPI schema is a finding."""
    _write(gui / "src" / "contracts.ts",
           "export interface ProjectHealth {\n  name: string;\n  extra: number;\n}\n")
    found = run_ecosystem_ssot(gui)
    assert _types(found) == [PROJECT_HEALTH]
    assert "GUI only ['extra']" in found[0].message


def test_missing_openapi_schema_is_skipped_and_reported(gui: Path, fake_workspace: Path) -> None:
    """Without a ``ProjectHealth`` schema the check is skipped with a note."""
    _write(fake_workspace / ECO.self_folder() / "openapi.json", "{}")
    found = run_ecosystem_ssot(gui)
    assert _types(found) == [SKIPPED]
    assert "no ProjectHealth schema" in found[0].message


# --- registry and severity -----------------------------------------------------


def _ssot_gate() -> Gate:
    return next(g for g in registry.GATES if g.name == "ecosystem_ssot")


def test_gate_is_registered_at_the_configured_severity() -> None:
    """The registry entry carries the one severity setting (warning for now)."""
    assert ECOSYSTEM_SSOT_SEVERITY is Severity.WARNING
    assert _ssot_gate().severity is ECOSYSTEM_SSOT_SEVERITY


def test_warning_findings_do_not_fail_validation(project: Path) -> None:
    """Warning-severity findings land in ``warnings`` and the project still passes."""
    _write(project / "a.md", f"See http://localhost:{PORT}.\n")
    report = validate_project(project)
    assert _types(report.warnings) == [PORT_LITERAL]
    assert not report.domain_errors


def test_error_severity_reports_into_the_domain_bucket(project: Path) -> None:
    """Switching the severity to error moves the findings into the failing bucket."""
    _write(project / "a.md", f"See http://localhost:{PORT}.\n")
    gate = replace(_ssot_gate(), severity=Severity.ERROR)
    found = run_gates(ProjectContext(project), [gate])
    assert (_types(found[Bucket.DOMAIN]), found[Bucket.WARNINGS]) == ([PORT_LITERAL], [])


def test_scan_workspace_reports_each_present_project(fake_workspace: Path) -> None:
    """Every validated project folder in the workspace gets an entry."""
    present = {p.folder for p in ECO.validated_projects() if (fake_workspace / p.folder).is_dir()}
    assert set(scan_workspace()) == present
