"""
LemGendary Environment Manager — ecosystem secrets vault.

The vault stores credentials in ``.secrets.yaml`` at the workspace root and
follows a write-only contract towards API clients:

* reads return only masked items (``has_value`` plus a short ``value_hint``);
* writes replace a stored value only when a new, non-empty value is sent; an
  omitted or empty ``secret_value`` keeps the value stored under the same id;
* listing has no side effects: when the vault file does not exist yet, the
  items are seeded in memory from legacy credential files and persisted only
  on the first save.

After a save the default Kaggle credential is propagated to the legacy files
that sibling repositories still read (``.kaggle_token``, ``.kaggle_users``
and ``~/.kaggle/kaggle.json``). Usernames are never invented, and every write
failure is logged and reported back to the caller.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from env_manager.ecosystem import ecosystem

_log = logging.getLogger("env_manager.secrets_vault")

SECRETS_FILE_NAME = ".secrets.yaml"
HINT_LENGTH = 4
MIN_LENGTH_FOR_HINT = 8
KAGGLE_SERVICE = "kaggle"
KAGGLE_JSON_LABEL = "~/.kaggle/kaggle.json"
DATASETS_REPO = ecosystem().require_service("dataset-compiler").project
TRAINING_REPO = ecosystem().require_service("training-suite").project

# Legacy files that hold one token each: (repository, file, id, service, label).
_SINGLE_TOKEN_SOURCES: tuple[tuple[str, str, str, str, str], ...] = (
    (TRAINING_REPO, ".GOOGLE_DRIVE", "gdrive-primary", "google_drive",
     "Google Drive Primary API Key"),
    (TRAINING_REPO, ".GITHUB_PAT", "github-primary", "github", "GitHub Personal Access Token"),
    (DATASETS_REPO, ".huggingface_token", "huggingface-primary", "huggingface",
     "Hugging Face Access Token"),
    (TRAINING_REPO, ".SATURN_PAT", "saturn-primary", "saturn_cloud", "Saturn Cloud Bearer Token"),
)

# Repositories that read the default Kaggle token from ``.kaggle_token``.
# Both repositories ignore that file in their own ``.gitignore``.
KAGGLE_TOKEN_REPOS: tuple[str, ...] = (DATASETS_REPO, TRAINING_REPO)


class SecretItem(BaseModel):
    """A secret as sent by a client. An empty ``secret_value`` keeps the stored value."""

    id: str
    service: str
    label: str
    username: str | None = None
    secret_value: str | None = None
    server: str | None = None
    is_default: bool = False
    created_at: str | None = None


class SecretsPayload(BaseModel):
    """Full list of secrets to store; items left out are removed from the vault."""

    secrets: list[SecretItem]


class MaskedSecretItem(BaseModel):
    """A secret as returned to clients: never contains the secret value."""

    id: str
    service: str
    label: str
    username: str | None = None
    server: str | None = None
    is_default: bool = False
    created_at: str | None = None
    has_value: bool
    value_hint: str


class PropagationIssue(BaseModel):
    """A legacy credential file that was skipped or could not be written."""

    path: str
    reason: str


class PropagationReport(BaseModel):
    """Outcome of propagating the default Kaggle credential to legacy files."""

    written: list[str] = []
    skipped: list[PropagationIssue] = []
    failed: list[PropagationIssue] = []


class SecretsListResponse(BaseModel):
    """Response of ``GET /api/secrets``."""

    status: str
    initialized: bool
    secrets: list[MaskedSecretItem]
    message: str | None = None


class SecretsSaveResponse(BaseModel):
    """Response of ``POST /api/secrets``."""

    status: str
    count: int
    message: str
    propagation: PropagationReport


class SecretsValidationError(ValueError):
    """The submitted secrets violate a vault rule (for example no Kaggle token)."""


class SecretsStorageError(RuntimeError):
    """The vault file could not be read or written."""


def value_hint(value: str) -> str:
    """Return the last characters of ``value``, or ``""`` when it is too short to hint."""
    if len(value) < MIN_LENGTH_FOR_HINT:
        return ""
    return value[-HINT_LENGTH:]


def mask_record(record: dict[str, Any]) -> MaskedSecretItem:
    """Convert a stored record into its masked, client-safe form."""
    value = str(record.get("secret_value") or "").strip()
    return MaskedSecretItem(
        id=str(record.get("id", "")),
        service=str(record.get("service", "")),
        label=str(record.get("label", "")),
        username=record.get("username"),
        server=record.get("server"),
        is_default=bool(record.get("is_default", False)),
        created_at=record.get("created_at"),
        has_value=bool(value),
        value_hint=value_hint(value),
    )


def _read_text(path: Path) -> str | None:
    """Read a legacy credential file; log and return ``None`` when it cannot be read."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        _log.warning("Cannot read legacy credential file %s: %s", path.name, exc)
        return None


def _seed_kaggle_users(root: Path) -> list[dict[str, Any]]:
    content = _read_text(root / TRAINING_REPO / ".kaggle_users")
    if not content:
        return []
    seeds: list[dict[str, Any]] = []
    for entry in content.split(";"):
        parts = dict(
            p.strip().split("=", 1) for p in entry.split(",") if "=" in p
        )
        username = parts.get("KAGGLE_USERNAME", "").strip()
        token = parts.get("KAGGLE_API_TOKEN", "").strip()
        if username and token:
            seeds.append({
                "id": f"kaggle-{username}",
                "service": KAGGLE_SERVICE,
                "label": f"Kaggle ({username})",
                "username": username,
                "secret_value": token,
                "server": None,
                # The first line of .kaggle_users is the default account.
                "is_default": not seeds,
            })
    return seeds


def _seed_single_tokens(root: Path) -> list[dict[str, Any]]:
    seeds: list[dict[str, Any]] = []
    for repo, file_name, item_id, service, label in _SINGLE_TOKEN_SOURCES:
        path = root / repo / file_name
        if not path.exists():
            continue
        token = _read_text(path)
        if token:
            seeds.append({
                "id": item_id,
                "service": service,
                "label": label,
                "username": None,
                "secret_value": token,
                "server": None,
                "is_default": True,
            })
    return seeds


def _seed_mt5(root: Path) -> list[dict[str, Any]]:
    path = root / DATASETS_REPO / ".mt5_credentials"
    if not path.exists():
        return []
    content = _read_text(path)
    if not content:
        return []
    fields: dict[str, str] = {}
    for line in content.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip().lower()] = value.strip()
    username = fields.get("user", "")
    if not username:
        return []
    return [{
        "id": f"mt5-{username}",
        "service": "metatrader5",
        "label": f"MT5 Account ({username})",
        "username": username,
        "secret_value": fields.get("pass", ""),
        "server": fields.get("server") or None,
        "is_default": True,
    }]


def seed_from_legacy_files(root: Path) -> list[dict[str, Any]]:
    """Build initial vault records from legacy credential files (read-only)."""
    seeds: list[dict[str, Any]] = []
    if (root / TRAINING_REPO / ".kaggle_users").exists():
        seeds.extend(_seed_kaggle_users(root))
    seeds.extend(_seed_single_tokens(root))
    seeds.extend(_seed_mt5(root))
    return seeds


@dataclass
class _Propagator:
    """Writes the default Kaggle credential to legacy files and records the outcome."""

    root: Path
    kaggle_config_dir: Path
    report: PropagationReport = field(default_factory=PropagationReport)

    def _label(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def _write(self, path: Path, label: str, content: str) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            _log.error("Failed writing legacy credential file %s: %s", label, exc)
            self.report.failed.append(PropagationIssue(path=label, reason=str(exc)))
            return
        self.report.written.append(label)

    def _skip(self, label: str, reason: str) -> None:
        _log.warning("Skipped legacy credential file %s: %s", label, reason)
        self.report.skipped.append(PropagationIssue(path=label, reason=reason))

    def _write_users_file(self, ordered: list[dict[str, Any]]) -> None:
        """Write every usable Kaggle credential, default first, to the training suite."""
        users_path = self.root / TRAINING_REPO / ".kaggle_users"
        label = self._label(users_path)
        if not users_path.parent.is_dir():
            self._skip(label, "repository not present in workspace")
            return
        lines = []
        for record in ordered:
            username = str(record.get("username") or "").strip()
            if not username:
                self._skip(f"{label} ({record.get('id')})", "Kaggle credential has no username")
                continue
            token_value = str(record["secret_value"]).strip()
            lines.append(f"KAGGLE_USERNAME={username}, KAGGLE_API_TOKEN={token_value};")
        if lines:
            self._write(users_path, label, "\n".join(lines) + "\n")

    def run(self, kaggle_records: list[dict[str, Any]]) -> PropagationReport:
        """Write the default Kaggle credential to every legacy file and return the report."""
        usable = [r for r in kaggle_records if str(r.get("secret_value") or "").strip()]
        if not usable:
            return self.report
        default = next((r for r in usable if r.get("is_default")), usable[0])
        ordered = [default] + [r for r in usable if r is not default]
        token = str(default["secret_value"]).strip()

        for repo in KAGGLE_TOKEN_REPOS:
            repo_dir = self.root / repo
            target = repo_dir / ".kaggle_token"
            if not repo_dir.is_dir():
                self._skip(self._label(target), "repository not present in workspace")
                continue
            self._write(target, self._label(target), token)

        self._write_users_file(ordered)

        default_username = str(default.get("username") or "").strip()
        if not default_username:
            self._skip(KAGGLE_JSON_LABEL, "default Kaggle credential has no username")
        else:
            content = json.dumps({"username": default_username, "key": token}, indent=2)
            self._write(self.kaggle_config_dir / "kaggle.json", KAGGLE_JSON_LABEL, content)
        return self.report


class SecretsVault:
    """Masked, write-only access to the workspace secrets file."""

    def __init__(
        self,
        workspace_root: Path,
        kaggle_config_dir: Path,
        seeder: Callable[[Path], list[dict[str, Any]]] = seed_from_legacy_files,
    ) -> None:
        self.workspace_root = workspace_root
        self.kaggle_config_dir = kaggle_config_dir
        self._seeder = seeder

    @property
    def secrets_file(self) -> Path:
        """Path of the vault file in the workspace root."""
        return self.workspace_root / SECRETS_FILE_NAME

    def is_initialized(self) -> bool:
        """True once the vault file exists."""
        return self.secrets_file.exists()

    def load_records(self) -> list[dict[str, Any]]:
        """Return stored records, or in-memory legacy seeds when the vault is new."""
        if not self.is_initialized():
            return self._seeder(self.workspace_root)
        try:
            with open(self.secrets_file, encoding="utf-8") as handle:
                data = yaml.safe_load(handle) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise SecretsStorageError(f"Failed loading {SECRETS_FILE_NAME}: {exc}") from exc
        records = data.get("secrets", []) if isinstance(data, dict) else []
        return [r for r in records if isinstance(r, dict)]

    def list_masked(self) -> list[MaskedSecretItem]:
        """Return every stored secret with its value masked."""
        return [mask_record(r) for r in self.load_records()]

    def _merge(self, items: list[SecretItem]) -> list[dict[str, Any]]:
        stored = {str(r.get("id")): r for r in self.load_records()}
        merged: list[dict[str, Any]] = []
        for item in items:
            record = item.model_dump()
            new_value = (item.secret_value or "").strip()
            if new_value:
                record["secret_value"] = new_value
            else:
                previous = stored.get(item.id, {})
                record["secret_value"] = str(previous.get("secret_value") or "").strip()
            merged.append(record)
        return merged

    def _write_atomically(self, records: list[dict[str, Any]]) -> None:
        target = self.secrets_file
        tmp = target.with_name(target.name + ".tmp")
        try:
            if target.exists():
                backup = target.with_name(target.name + ".bak")
                backup.write_bytes(target.read_bytes())
            with open(tmp, "w", encoding="utf-8") as handle:
                yaml.safe_dump(
                    {"secrets": records}, handle, default_flow_style=False, sort_keys=False
                )
            os.replace(tmp, target)
        except OSError as exc:
            raise SecretsStorageError(f"Failed writing {SECRETS_FILE_NAME}: {exc}") from exc

    def save(self, items: list[SecretItem]) -> SecretsSaveResponse:
        """Merge, validate, persist and propagate. Never returns secret values."""
        records = self._merge(items)
        kaggle = [r for r in records if str(r.get("service", "")).lower() == KAGGLE_SERVICE]
        if not any(r["secret_value"] for r in kaggle):
            raise SecretsValidationError(
                "Validation failed: Kaggle API token is mandatory across the LemGendary Ecosystem."
            )
        self._write_atomically(records)
        report = _Propagator(self.workspace_root, self.kaggle_config_dir).run(kaggle)
        if report.failed:
            message = (
                "Secrets saved, but some legacy credential files could not be written."
            )
        else:
            message = "Secrets securely persisted and propagated across repositories."
        return SecretsSaveResponse(
            status="success", count=len(records), message=message, propagation=report
        )
