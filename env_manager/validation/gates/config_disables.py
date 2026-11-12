"""Rule disables in linter config files, forbidden by ROBOTS.md.

A config file that switches a check off is a suppression just like an
inline directive, only wider. This module reads the config files the
ecosystem's linters use and reports every rule they disable:

- Ruff (``pyproject.toml``, ``ruff.toml``, ``.ruff.toml``): ``ignore``,
  ``extend-ignore`` and ``per-file-ignores``.
- Pylint (``pyproject.toml``, ``.pylintrc``, ``pylintrc``, ``setup.cfg``,
  ``tox.ini``): ``disable``.
- Pyright (``pyproject.toml``, ``pyrightconfig.json``): any ``report*``
  rule set to ``false`` or ``"none"``.
- markdownlint (``.markdownlint.json``): any rule set to ``false``.
- ESLint (``eslint.config.*``, ``.eslintrc*``): any rule set to ``"off"``
  or ``0``.
"""

import configparser
import json
import re
import tomllib
from pathlib import Path
from typing import Any

from env_manager._logging import get_logger
from env_manager.validation.models import FileValidationViolation, read_error

_log = get_logger(__name__)

CONFIG_DISABLE = "suppression"
"""Violation type, shared with the inline suppression scan."""

RUFF_FILES = frozenset({"pyproject.toml", "ruff.toml", ".ruff.toml"})
PYLINT_INI_FILES = frozenset({".pylintrc", "pylintrc", "setup.cfg", "tox.ini"})
PYRIGHT_JSON_FILES = frozenset({"pyrightconfig.json"})
MARKDOWNLINT_FILES = frozenset({".markdownlint.json"})
ESLINT_PREFIXES = ("eslint.config.", ".eslintrc")

_RUFF_DISABLE_KEYS = ("ignore", "extend-ignore", "per-file-ignores")
_PYRIGHT_OFF = (False, "none")
_ESLINT_OFF = re.compile(r"""["']?[@\w/-]+["']?\s*:\s*(?:["']off["']|0)\s*[,}\]\n]""")


def _finding(path: Path, line: int, text: str) -> FileValidationViolation:
    return FileValidationViolation(
        file_path=str(path),
        line_number=line,
        violation_type=CONFIG_DISABLE,
        message=f"Linter rule disabled in config: {text.strip()[:120]}",
    )


def _line_of(text: str, needle: str) -> int:
    """1-based line of the first ``needle`` in ``text``, or 0 when absent."""
    index = text.find(needle)
    return text.count("\n", 0, index) + 1 if index >= 0 else 0


def _ruff_disables(table: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for section in (table, table.get("lint", {})):
        for key in _RUFF_DISABLE_KEYS:
            if section.get(key):
                found.append(f"{key} = {section[key]}")
    return found


def _pyright_disables(table: dict[str, Any]) -> list[str]:
    return [
        f"{key} = {value}" for key, value in table.items()
        if key.startswith("report") and value in _PYRIGHT_OFF
    ]


def _pylint_disables(table: dict[str, Any]) -> list[str]:
    found: list[str] = []
    for section in table.values():
        if isinstance(section, dict) and section.get("disable"):
            found.append(f"disable = {section['disable']}")
    return found


def scan_toml(path: Path, text: str) -> list[FileValidationViolation]:
    """Ruff, Pylint and Pyright disables in a TOML config."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        _log.debug("Config disable scan skips unparsable %s: %s", path, exc)
        return []
    tool = data.get("tool", {}) if path.name == "pyproject.toml" else {"ruff": data}
    found = _ruff_disables(tool.get("ruff", {}))
    found += _pylint_disables(tool.get("pylint", {}))
    found += _pyright_disables(tool.get("pyright", {}))
    return [_finding(path, _line_of(text, item.split(" = ", 1)[0]), item) for item in found]


def scan_pylint_ini(path: Path, text: str) -> list[FileValidationViolation]:
    """Pylint ``disable =`` entries in an INI-style config."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        _log.debug("Config disable scan skips unparsable %s: %s", path, exc)
        return []
    is_shared = path.name in {"setup.cfg", "tox.ini"}
    found: list[FileValidationViolation] = []
    for name in parser.sections():
        if is_shared and not name.startswith("pylint"):
            continue
        value = parser.get(name, "disable", fallback="").strip()
        if value:
            found.append(_finding(path, _line_of(text, "disable"), f"disable = {value}"))
    return found


def _json_object(path: Path, text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        _log.debug("Config disable scan skips unparsable %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def scan_pyright_json(path: Path, text: str) -> list[FileValidationViolation]:
    """Pyright ``report*`` rules switched off in ``pyrightconfig.json``."""
    return [
        _finding(path, _line_of(text, f'"{item.split(" = ", 1)[0]}"'), item)
        for item in _pyright_disables(_json_object(path, text))
    ]


def scan_markdownlint(path: Path, text: str) -> list[FileValidationViolation]:
    """markdownlint rules set to ``false``."""
    return [
        _finding(path, _line_of(text, f'"{rule}"'), f"{rule}: false")
        for rule, value in _json_object(path, text).items()
        if value is False
    ]


def scan_eslint(path: Path, text: str) -> list[FileValidationViolation]:
    """ESLint rules set to ``"off"`` or ``0``."""
    return [
        _finding(path, text.count("\n", 0, m.start()) + 1, m.group(0))
        for m in _ESLINT_OFF.finditer(text)
    ]


def scan_config_disables(path: Path) -> list[FileValidationViolation]:
    """Return every rule ``path`` disables, or nothing when it is not a linter config."""
    name = path.name
    if name in RUFF_FILES:
        scanner = scan_toml
    elif name in PYLINT_INI_FILES:
        scanner = scan_pylint_ini
    elif name in PYRIGHT_JSON_FILES:
        scanner = scan_pyright_json
    elif name in MARKDOWNLINT_FILES:
        scanner = scan_markdownlint
    elif name.startswith(ESLINT_PREFIXES):
        scanner = scan_eslint
    else:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        _log.warning("Cannot read config %s: %s", path, exc)
        return [read_error(path, exc)]
    return scanner(path, text)
