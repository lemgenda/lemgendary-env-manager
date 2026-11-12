"""Venv-to-manifest sync keeps tags, extras, floors and markers right (audit 4.1.4)."""

import json
from pathlib import Path
from unittest.mock import patch

from env_manager.ecosystem import ecosystem
from env_manager.upgrade import manifest_sync
from env_manager.upgrade.manifest_sync import public_version, synced_line
from env_manager.upgrade.npm_manifest_sync import (
    merge_dependency_sections,
    sync_npm_manifest_from_project,
)

INSTALLED = {
    "torch": "2.16.0+cu134",
    "uvicorn": "0.60.0",
    "ruff": "0.15.0",
    "bitsandbytes": "0.51.0",
    "pydantic": "2.20.0",
    "rich": "15.1.0",
}


def test_local_build_tag_is_stripped() -> None:
    assert public_version("2.16.0+cu134") == "2.16.0"
    assert synced_line("torch==2.15.0", INSTALLED) == "torch==2.16.0"


def test_extras_are_preserved() -> None:
    assert synced_line("uvicorn[standard]==0.54.0", INSTALLED) == "uvicorn[standard]==0.60.0"


def test_floor_specifiers_are_left_as_written() -> None:
    assert synced_line("rich>=13.0.0", INSTALLED) == "rich>=13.0.0"
    assert synced_line("rich>=13,<16", INSTALLED) == "rich>=13,<16"


def test_marker_text_is_kept_verbatim() -> None:
    line = "bitsandbytes==0.50.2; sys_platform != 'darwin'"
    assert synced_line(line, INSTALLED) == "bitsandbytes==0.51.0; sys_platform != 'darwin'"


def test_passthrough_lines() -> None:
    for line in ("", "# comment", "--extra-index-url https://example.invalid/simple",
                 "-e .", "pydantic==2.14.1", "unknown-pkg==1.0", "not a requirement ((("):
        assert synced_line(line, INSTALLED) == line


def test_unchanged_pin_keeps_original_formatting() -> None:
    line = "rich == 15.1.0"
    assert synced_line(line, INSTALLED) == line


def test_npm_dependency_sections_flow_back_but_version_stays() -> None:
    central = {"name": "gui", "version": "1.0.0", "dependencies": {"react": "^18.0.0"},
               "scripts": {"build": "vite build"}}
    project = {"name": "gui", "version": "9.9.9", "dependencies": {"react": "^18.3.1"},
               "devDependencies": {"vite": "^6.0.0"}}
    merged = merge_dependency_sections(central, project)
    assert merged["version"] == "1.0.0"
    assert merged["dependencies"] == {"react": "^18.3.1"}
    assert merged["devDependencies"] == {"vite": "^6.0.0"}
    assert merged["scripts"] == central["scripts"]
    assert list(merged)[:4] == ["name", "version", "dependencies", "scripts"]


def test_npm_sync_writes_central_manifest(tmp_path: Path) -> None:
    project_pkg = tmp_path / "package.json"
    central = tmp_path / "central.package.json"
    project_pkg.write_text(json.dumps({"dependencies": {"a": "^2.0.0"}}), encoding="utf-8")
    central.write_text(json.dumps({"version": "1.0.0", "dependencies": {"a": "^1.0.0"}}),
                       encoding="utf-8")
    ok, _msg = sync_npm_manifest_from_project(project_pkg, central)
    assert ok
    assert json.loads(central.read_text(encoding="utf-8")) == {
        "version": "1.0.0", "dependencies": {"a": "^2.0.0"},
    }
    ok_again, msg = sync_npm_manifest_from_project(project_pkg, central)
    assert ok_again
    assert "already matches" in msg


def test_sync_is_scoped_to_the_requested_project(fake_workspace: Path) -> None:
    """With ``project`` set only that project's manifests are touched."""
    target = next(p for p in ecosystem().projects if p.requirements_manifest is not None)
    with patch.object(manifest_sync, "is_venv_valid", return_value=True), \
            patch.object(manifest_sync, "get_installed_packages", return_value={"rich": "15.1.0"}):
        results = manifest_sync.sync_manifests_from_venvs(fake_workspace, project=target.folder)
    assert set(results) <= {target.folder, f"{target.folder} (npm)"}
    assert results[target.folder][0]
    synced = fake_workspace / target.folder / "requirements.txt"
    assert synced.read_text(encoding="utf-8") == "rich>=13.0.0\n"
