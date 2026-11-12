"""The ecosystem registry: schema, loader, derived inventories and ``GET /api/ecosystem``."""

import json
from collections.abc import Iterator
from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from env_manager import ecosystem as eco_module
from env_manager import server
from env_manager.ecosystem import (
    ECOSYSTEM_FILE_ENV,
    REPO_ECOSYSTEM_FILE,
    SCHEMA_FILE,
    EcosystemFileError,
    ecosystem,
    ecosystem_file,
    github_repo_url,
    kaggle_accelerator,
    kaggle_accelerators,
    kaggle_collaborators,
)
from env_manager.pipeline.steps import STEPS, TOTAL_STEPS
from env_manager.upgrade.models import upgrade_order
from env_manager.validation.domain.project_hygiene import asset_store_folders


def _raw() -> dict:
    return json.loads(REPO_ECOSYSTEM_FILE.read_text(encoding="utf-8"))


@pytest.fixture
def fresh_cache() -> Iterator[None]:
    """Drop parsed files before and after a test that points the loader elsewhere."""
    eco_module.clear_cache()
    yield
    eco_module.clear_cache()


def test_file_matches_schema() -> None:
    """``ecosystem.json`` is valid against its draft 2020-12 schema."""
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = [e.message for e in Draft202012Validator(schema).iter_errors(_raw())]
    assert errors == []


def test_schema_rejects_unmanaged_project_with_order() -> None:
    """An unmanaged project may not carry an upgrade order."""
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    raw = _raw()
    raw["projects"][-1]["upgrade_order"] = 9
    assert list(Draft202012Validator(schema).iter_errors(raw))


def test_default_file_is_the_repository_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the override the loader reads this repository's file."""
    monkeypatch.delenv(ECOSYSTEM_FILE_ENV, raising=False)
    assert ecosystem_file() == REPO_ECOSYSTEM_FILE


@pytest.mark.usefixtures("fresh_cache")
def test_env_var_overrides_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``LEMGENDARY_ECOSYSTEM_FILE`` points the loader at a fixture."""
    raw = _raw()
    raw["identity"]["kaggle_owner"] = "fixture-owner"
    fixture = tmp_path / "ecosystem.json"
    fixture.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv(ECOSYSTEM_FILE_ENV, str(fixture))
    assert ecosystem().identity.kaggle_owner == "fixture-owner"


@pytest.mark.usefixtures("fresh_cache")
def test_missing_file_names_the_expected_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing file fails loudly with the path it expected."""
    missing = tmp_path / "nowhere" / "ecosystem.json"
    monkeypatch.setenv(ECOSYSTEM_FILE_ENV, str(missing))
    with pytest.raises(EcosystemFileError, match="nowhere"):
        ecosystem()


@pytest.mark.usefixtures("fresh_cache")
def test_dangling_service_project_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A service must name a project listed in the file."""
    raw = _raw()
    raw["services"][0]["project"] = "missing-project"
    fixture = tmp_path / "ecosystem.json"
    fixture.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv(ECOSYSTEM_FILE_ENV, str(fixture))
    with pytest.raises(EcosystemFileError, match="missing-project"):
        ecosystem()


def test_services_resolve_by_alias() -> None:
    """Service lookup accepts the id and every alias, case-insensitively."""
    eco = ecosystem()
    for svc in eco.services:
        for name in (svc.id, *svc.aliases, svc.id.upper()):
            assert eco.service(name) == svc
    assert eco.service("unknown") is None


def test_ports_are_unique() -> None:
    """No two services share a port."""
    ports = [svc.port for svc in ecosystem().services]
    assert len(set(ports)) == len(ports)


def test_derived_inventories_follow_the_file() -> None:
    """Upgrade order, asset stores and pipeline steps all come from the file."""
    raw = _raw()
    managed = sorted(
        (p for p in raw["projects"] if p["managed"]), key=lambda p: p["upgrade_order"],
    )
    assert upgrade_order() == [p["folder"] for p in managed]
    assets = {p["folder"] for p in raw["projects"] if p["kind"] == "asset"}
    assert assets <= asset_store_folders()
    assert len(raw["pipeline_steps"]) == TOTAL_STEPS
    assert sorted(STEPS) == [s["number"] for s in raw["pipeline_steps"]]


def test_api_returns_the_file() -> None:
    """``GET /api/ecosystem`` returns the file content unchanged."""
    response = TestClient(server.app).get("/api/ecosystem")
    assert response.status_code == HTTPStatus.OK
    assert response.json() == _raw()


def test_kaggle_readers_follow_the_file() -> None:
    """Collaborators and accelerators are read from the ``kaggle`` section."""
    kaggle = _raw()["kaggle"]
    assert list(kaggle_collaborators()) == kaggle["collaborators"]
    assert [a.id for a in kaggle_accelerators()] == [a["id"] for a in kaggle["accelerators"]]
    assert kaggle_accelerator().id == kaggle["default_accelerator"]
    assert kaggle_accelerator("cpu").machine_shape is None
    with pytest.raises(KeyError, match="unknown-gpu"):
        kaggle_accelerator("unknown-gpu")


def test_no_retired_accelerator_is_offered() -> None:
    """P100 is retired and no offered shape is on the retired list."""
    retired = {s.lower() for s in ecosystem().kaggle.retired_machine_shapes}
    assert "nvidiateslap100" in retired
    offered = {(a.machine_shape or "").lower() for a in kaggle_accelerators()}
    assert not offered & retired


def test_github_repo_url_names_a_known_project() -> None:
    """Clone URLs are ``<github_url>/<folder>.git`` for listed projects only."""
    folder = ecosystem().self_folder()
    expected = f"{_raw()['identity']['github_url']}/{folder}.git"
    assert github_repo_url(folder) == expected
    with pytest.raises(KeyError):
        github_repo_url("not-a-project")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda k: k.update(default_accelerator="missing"), "default_accelerator"),
        (lambda k: k["accelerators"][1].update(machine_shape="NvidiaTeslaP100"), "retired"),
        (lambda k: k["accelerators"][0].update(machine_shape="NvidiaTeslaT4"), "cpu"),
    ],
)
@pytest.mark.usefixtures("fresh_cache")
def test_invalid_kaggle_settings_are_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change, message: str,
) -> None:
    """A dangling default, a retired shape or a CPU with a shape fails loudly."""
    raw = _raw()
    change(raw["kaggle"])
    fixture = tmp_path / "ecosystem.json"
    fixture.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv(ECOSYSTEM_FILE_ENV, str(fixture))
    with pytest.raises(EcosystemFileError, match=message):
        ecosystem()


def test_schema_rejects_cpu_with_machine_shape() -> None:
    """The schema ties a null ``machine_shape`` to the ``cpu`` kind."""
    schema = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    raw = _raw()
    raw["kaggle"]["accelerators"][0]["machine_shape"] = "NvidiaTeslaT4"
    assert list(Draft202012Validator(schema).iter_errors(raw))
