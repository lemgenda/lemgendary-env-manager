"""The shared Kaggle credential loader and the Kaggle CLI config writer.

Every token here is a fake; nothing reads or writes the real user home.
"""

import json
import os
import stat
import sys
from pathlib import Path

import pytest

from env_manager.kaggle_auth.cli_config import PRIVATE_FILE_MODE
from env_manager.kaggle_auth import (
    KaggleCliConfigWriter,
    KaggleCredential,
    credential_from_access_token_file,
    credential_from_env,
    credential_from_vault,
    load_kaggle_credential,
)

API_TOKEN = "KGAT_fake_token_0000"
OTHER_API_TOKEN = "KGAT_fake_token_1111"
LEGACY_KEY = "fake-legacy-key-0000"


def _record(value: str, username: str | None = "alice", default: bool = True) -> dict:
    return {
        "id": f"kaggle-{username}",
        "service": "kaggle",
        "username": username,
        "secret_value": value,
        "is_default": default,
    }


def test_repr_never_shows_the_token() -> None:
    """A credential can be logged without leaking its value."""
    cred = KaggleCredential(token=API_TOKEN, username="alice", source="vault")
    assert API_TOKEN not in repr(cred)


def test_vault_prefers_the_default_record() -> None:
    """The ``is_default`` Kaggle record wins over earlier ones."""
    records = [
        _record(OTHER_API_TOKEN, "bob", default=False),
        _record(API_TOKEN),
        {"service": "github", "secret_value": "fake-gh", "is_default": True},
    ]
    cred = credential_from_vault(records)
    assert cred is not None
    assert (cred.token, cred.username, cred.is_access_token) == (API_TOKEN, "alice", True)


def test_vault_ignores_empty_values() -> None:
    """A Kaggle record without a value is not a credential."""
    assert credential_from_vault([_record("")]) is None


def test_env_api_token_beats_legacy_pair() -> None:
    """``KAGGLE_API_TOKEN`` is used before ``KAGGLE_USERNAME``/``KAGGLE_KEY``."""
    env = {"KAGGLE_API_TOKEN": API_TOKEN, "KAGGLE_USERNAME": "alice", "KAGGLE_KEY": LEGACY_KEY}
    cred = credential_from_env(env)
    assert cred is not None and cred.token == API_TOKEN


def test_env_legacy_key_needs_a_username() -> None:
    """A legacy key alone is not usable; with a username it is."""
    assert credential_from_env({"KAGGLE_KEY": LEGACY_KEY}) is None
    cred = credential_from_env({"KAGGLE_KEY": LEGACY_KEY, "KAGGLE_USERNAME": "alice"})
    assert cred is not None and not cred.is_access_token


def test_access_token_file(tmp_path: Path) -> None:
    """The token file is read and stripped; the ``.txt`` variant also counts."""
    assert credential_from_access_token_file(tmp_path) is None
    (tmp_path / "access_token.txt").write_text(f"{API_TOKEN}\n", encoding="utf-8")
    cred = credential_from_access_token_file(tmp_path)
    assert cred is not None and cred.token == API_TOKEN


def test_load_order_is_vault_env_file(tmp_path: Path) -> None:
    """Vault first, then the environment, then the ``access_token`` file."""
    (tmp_path / "access_token").write_text(API_TOKEN, encoding="utf-8")
    env = {"KAGGLE_API_TOKEN": OTHER_API_TOKEN}
    vault_cred = load_kaggle_credential([_record(LEGACY_KEY)], env, tmp_path)
    env_cred = load_kaggle_credential([], env, tmp_path)
    file_cred = load_kaggle_credential([], {}, tmp_path)
    assert vault_cred is not None and vault_cred.source == "vault"
    assert env_cred is not None and env_cred.token == OTHER_API_TOKEN
    assert file_cred is not None and file_cred.source == "access_token"
    assert load_kaggle_credential([], {}, tmp_path / "missing") is None


def test_writer_puts_api_token_in_access_token(tmp_path: Path) -> None:
    """An API token is written to ``access_token`` and nowhere else."""
    report = KaggleCliConfigWriter(tmp_path).sync(
        KaggleCredential(token=API_TOKEN, username=None, source="vault")
    )
    assert report.written == ["~/.kaggle/access_token"]
    assert (tmp_path / "access_token").read_text(encoding="utf-8") == API_TOKEN
    assert not (tmp_path / "kaggle.json").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_writer_creates_owner_only_files(tmp_path: Path) -> None:
    """Credential files are created with mode 0600."""
    KaggleCliConfigWriter(tmp_path).sync(
        KaggleCredential(token=API_TOKEN, username=None, source="vault")
    )
    mode = stat.S_IMODE(os.stat(tmp_path / "access_token").st_mode)
    assert mode == PRIVATE_FILE_MODE


def test_writer_keeps_a_genuine_legacy_config(tmp_path: Path) -> None:
    """A ``kaggle.json`` with a legacy key is left alone when an API token is synced."""
    legacy = json.dumps({"username": "alice", "key": LEGACY_KEY})
    (tmp_path / "kaggle.json").write_text(legacy, encoding="utf-8")
    report = KaggleCliConfigWriter(tmp_path).sync(
        KaggleCredential(token=API_TOKEN, username="alice", source="vault")
    )
    assert not report.removed
    assert (tmp_path / "kaggle.json").read_text(encoding="utf-8") == legacy


def test_writer_without_credential_does_nothing(tmp_path: Path) -> None:
    """No credential means an empty report and no files."""
    config_dir = tmp_path / "kaggle"
    report = KaggleCliConfigWriter(config_dir).sync(None)
    assert not report.written and not config_dir.exists()
