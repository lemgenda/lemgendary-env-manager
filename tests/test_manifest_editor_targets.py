"""The manifest editor edits the central SSOT files and syncs after a save (audit GUI-3)."""

import asyncio
from pathlib import Path
from unittest.mock import patch

from env_manager.api import routes_manifests
from env_manager.api.routes_manifests import ManifestSaveRequest, manifest_targets
from env_manager.ecosystem import ecosystem


def test_dependency_manifests_point_at_the_central_files(fake_workspace: Path) -> None:
    """Python and npm manifests resolve into ``<env-manager>/requirements/``."""
    central = fake_workspace / ecosystem().self_folder() / "requirements"
    dependency_targets = [t for t in manifest_targets() if t.central]
    assert dependency_targets
    for target in dependency_targets:
        assert target.path_in(fake_workspace).parent == central, target.name
    assert all(not t.central for t in manifest_targets() if t.format == "yaml")


def test_saving_a_central_manifest_syncs_the_projects(fake_workspace: Path) -> None:
    """A central save is followed by ``sync_all_manifests``; its results are returned."""
    target = next(t for t in manifest_targets() if t.central and t.format == "text")
    request = ManifestSaveRequest(name=target.name, content="rich>=13.0.0\n")
    with patch.object(routes_manifests, "sync_all_manifests",
                      return_value={target.project: (True, "synced")}) as sync:
        response = asyncio.run(routes_manifests.save_manifest_content(request))
    sync.assert_called_once_with()
    assert response["sync"] == {target.project: {"success": True, "message": "synced"}}
    assert target.path_in(fake_workspace).read_text(encoding="utf-8") == "rich>=13.0.0\n"


def test_saving_a_service_registry_does_not_sync(fake_workspace: Path) -> None:
    """Project-owned YAML registries are written in place with no manifest sync."""
    target = next(t for t in manifest_targets() if not t.central)
    target.path_in(fake_workspace).parent.mkdir(parents=True, exist_ok=True)
    request = ManifestSaveRequest(name=target.name, content="key: value\n")
    with patch.object(routes_manifests, "sync_all_manifests") as sync:
        response = asyncio.run(routes_manifests.save_manifest_content(request))
    sync.assert_not_called()
    assert "sync" not in response
