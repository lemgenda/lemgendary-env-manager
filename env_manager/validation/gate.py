"""The validation gate abstraction: a named check bound to a report bucket.

New gates are added by registering a ``Gate`` in ``registry.GATES``; the
runner in ``env_manager.validator`` never changes (OCP).
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from env_manager.validation.models import FileValidationViolation


class Bucket(StrEnum):
    """The ``ProjectValidationReport`` list a gate's violations go into."""

    LINT = "lint_errors"
    YAML = "yaml_errors"
    JSON = "json_errors"
    HTML = "html_errors"
    WCAG = "wcag_violations"
    DOMAIN = "domain_errors"


@dataclass(frozen=True)
class ProjectContext:
    """What a gate may know about the project it checks."""

    project_dir: Path
    is_node_project: bool = False

    @property
    def name(self) -> str:
        """Directory name of the project."""
        return self.project_dir.name


def always(_ctx: ProjectContext) -> bool:
    """Applicability predicate for gates that run on every project."""
    return True


@dataclass(frozen=True)
class Gate:
    """One validation check.

    ``external`` marks gates that shell out to a tool that may be absent,
    so tests can run without it.
    """

    name: str
    bucket: Bucket
    check: Callable[[Path], list[FileValidationViolation]]
    applies: Callable[[ProjectContext], bool] = always
    external: bool = False

    def run(self, ctx: ProjectContext) -> list[FileValidationViolation]:
        """Run the check when it applies to ``ctx``, else return no violations."""
        if not self.applies(ctx):
            return []
        return self.check(ctx.project_dir)


def for_projects(*names: str) -> Callable[[ProjectContext], bool]:
    """Return a predicate that is true for the named projects only."""
    wanted = frozenset(names)

    def applies(ctx: ProjectContext) -> bool:
        return ctx.name in wanted

    return applies
