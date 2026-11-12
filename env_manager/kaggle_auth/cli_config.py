"""Write a Kaggle credential where the official Kaggle CLI and SDK look for it.

* A ``KGAT_`` API token goes to ``<config_dir>/access_token``. A
  ``kaggle.json`` that holds an API token as its legacy ``key`` (which the
  CLI rejects with 401) is removed.
* A legacy key goes to ``<config_dir>/kaggle.json`` with its username; it is
  skipped when no username is known (usernames are never invented).

Both files are created owner-only (0600). The outcome is a
:class:`PropagationReport`; write failures are logged and reported, never
raised. Secret values are never logged or reported.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from env_manager._logging import get_logger
from env_manager.kaggle_auth.credentials import (
    ACCESS_TOKEN_FILE_NAMES,
    LEGACY_CONFIG_FILE_NAME,
    KaggleCredential,
    is_access_token,
)

_log = get_logger(__name__)

PRIVATE_FILE_MODE = 0o600
"""Owner read and write only, as the Kaggle CLI expects for credential files."""

ACCESS_TOKEN_LABEL = f"~/.kaggle/{ACCESS_TOKEN_FILE_NAMES[0]}"
LEGACY_CONFIG_LABEL = f"~/.kaggle/{LEGACY_CONFIG_FILE_NAME}"


class PropagationIssue(BaseModel):
    """A credential file that was skipped or could not be written."""

    path: str
    reason: str


class PropagationReport(BaseModel):
    """Outcome of propagating the default Kaggle credential to the CLI config."""

    written: list[str] = []
    removed: list[str] = []
    skipped: list[PropagationIssue] = []
    failed: list[PropagationIssue] = []


def write_private_file(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` readable and writable by the owner only."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_FILE_MODE)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.chmod(path, PRIVATE_FILE_MODE)


def holds_access_token_as_key(path: Path) -> bool:
    """True when the ``kaggle.json`` at ``path`` stores an API token as ``key``."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and is_access_token(str(data.get("key") or ""))


@dataclass
class KaggleCliConfigWriter:
    """Synchronizes one credential to the Kaggle config folder ``config_dir``."""

    config_dir: Path
    report: PropagationReport = field(default_factory=PropagationReport)

    def _write(self, name: str, label: str, content: str) -> None:
        try:
            write_private_file(self.config_dir / name, content)
        except OSError as exc:
            _log.error("Failed writing credential file %s: %s", label, exc)
            self.report.failed.append(PropagationIssue(path=label, reason=str(exc)))
            return
        self.report.written.append(label)

    def _skip(self, label: str, reason: str) -> None:
        _log.warning("Skipped credential file %s: %s", label, reason)
        self.report.skipped.append(PropagationIssue(path=label, reason=reason))

    def _remove_token_as_key(self) -> None:
        path = self.config_dir / LEGACY_CONFIG_FILE_NAME
        if not holds_access_token_as_key(path):
            return
        try:
            path.unlink()
        except OSError as exc:
            _log.error("Failed removing credential file %s: %s", LEGACY_CONFIG_LABEL, exc)
            self.report.failed.append(PropagationIssue(path=LEGACY_CONFIG_LABEL, reason=str(exc)))
            return
        self.report.removed.append(LEGACY_CONFIG_LABEL)

    def sync(self, credential: KaggleCredential | None) -> PropagationReport:
        """Write ``credential`` to the file its kind belongs in; return the report."""
        if credential is None:
            return self.report
        if credential.is_access_token:
            self._write(ACCESS_TOKEN_FILE_NAMES[0], ACCESS_TOKEN_LABEL, credential.token)
            self._remove_token_as_key()
        elif not credential.username:
            self._skip(LEGACY_CONFIG_LABEL, "default Kaggle credential has no username")
        else:
            pair = {"username": credential.username, "key": credential.token}
            content = json.dumps(pair, indent=2)
            self._write(LEGACY_CONFIG_FILE_NAME, LEGACY_CONFIG_LABEL, content)
        return self.report
