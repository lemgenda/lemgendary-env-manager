"""Load the ecosystem registry: services, projects, pipeline steps, identity, Kaggle.

``ecosystem/ecosystem.json`` at the root of this repository is the single
source of truth for these facts; every module that needs a port, a project
folder, a pipeline step or the documentation URL reads it through
:func:`ecosystem`. The file is looked up in this order:

1. ``LEMGENDARY_ECOSYSTEM_FILE`` (tests point it at a fixture);
2. this repository's own ``ecosystem/ecosystem.json``;
3. the copy installed with the wheel under ``<prefix>/share``.

A missing or invalid file raises :class:`EcosystemFileError` naming the path.

Readers for the cross-project Kaggle and GitHub settings:
:func:`kaggle_collaborators`, :func:`kaggle_accelerators`,
:func:`kaggle_accelerator` and :func:`github_repo_url`.
"""

import json
import os
import sys
from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from env_manager.workspace import ENV_MANAGER_DIR

ECOSYSTEM_FILE_ENV = "LEMGENDARY_ECOSYSTEM_FILE"
"""Environment variable that overrides the ecosystem file location."""

REPO_ECOSYSTEM_FILE = ENV_MANAGER_DIR / "ecosystem" / "ecosystem.json"
"""The authoritative file in this repository's checkout."""

SCHEMA_FILE = REPO_ECOSYSTEM_FILE.with_name("ecosystem.schema.json")
"""JSON Schema (draft 2020-12) the ecosystem file must satisfy."""

INSTALLED_ECOSYSTEM_FILE = (
    Path(sys.prefix) / "share" / "lemgendary-env-manager" / "ecosystem" / "ecosystem.json"
)
"""Where a wheel install places the file (``tool.setuptools.data-files``)."""


SELF_SERVICE_ID = "env-manager"
"""The service this package runs; it is never spawned or stopped like a sidecar."""


class EcosystemFileError(RuntimeError):
    """The ecosystem file is missing, unreadable or invalid."""


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Identity(_Record):
    """Public names and addresses shared by every project."""

    docs_url: str
    kaggle_owner: str
    bind_host: str
    github_url: str
    """GitHub owner URL; a project's clone URL is ``<github_url>/<folder>.git``."""


class Service(_Record):
    """An HTTP sidecar: where it listens and how it is started."""

    id: str
    aliases: tuple[str, ...]
    project: str
    port: int
    health_path: str
    liveness_path: str
    websocket_path: str
    app_import: str
    log_dir: str


class Project(_Record):
    """A workspace folder and how the environment manager treats it."""

    folder: str
    display_name: str
    kind: Literal["python", "node", "asset"]
    managed: bool
    validated: bool
    upgrade_order: int | None
    git_hooked: bool
    requirements_manifest: str | None
    npm_manifest: str | None
    legacy_folders: tuple[str, ...]

    @property
    def is_node(self) -> bool:
        """True for an npm project."""
        return self.kind == "node"


class PipelineStep(_Record):
    """One step of the Smart Clean Install Pipeline."""

    number: int
    id: str
    name: str
    short_description: str
    long_description: str


class KaggleAccelerator(_Record):
    """A Kaggle session type: what a notebook or kernel push asks for.

    ``machine_shape`` is the value for the kernel metadata ``machine_shape``
    and the ``acc`` argument of ``kernels_push`` (``None`` for CPU).
    """

    id: str
    label: str
    kind: Literal["cpu", "gpu", "tpu"]
    machine_shape: str | None

    @model_validator(mode="after")
    def _shape_matches_kind(self) -> "KaggleAccelerator":
        if (self.kind == "cpu") != (self.machine_shape is None):
            raise ValueError(f"accelerator {self.id}: machine_shape is null exactly for cpu")
        return self


class KaggleSettings(_Record):
    """Kaggle facts shared by every project."""

    collaborators: tuple[str, ...]
    """Kaggle usernames granted the editor role on pushed models."""
    default_accelerator: str
    accelerators: tuple[KaggleAccelerator, ...]
    retired_machine_shapes: tuple[str, ...]
    """Shapes Kaggle no longer offers (compared case-insensitively)."""

    @model_validator(mode="after")
    def _check_accelerators(self) -> "KaggleSettings":
        ids = [a.id for a in self.accelerators]
        if len(set(ids)) != len(ids):
            raise ValueError("kaggle accelerator ids must be unique")
        if self.default_accelerator not in ids:
            raise ValueError(f"default_accelerator {self.default_accelerator} is not listed")
        retired = {shape.lower() for shape in self.retired_machine_shapes}
        for acc in self.accelerators:
            if acc.machine_shape and acc.machine_shape.lower() in retired:
                raise ValueError(f"accelerator {acc.id} uses retired {acc.machine_shape}")
        return self

    def accelerator(self, accelerator_id: str) -> KaggleAccelerator:
        """Return the accelerator ``accelerator_id``; raise :class:`KeyError` when unknown."""
        for acc in self.accelerators:
            if acc.id == accelerator_id:
                return acc
        raise KeyError(f"Unknown Kaggle accelerator '{accelerator_id}' in {ecosystem_file()}")


class Ecosystem(_Record):
    """The whole ecosystem file."""

    schema_ref: str = Field(alias="$schema")
    schema_version: Literal[1]
    identity: Identity
    services: tuple[Service, ...]
    projects: tuple[Project, ...]
    pipeline_steps: tuple[PipelineStep, ...]
    kaggle: KaggleSettings

    @model_validator(mode="after")
    def _check_references(self) -> "Ecosystem":
        folders = [p.folder for p in self.projects]
        if len(set(folders)) != len(folders):
            raise ValueError("project folders must be unique")
        for svc in self.services:
            if svc.project not in folders:
                raise ValueError(f"service {svc.id} names unknown project {svc.project}")
        numbers = [s.number for s in self.pipeline_steps]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError("pipeline step numbers must run 1..N in order")
        for proj in self.projects:
            if proj.managed != (proj.upgrade_order is not None):
                raise ValueError(f"{proj.folder}: upgrade_order is set exactly when managed")
        return self

    def service(self, service_id: str) -> Service | None:
        """Return the service whose id or alias is ``service_id`` (case-insensitive)."""
        wanted = service_id.strip().lower()
        for svc in self.services:
            if wanted == svc.id or wanted in svc.aliases:
                return svc
        return None

    def require_service(self, service_id: str) -> Service:
        """Return the service ``service_id``; raise :class:`KeyError` when unknown."""
        svc = self.service(service_id)
        if svc is None:
            raise KeyError(f"Unknown service '{service_id}' in {ecosystem_file()}")
        return svc

    def project(self, folder: str) -> Project:
        """Return the project in ``folder``; raise :class:`KeyError` when unknown."""
        for proj in self.projects:
            if proj.folder == folder:
                return proj
        raise KeyError(f"Unknown project '{folder}' in {ecosystem_file()}")

    def self_folder(self) -> str:
        """Workspace folder of this package's own project (the env-manager service)."""
        return self.require_service(SELF_SERVICE_ID).project

    def display_name(self, folder: str) -> str:
        """Canonical name of the project in ``folder``."""
        return self.project(folder).display_name

    def service_url(self, service: Service, path: str = "") -> str:
        """Local HTTP URL of ``service`` on the bind host, with ``path`` appended."""
        return f"http://{self.identity.bind_host}:{service.port}{path}"

    def managed_projects(self) -> list[Project]:
        """Projects the pipeline installs and upgrades, in upgrade order."""
        managed = [p for p in self.projects if p.upgrade_order is not None]
        return sorted(managed, key=lambda p: p.upgrade_order or 0)

    def validated_projects(self) -> list[Project]:
        """Projects that ``lem-env validate`` checks, in file order."""
        return [p for p in self.projects if p.validated]

    def git_hooked_projects(self) -> list[Project]:
        """Projects that receive the standard pre-commit hook, in file order."""
        return [p for p in self.projects if p.git_hooked]

    def step(self, number: int) -> PipelineStep:
        """Return pipeline step ``number``."""
        return self.pipeline_steps[number - 1]

    def github_repo_url(self, folder: str) -> str:
        """Git clone URL of the project in ``folder`` (``KeyError`` when unknown)."""
        project = self.project(folder)
        return f"{self.identity.github_url.rstrip('/')}/{project.folder}.git"


def ecosystem_file() -> Path:
    """Path of the ecosystem file that :func:`ecosystem` reads."""
    override = os.environ.get(ECOSYSTEM_FILE_ENV)
    if override:
        return Path(override)
    if not REPO_ECOSYSTEM_FILE.is_file() and INSTALLED_ECOSYSTEM_FILE.is_file():
        return INSTALLED_ECOSYSTEM_FILE
    return REPO_ECOSYSTEM_FILE


@cache
def _load(path: Path) -> Ecosystem:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EcosystemFileError(
            f"Ecosystem file not found: expected {path} "
            f"(set {ECOSYSTEM_FILE_ENV} to override)"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise EcosystemFileError(f"Cannot read ecosystem file {path}: {exc}") from exc
    try:
        return Ecosystem.model_validate(raw)
    except ValidationError as exc:
        raise EcosystemFileError(f"Invalid ecosystem file {path}: {exc}") from exc


def ecosystem() -> Ecosystem:
    """Return the parsed ecosystem file, cached per path."""
    return _load(ecosystem_file())


def clear_cache() -> None:
    """Forget every parsed file (tests that rewrite a fixture call this)."""
    _load.cache_clear()


def kaggle_collaborators() -> tuple[str, ...]:
    """Kaggle usernames that get the editor role on pushed models."""
    return ecosystem().kaggle.collaborators


def kaggle_accelerators() -> tuple[KaggleAccelerator, ...]:
    """Every Kaggle session type, in the order a picker should show them."""
    return ecosystem().kaggle.accelerators


def kaggle_accelerator(accelerator_id: str | None = None) -> KaggleAccelerator:
    """The accelerator ``accelerator_id``, or the default one when ``None``."""
    settings = ecosystem().kaggle
    return settings.accelerator(accelerator_id or settings.default_accelerator)


def github_repo_url(folder: str) -> str:
    """Git clone URL of the project in ``folder``."""
    return ecosystem().github_repo_url(folder)
