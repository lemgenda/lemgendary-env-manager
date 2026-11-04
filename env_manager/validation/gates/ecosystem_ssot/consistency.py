"""Consistency rules for documentation and records: written-out facts must be right.

Human documentation and registries may spell out a service port, the Kaggle
owner or the docs URL, but only with the value in ``ecosystem.json``. Each
rule here reports a value that contradicts the file:

- a port given for a named service that is not that service's port;
- one of our ``LemGendized*`` Kaggle datasets under another owner, or a
  ``kaggle_username`` that is not the owner;
- a URL on the docs host outside the docs base URL;
- a project folder under a legacy name.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

from env_manager.ecosystem import Ecosystem
from env_manager.validation.gates.ecosystem_ssot.literals import Line
from env_manager.validation.models import FileValidationViolation

WRONG_PORT = "ssot_wrong_port"
WRONG_KAGGLE_OWNER = "ssot_wrong_kaggle_owner"
WRONG_DOCS_URL = "ssot_wrong_docs_url"
LEGACY_FOLDER = "ssot_legacy_folder"

OWN_DATASET_PREFIX = "lemgendized"
"""Slug prefix of the ecosystem's own Kaggle datasets and models (case-insensitive)."""

DISPLAY_PREFIX = "LemGendary "
"""Brand prefix of project display names; the rest also names the project."""

_PORT_MENTION = re.compile(
    r"(?:(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\]):|\bports?\b[\s:=(`\"']{1,3})(\d{4,5})(?!\d)",
    re.IGNORECASE,
)
_OWN_KAGGLE_REF = re.compile(
    rf"(?:kaggle://|kaggle\.com/(?:datasets/|models/)?)([\w-]+)/({OWN_DATASET_PREFIX}[\w-]*)",
    re.IGNORECASE,
)
_KAGGLE_USERNAME = re.compile(r"\bkaggle_username[\"']?\s*[:=]\s*[\"']?([\w-]+)", re.IGNORECASE)


def _violation(path: str, number: int, kind: str, message: str) -> FileValidationViolation:
    return FileValidationViolation(
        file_path=path, line_number=number, violation_type=kind, message=message,
    )


@dataclass(frozen=True)
class ServicePortRule:
    """A port written next to a service name must be that service's port."""

    names: tuple[tuple[re.Pattern[str], str, int], ...]

    @classmethod
    def for_ecosystem(cls, eco: Ecosystem) -> "ServicePortRule":
        """Match each service by id, hyphenated alias, project folder and display name."""
        names: list[tuple[re.Pattern[str], str, int]] = []
        for svc in eco.services:
            display = eco.display_name(svc.project)
            spellings = {
                svc.id, svc.id.replace("-", " "), svc.project, display,
                display.removeprefix(DISPLAY_PREFIX),
                *(alias for alias in svc.aliases if "-" in alias),
            }
            for spelling in sorted(spellings, key=len, reverse=True):
                pattern = re.compile(rf"(?<![\w-]){re.escape(spelling)}(?![\w-])", re.IGNORECASE)
                names.append((pattern, svc.id, svc.port))
        return cls(tuple(names))

    def _nearest_service(self, text: str, end: int) -> tuple[str, int] | None:
        """The service named closest before ``end`` on the line, if any."""
        mentions = [
            (match.start(), service_id, port)
            for pattern, service_id, port in self.names
            for match in pattern.finditer(text, 0, end)
        ]
        if not mentions:
            return None
        _, service_id, port = max(mentions)
        return service_id, port

    def scan(self, path: str, lines: Sequence[Line]) -> list[FileValidationViolation]:
        """Return one violation per port that contradicts the service named before it."""
        found: list[FileValidationViolation] = []
        for number, text in lines:
            for match in _PORT_MENTION.finditer(text):
                named = self._nearest_service(text, match.start())
                if named is None or int(match.group(1)) == named[1]:
                    continue
                found.append(_violation(
                    path, number, WRONG_PORT,
                    f"Port {match.group(1)} given for {named[0]}; ecosystem.json has "
                    f"{named[1]}: {text.strip()[:120]}",
                ))
        return found


@dataclass(frozen=True)
class KaggleOwnerRule:
    """Our own Kaggle datasets and the configured username name the right owner."""

    owner: str

    def scan(self, path: str, lines: Sequence[Line]) -> list[FileValidationViolation]:
        """Return one violation per own reference or username with another owner."""
        found: list[FileValidationViolation] = []
        for number, text in lines:
            owners = [m.group(1) for m in _OWN_KAGGLE_REF.finditer(text)]
            owners += [m.group(1) for m in _KAGGLE_USERNAME.finditer(text)]
            found.extend(
                _violation(
                    path, number, WRONG_KAGGLE_OWNER,
                    f"Kaggle owner '{owner}' differs from ecosystem.json "
                    f"'{self.owner}': {text.strip()[:120]}",
                )
                for owner in owners if owner != self.owner
            )
        return found


@dataclass(frozen=True)
class DocsUrlRule:
    """Links to the docs host stay inside the docs base URL."""

    docs_url: str

    def scan(self, path: str, lines: Sequence[Line]) -> list[FileValidationViolation]:
        """Return one violation per docs-host URL outside the base URL."""
        host = urlparse(self.docs_url).netloc
        pattern = re.compile(rf"https?://{re.escape(host)}[^\s\"'<>)\]`]*")
        base = self.docs_url.split("://", 1)[1]
        found: list[FileValidationViolation] = []
        for number, text in lines:
            for match in pattern.finditer(text):
                rest = match.group(0).split("://", 1)[1]
                if rest == base or rest.startswith(base + "/"):
                    continue
                found.append(_violation(
                    path, number, WRONG_DOCS_URL,
                    f"Docs URL {match.group(0)} is outside {self.docs_url}",
                ))
        return found


@dataclass(frozen=True)
class LegacyFolderRule:
    """Project folders are written under their current names."""

    pattern: re.Pattern[str] | None
    current: dict[str, str]

    @classmethod
    def for_ecosystem(cls, eco: Ecosystem) -> "LegacyFolderRule":
        """Build the rule from every project's ``legacy_folders``."""
        current = {old: p.folder for p in eco.projects for old in p.legacy_folders}
        if not current:
            return cls(None, current)
        names = "|".join(re.escape(n) for n in sorted(current, key=len, reverse=True))
        return cls(re.compile(rf"(?<![\w-])(?:{names})(?![\w-])"), current)

    def scan(self, path: str, lines: Sequence[Line]) -> list[FileValidationViolation]:
        """Return one violation per legacy folder name."""
        if self.pattern is None:
            return []
        return [
            _violation(
                path, number, LEGACY_FOLDER,
                f"Legacy folder '{old}' is now '{self.current[old]}' (ecosystem.json)",
            )
            for number, text in lines
            for old in self.pattern.findall(text)
        ]


ConsistencyRule = ServicePortRule | KaggleOwnerRule | DocsUrlRule | LegacyFolderRule


def consistency_rules(eco: Ecosystem) -> tuple[ConsistencyRule, ...]:
    """Every rule for documentation and records, built from ``eco``."""
    return (
        ServicePortRule.for_ecosystem(eco),
        KaggleOwnerRule(eco.identity.kaggle_owner),
        DocsUrlRule(eco.identity.docs_url),
        LegacyFolderRule.for_ecosystem(eco),
    )
