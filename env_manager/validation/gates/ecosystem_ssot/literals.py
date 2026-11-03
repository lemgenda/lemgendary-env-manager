"""Literal ecosystem facts written into a project's files.

Each rule matches one fact from ``ecosystem.json`` (a service port, the
Kaggle owner, the docs URL, or a list of project folders) on the lines of a
file that the allowlist does not cover.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from env_manager.ecosystem import Ecosystem
from env_manager.validation.models import FileValidationViolation

PORT_LITERAL = "ssot_port"
KAGGLE_OWNER_LITERAL = "ssot_kaggle_owner"
DOCS_URL_LITERAL = "ssot_docs_url"
PROJECT_LIST_LITERAL = "ssot_project_list"

PROJECT_LIST_MINIMUM = 3
"""Distinct project folders that make a nearby group of mentions a list."""

PROJECT_LIST_GAP = 2
"""Most lines between two folder mentions that still belong to one list."""

Line = tuple[int, str]
"""A ``(line_number, text)`` pair."""


@dataclass(frozen=True)
class LiteralRule:
    """One fact that must not be written out: a pattern and how to report it."""

    violation_type: str
    pattern: re.Pattern[str]
    label: str

    def scan(self, path: str, lines: Sequence[Line]) -> list[FileValidationViolation]:
        """Return one violation per line of ``lines`` that matches the pattern."""
        return [
            FileValidationViolation(
                file_path=path, line_number=number, violation_type=self.violation_type,
                message=f"Literal {self.label} (read ecosystem.json): {text.strip()[:120]}",
            )
            for number, text in lines
            if re.search(self.pattern, text)
        ]


PORT_TOKEN_BEFORE = r"(?<![\w.#$-])"
"""A port token is not the tail of a word, hex value, version, colour or range."""

PORT_TOKEN_AFTER = r"(?![\w$]|[.,]\d)"
"""A port token is not the head of a longer word, unit or decimal number."""


def port_rule(eco: Ecosystem) -> LiteralRule:
    """Any service port written as a standalone number token.

    For a port ``P``: ``:P``, ``}:P``, ``(P)``, ``= P`` and ``P,`` all match;
    ``1P``, ``P1``, ``0xP``, ``#P``, ``1.P``, ``P.5`` and ``-P`` do not.
    Code, configuration and tests read ports from the ecosystem file.
    """
    ports = "|".join(str(svc.port) for svc in sorted(eco.services, key=lambda s: s.port))
    pattern = re.compile(rf"{PORT_TOKEN_BEFORE}(?:{ports}){PORT_TOKEN_AFTER}")
    return LiteralRule(PORT_LITERAL, pattern, "service port")


def kaggle_owner_rule(eco: Ecosystem) -> LiteralRule:
    """The Kaggle owner account name as a whole word."""
    owner = re.escape(eco.identity.kaggle_owner)
    return LiteralRule(
        KAGGLE_OWNER_LITERAL, re.compile(rf"(?<![\w-]){owner}(?![\w-])"), "Kaggle owner",
    )


def docs_url_rule(eco: Ecosystem) -> LiteralRule:
    """The documentation site base URL."""
    return LiteralRule(
        DOCS_URL_LITERAL, re.compile(re.escape(eco.identity.docs_url)), "docs URL",
    )


def literal_rules(eco: Ecosystem) -> tuple[LiteralRule, ...]:
    """Every single-line rule for ``eco``."""
    return (port_rule(eco), kaggle_owner_rule(eco), docs_url_rule(eco))


@dataclass(frozen=True)
class ProjectListRule:
    """Groups of nearby lines that name at least three project folders."""

    pattern: re.Pattern[str]
    minimum: int = PROJECT_LIST_MINIMUM
    gap: int = PROJECT_LIST_GAP

    @classmethod
    def for_ecosystem(cls, eco: Ecosystem) -> "ProjectListRule":
        """Build the rule from the folders in ``eco``, longest name first."""
        folders = sorted((p.folder for p in eco.projects), key=len, reverse=True)
        names = "|".join(re.escape(name) for name in folders)
        return cls(re.compile(rf"(?<![\w-])(?:{names})(?![\w-])"))

    def scan(self, path: str, lines: Sequence[Line]) -> list[FileValidationViolation]:
        """Return one violation per group of mentions that names enough folders."""
        found: list[FileValidationViolation] = []
        for start, names in self._groups(lines):
            if len(names) >= self.minimum:
                found.append(FileValidationViolation(
                    file_path=path, line_number=start, violation_type=PROJECT_LIST_LITERAL,
                    message=(
                        f"Project list of {len(names)} folders (read ecosystem.json): "
                        + ", ".join(sorted(names))
                    ),
                ))
        return found

    def _groups(self, lines: Sequence[Line]) -> list[tuple[int, set[str]]]:
        """Split folder mentions into groups separated by more than ``gap`` lines."""
        groups: list[tuple[int, set[str]]] = []
        last = None
        for number, text in lines:
            names = set(re.findall(self.pattern, text))
            if not names:
                continue
            if last is None or number - last > self.gap:
                groups.append((number, set()))
            groups[-1][1].update(names)
            last = number
        return groups
