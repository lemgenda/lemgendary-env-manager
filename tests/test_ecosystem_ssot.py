"""The ``ecosystem_ssot`` gate: literal ecosystem facts and GUI parity checks.

Every fact is read from the ecosystem registry at runtime, so this file
holds no port, owner, URL or folder list itself.
"""

import json
import logging
from dataclasses import fields, replace
from pathlib import Path, PurePosixPath

import pytest

from env_manager import server
from env_manager.ecosystem import REPO_ECOSYSTEM_FILE, ecosystem
from env_manager.health.models import ProjectHealth
from env_manager.validation import registry
from env_manager.validation.gate import Bucket, Gate, ProjectContext, Severity
from env_manager.validation.gates.ecosystem_ssot import (
    ECOSYSTEM_SSOT_SEVERITY,
    absent_projects,
    collect_ssot_findings,
    run_ecosystem_ssot,
    scan_workspace,
)
from env_manager.validation.gates.ecosystem_ssot.allowlist import FileMode, file_mode
from env_manager.validation.gates.ecosystem_ssot.consistency import (
    LEGACY_FOLDER,
    WRONG_DOCS_URL,
    WRONG_KAGGLE_OWNER,
    WRONG_PORT,
)
from env_manager.validation.gates.ecosystem_ssot.gui import (
    CSP_PORTS,
    MANIFESTS_DIR,
    NPM_MANIFEST,
    PACKAGE_JSON,
    openapi_schema_keys,
    PIPELINE_STEPS,
    PROJECT_HEALTH,
    SKIPPED,
    SNAPSHOT_MISMATCH,
    SNAPSHOT_PATH,
    TAURI_CONFIG_PATH,
    UNREADABLE,
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
SERVICE = ECO.services[0]
OTHER_PORT = next(svc.port for svc in ECO.services if svc.port != SERVICE.port)
PACKAGE = {
    "name": "gui",
    "version": "1.0.0",
    "scripts": {"lint": "eslint src"},
    "devDependencies": {"eslint": "9.0.0"},
}
OWNER = ECO.identity.kaggle_owner
ASSET = next(p for p in ECO.projects if p.kind == "asset" and p.legacy_folders)


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
    package = json.dumps(PACKAGE, indent=2)
    _write(_central_manifest(fake_workspace), package)
    _write(gui / PACKAGE_JSON, package)
    return gui


def _central_manifest(workspace: Path) -> Path:
    manifest = ECO.project(GUI_FOLDER).npm_manifest
    assert manifest is not None
    return workspace / ECO.self_folder() / MANIFESTS_DIR / manifest


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
    """Only the README changelog section is history; other sections are checked."""
    readme = (
        "# Project\n\n"
        f"The {SERVICE.project} sidecar listens on port {OTHER_PORT}.\n\n"
        "## Changelog\n\n### v1.0.0\n\n"
        f"- Moved {SERVICE.project} to port {OTHER_PORT}.\n\n"
        "## Usage\n\n"
        f"Open {SERVICE.project} at http://localhost:{OTHER_PORT}.\n"
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
    found = collect_ssot_findings(gui)
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
    found = collect_ssot_findings(gui)
    assert _types(found) == [SKIPPED]
    assert "no ProjectHealth schema" in found[0].message


def test_gui_package_json_drift_is_flagged(gui: Path) -> None:
    """A GUI ``package.json`` that moved past the central manifest is a finding."""
    _write(gui / PACKAGE_JSON, json.dumps({**PACKAGE, "devDependencies": {"eslint": "9.1.0"}}))
    found = run_ecosystem_ssot(gui)
    assert _types(found) == [NPM_MANIFEST]
    assert "['devDependencies']" in found[0].message


def test_key_order_does_not_count_as_package_json_drift(gui: Path) -> None:
    """Only values are compared; reordered keys are not drift."""
    _write(gui / PACKAGE_JSON, json.dumps(dict(reversed(PACKAGE.items()))))
    assert not run_ecosystem_ssot(gui)


@pytest.mark.parametrize("side", ["central", "gui"])
def test_missing_package_json_is_skipped_and_reported(
    gui: Path, fake_workspace: Path, side: str,
) -> None:
    """Without the central manifest or the GUI ``package.json`` the check is skipped."""
    target = _central_manifest(fake_workspace) if side == "central" else gui / PACKAGE_JSON
    target.unlink()
    found = collect_ssot_findings(gui)
    assert _types(found) == [SKIPPED]
    assert f"{target.name} is not present" in found[0].message


def test_unparseable_package_json_is_a_finding(gui: Path) -> None:
    """A GUI ``package.json`` that is not JSON fails rather than skips."""
    _write(gui / PACKAGE_JSON, "{")
    assert _types(run_ecosystem_ssot(gui)) == [UNREADABLE]


# --- registry and severity -----------------------------------------------------


def _ssot_gate() -> Gate:
    return next(g for g in registry.GATES if g.name == "ecosystem_ssot")


def test_gate_is_registered_at_the_configured_severity() -> None:
    """The registry entry carries the one severity setting (error)."""
    assert ECOSYSTEM_SSOT_SEVERITY is Severity.ERROR
    assert _ssot_gate().severity is ECOSYSTEM_SSOT_SEVERITY


def test_findings_fail_validation_at_error_severity(project: Path) -> None:
    """At error severity the findings land in the failing domain bucket."""
    _write(project / "a.py", f'URL = "http://localhost:{PORT}"\n')
    report = validate_project(project)
    assert _types(report.domain_errors) == [PORT_LITERAL]
    assert not report.warnings


def test_skip_notes_do_not_fail_the_gate(gui: Path) -> None:
    """The gate drops ``ssot_skipped`` notes; only the unfiltered collector shows them."""
    table = "export const PIPELINE_STEPS: Step[] = [\n  {\n    num: 1,\n  },\n];\n"
    _write(gui / "src" / "components" / "Steps.tsx", table)
    assert _types(collect_ssot_findings(gui)) == [SKIPPED]
    assert not run_ecosystem_ssot(gui)


def test_unparseable_tauri_config_fails(gui: Path) -> None:
    """A Tauri config that exists but is not JSON is a failing finding, not a skip."""
    _write(gui / TAURI_CONFIG_PATH, "{ not json")
    assert _types(run_ecosystem_ssot(gui)) == [UNREADABLE]


def test_unparseable_openapi_fails(gui: Path, fake_workspace: Path) -> None:
    """An OpenAPI file that exists but is not JSON is a failing finding."""
    _write(fake_workspace / ECO.self_folder() / "openapi.json", "{ not json")
    assert _types(run_ecosystem_ssot(gui)) == [UNREADABLE]


def test_missing_openapi_file_is_only_a_note(gui: Path, fake_workspace: Path) -> None:
    """An absent OpenAPI file is optional: a skip note, no failure."""
    (fake_workspace / ECO.self_folder() / "openapi.json").unlink()
    assert _types(collect_ssot_findings(gui)) == [SKIPPED]
    assert not run_ecosystem_ssot(gui)


def test_error_severity_reports_into_the_domain_bucket(project: Path) -> None:
    """Switching the severity to error moves the findings into the failing bucket."""
    _write(project / "a.py", f'URL = "http://localhost:{PORT}"\n')
    gate = replace(_ssot_gate(), severity=Severity.ERROR)
    found = run_gates(ProjectContext(project), [gate])
    assert (_types(found[Bucket.DOMAIN]), found[Bucket.WARNINGS]) == ([PORT_LITERAL], [])


def test_scan_workspace_reports_each_present_project(fake_workspace: Path) -> None:
    """Every validated project folder in the workspace gets an entry."""
    present = {p.folder for p in ECO.validated_projects() if (fake_workspace / p.folder).is_dir()}
    assert set(scan_workspace()) == present


def test_absent_project_is_logged_not_a_finding(caplog: pytest.LogCaptureFixture) -> None:
    """A validated project that is not checked out is reported in the log only."""
    absent = absent_projects()
    assert absent
    with caplog.at_level(logging.WARNING):
        results = scan_workspace()
    assert not set(absent) & set(results)
    assert all(folder in caplog.text for folder in absent)


# --- file modes ------------------------------------------------------------------


@pytest.mark.parametrize(("relative", "asset", "mode"), [
    ("ecosystem/ecosystem.json", False, FileMode.SKIP),
    ("tests/fixtures/a.json", False, FileMode.SKIP),
    ("README.md", False, FileMode.CONSISTENCY),
    ("papers/manual.html", False, FileMode.CONSISTENCY),
    ("unified_models_v2.yaml", False, FileMode.CONSISTENCY),
    ("model/model_metadata.yaml", True, FileMode.CONSISTENCY),
    ("config.yaml", False, FileMode.STRICT),
    ("src/app.ts", False, FileMode.STRICT),
    ("tests/notes.md", False, FileMode.STRICT),
    ("src/__tests__/a.test.ts", False, FileMode.STRICT),
    ("test_api.py", False, FileMode.STRICT),
])
def test_file_mode(relative: str, asset: bool, mode: FileMode) -> None:
    """Docs and records are checked for consistency; code, config and tests strictly."""
    assert file_mode(PurePosixPath(relative), asset) is mode


# --- documentation and records: consistency only ------------------------------------


def test_documentation_with_registry_values_passes(project: Path) -> None:
    """Docs may write out the right port, owner, docs URL and folder list."""
    folders = "\n".join(f"- `{f}`" for f in FOLDERS)
    _write(project / "docs" / "guide.md", (
        f"The {SERVICE.project} sidecar listens on http://127.0.0.1:{SERVICE.port}.\n"
        f"Data: kaggle://{OWNER}/lemgendizedexample and "
        f"https://www.kaggle.com/datasets/{OWNER}/lemgendizedexample\n"
        f"Docs: {ECO.identity.docs_url}/index.html\n\n{folders}\n"
    ))
    _write(project / "unified_models_v2.yaml", f"kaggle_ref: kaggle://{OWNER}/lemgendizedx\n")
    assert not run_ecosystem_ssot(project)


def test_wrong_port_for_a_named_service_is_flagged(project: Path) -> None:
    """A port next to a service name must be that service's port."""
    _write(project / "guide.md", (
        f"Start {SERVICE.project} (port {OTHER_PORT}).\n"
        f"Ports {OTHER_PORT} and 1234 without a service name pass.\n"
    ))
    found = run_ecosystem_ssot(project)
    assert _types(found) == [WRONG_PORT]
    assert found[0].line_number == 1


def test_wrong_kaggle_owner_is_flagged_but_third_party_owners_pass(project: Path) -> None:
    """Our own datasets and the configured username must name the registry owner."""
    _write(project / "unified_data.yaml", (
        "kaggle_ref: kaggle://someoneelse/lemgendizedexample\n"
        "kaggle_ref: kaggle://someoneelse/public-dataset\n"
        "kaggle_username: someoneelse\n"
    ))
    assert _types(run_ecosystem_ssot(project)) == [WRONG_KAGGLE_OWNER, WRONG_KAGGLE_OWNER]


def test_docs_host_url_outside_the_base_is_flagged(project: Path) -> None:
    """A link to the docs host must stay under the docs base URL."""
    _write(project / "index.html", f"<a href=\"{ECO.identity.docs_url}-old/a.html\">x</a>\n")
    assert _types(run_ecosystem_ssot(project)) == [WRONG_DOCS_URL]


def test_legacy_folder_name_is_flagged(project: Path) -> None:
    """A renamed project folder is written under its current name."""
    _write(project / "guide.md", f"Models live in `{ASSET.legacy_folders[0]}`.\n")
    assert _types(run_ecosystem_ssot(project)) == [LEGACY_FOLDER]


def test_asset_project_is_checked_for_consistency_only(fake_workspace: Path) -> None:
    """Generated records in an asset project may hold the right owner and docs URL."""
    asset = fake_workspace / ASSET.folder
    _write(asset / "model" / "model_metadata.yaml", (
        f"owner: {OWNER}\ndocs: {ECO.identity.docs_url}/index.html\n"
    ))
    assert not run_ecosystem_ssot(asset)
    _write(asset / "model" / "model-metadata.json", '{"id": "someoneelse/lemgendizedx"}\n')
    _write(asset / "model" / "README.md", "kaggle://someoneelse/lemgendizedx\n")
    assert _types(run_ecosystem_ssot(asset)) == [WRONG_KAGGLE_OWNER]


def test_literal_in_a_test_outside_fixtures_is_flagged(project: Path) -> None:
    """Tests may hold literal facts only in fixture files."""
    _write(project / "tests" / "test_a.py", f'URL = "http://127.0.0.1:{PORT}"\n')
    assert _types(run_ecosystem_ssot(project)) == [PORT_LITERAL]


# --- ProjectHealth contract -------------------------------------------------------


def test_openapi_project_health_schema_matches_the_model() -> None:
    """``GET /api/projects`` is typed: the OpenAPI schema lists the model's fields."""
    keys = openapi_schema_keys(server.app.openapi(), "ProjectHealth")
    assert keys == {field.name for field in fields(ProjectHealth)}
