"""The PowerShell launcher passes every CLI command through (REVIEW_REPORT D-19)."""

import re
from pathlib import Path

from typer.core import TyperGroup
from typer.main import get_command

from env_manager.cli import app

LAUNCHER = Path(__file__).resolve().parent.parent / "lemgendary_env_manager.ps1"


def _launcher_text() -> str:
    return LAUNCHER.read_text(encoding="utf-8")


def test_validate_set_lists_every_cli_command() -> None:
    """Each ``lem-env`` command is accepted by the launcher's ValidateSet."""
    match = re.search(r"ValidateSet\(([^)]*)\)", _launcher_text())
    assert match is not None
    accepted = set(re.findall(r'"([^"]+)"', match.group(1))) - {"menu"}
    group = get_command(app)
    assert isinstance(group, TyperGroup)
    commands = set(group.commands)
    assert accepted == commands


def test_launcher_does_not_discard_exit_codes_or_errors() -> None:
    """No ``exit 0`` after a CLI call and no ``SilentlyContinue``."""
    text = _launcher_text()
    assert "SilentlyContinue" not in text
    assert "exit $LASTEXITCODE" in text


def test_launcher_reads_the_sidecar_address_from_the_ecosystem_file() -> None:
    """The menu shows the address from ``ecosystem.json``, not a literal port."""
    text = _launcher_text()
    assert '"ecosystem.json"' in text
    assert "$EnvService.port" in text
    assert "http://$EnvAddress" in text
