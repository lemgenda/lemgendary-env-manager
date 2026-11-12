"""The one Kaggle credential loader of the ecosystem.

Kaggle issues two kinds of credential and the official tooling (kaggle 2.x
with kagglesdk) reads them from different places:

* an API token (``KGAT_`` prefix) from ``KAGGLE_API_TOKEN`` or the file
  ``~/.kaggle/access_token``; the account name is introspected from it;
* a legacy username and key pair from ``KAGGLE_USERNAME``/``KAGGLE_KEY`` or
  ``~/.kaggle/kaggle.json``.

:func:`load_kaggle_credential` resolves the credential in the order the
ecosystem agreed on: the secrets vault, then the environment, then the
``access_token`` file. Each source has its own small reader so callers that
need only one (the vault propagation) use the same rule. Nothing here
mutates ``os.environ`` or writes files.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

KAGGLE_SERVICE = "kaggle"
"""Vault ``service`` value of a Kaggle credential."""

ACCESS_TOKEN_PREFIX = "KGAT_"
"""Prefix of a Kaggle API token (as opposed to a legacy key)."""

ACCESS_TOKEN_ENV = "KAGGLE_API_TOKEN"
USERNAME_ENV = "KAGGLE_USERNAME"
LEGACY_KEY_ENV = "KAGGLE_KEY"

ACCESS_TOKEN_FILE_NAMES = ("access_token", "access_token.txt")
"""Token files kagglesdk reads in the Kaggle config folder, in order."""

LEGACY_CONFIG_FILE_NAME = "kaggle.json"
"""Legacy username and key file in the Kaggle config folder."""

SOURCE_VAULT = "vault"
SOURCE_ENV = "env"
SOURCE_ACCESS_TOKEN_FILE = "access_token"


@dataclass(frozen=True)
class KaggleCredential:
    """A resolved Kaggle credential. The token never appears in ``repr``."""

    token: str = field(repr=False)
    username: str | None
    source: str

    @property
    def is_access_token(self) -> bool:
        """True for a ``KGAT_`` API token, False for a legacy key."""
        return is_access_token(self.token)


def is_access_token(token: str) -> bool:
    """True when ``token`` is a Kaggle API token (``KGAT_`` prefix)."""
    return token.strip().startswith(ACCESS_TOKEN_PREFIX)


def _text(value: Any) -> str:
    return str(value or "").strip()


def credential_from_vault(records: Iterable[Mapping[str, Any]]) -> KaggleCredential | None:
    """The default Kaggle credential among vault ``records``, or ``None``.

    Only Kaggle records with a value count. The record marked ``is_default``
    wins; otherwise the first usable record.
    """
    usable = [
        r for r in records
        if _text(r.get("service")).lower() == KAGGLE_SERVICE and _text(r.get("secret_value"))
    ]
    if not usable:
        return None
    chosen = next((r for r in usable if r.get("is_default")), usable[0])
    return KaggleCredential(
        token=_text(chosen.get("secret_value")),
        username=_text(chosen.get("username")) or None,
        source=SOURCE_VAULT,
    )


def credential_from_env(environ: Mapping[str, str]) -> KaggleCredential | None:
    """The credential in ``KAGGLE_API_TOKEN``, else ``KAGGLE_USERNAME``/``KAGGLE_KEY``."""
    username = _text(environ.get(USERNAME_ENV)) or None
    token = _text(environ.get(ACCESS_TOKEN_ENV))
    if token:
        return KaggleCredential(token=token, username=username, source=SOURCE_ENV)
    key = _text(environ.get(LEGACY_KEY_ENV))
    if key and (username or is_access_token(key)):
        return KaggleCredential(token=key, username=username, source=SOURCE_ENV)
    return None


def credential_from_access_token_file(config_dir: Path) -> KaggleCredential | None:
    """The token in ``<config_dir>/access_token`` (or ``access_token.txt``), or ``None``."""
    for name in ACCESS_TOKEN_FILE_NAMES:
        path = config_dir / name
        try:
            token = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if token:
            return KaggleCredential(token=token, username=None, source=SOURCE_ACCESS_TOKEN_FILE)
    return None


def load_kaggle_credential(
    vault_records: Iterable[Mapping[str, Any]],
    environ: Mapping[str, str],
    config_dir: Path,
) -> KaggleCredential | None:
    """Resolve the Kaggle credential: vault, then environment, then ``access_token``."""
    return (
        credential_from_vault(vault_records)
        or credential_from_env(environ)
        or credential_from_access_token_file(config_dir)
    )
