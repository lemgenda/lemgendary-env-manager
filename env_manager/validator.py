"""Workspace validation runner.

``validate_project`` runs the per-file compile and emoji walk, then every
gate in ``env_manager.validation.registry.GATES`` whose predicate applies
to the project, and collects the violations into a
``ProjectValidationReport``. The gates themselves live in
``env_manager.validation.gates`` (tool and source-text checks) and
``env_manager.validation.domain`` (project-specific rules).

This module also keeps the public names that callers import from
``env_manager.validator``.
"""

from collections.abc import Iterator
from pathlib import Path

from env_manager.ecosystem import ecosystem
from env_manager.validation import registry
from env_manager.validation.gate import Bucket, Gate, ProjectContext
from env_manager.validation.gates.data_formats import run_jsonlint, run_yamllint
from env_manager.validation.gates.node_linters import (
    run_eslint,
    run_markdownlint,
    run_typescript_check,
)
from env_manager.validation.gates.powershell import parse_pssa_line, run_psscriptanalyzer
from env_manager.validation.gates.source_files import (
    compile_python_file,
    scan_file_for_emojis,
    scan_sources,
)
from env_manager.validation.gates.web import run_html_validate, run_pa11y_wcag
from env_manager.validation.models import (
    TOOL_ERROR,
    FileValidationViolation,
    ProjectValidationReport,
)



def run_gates(
    ctx: ProjectContext, gates: list[Gate] | None = None,
) -> dict[Bucket, list[FileValidationViolation]]:
    """Run every applicable gate on ``ctx`` and group the violations by bucket."""
    found: dict[Bucket, list[FileValidationViolation]] = {bucket: [] for bucket in Bucket}
    for gate in registry.GATES if gates is None else gates:
        found[gate.target_bucket].extend(gate.run(ctx))
    return found


def run_domain_verification(project_dir: Path) -> list[FileValidationViolation]:
    """Run only the project-domain gates on ``project_dir``."""
    domain = [g for g in registry.GATES if g.bucket is Bucket.DOMAIN]
    return run_gates(ProjectContext(project_dir), domain)[Bucket.DOMAIN]


def validate_workspace(
    base_dir: Path, project: str | None = None,
) -> dict[str, ProjectValidationReport]:
    """Validate every validated project present under ``base_dir``, or only ``project``.

    The projects (and whether each is an npm project) come from
    ``ecosystem.json`` (``projects[].validated``, ``projects[].kind``).

    The one implementation behind the CLI and the API, so both check the
    same projects with the same ``is_node_project`` flags.
    """
    return dict(iter_workspace_validation(base_dir, project))


def iter_workspace_validation(
    base_dir: Path, project: str | None = None,
) -> Iterator[tuple[str, ProjectValidationReport]]:
    """Yield ``(folder, report)`` for each validated project, one at a time.

    Same projects and flags as :func:`validate_workspace`; callers that
    stream progress (pipeline step 6) report each project as it finishes.
    """
    for target in ecosystem().validated_projects():
        name = target.folder
        if project is not None and name != project:
            continue
        project_dir = base_dir / name
        if project_dir.exists():
            yield name, validate_project(project_dir, is_node_project=target.is_node)


def validate_project(project_dir: Path, is_node_project: bool = False) -> ProjectValidationReport:
    """Execute the complete validation suite across all files in a project."""
    scan = scan_sources(project_dir)
    found = run_gates(ProjectContext(project_dir, is_node_project))
    passed = (
        not scan.compile_errors
        and not scan.emoji_violations
        and not any(v for bucket, v in found.items() if bucket is not Bucket.WARNINGS)
    )
    return ProjectValidationReport(
        project_name=project_dir.name,
        compiled_files_count=scan.compiled_files_count,
        compile_errors=scan.compile_errors,
        emoji_violations=scan.emoji_violations,
        lint_errors=found[Bucket.LINT],
        yaml_errors=found[Bucket.YAML],
        json_errors=found[Bucket.JSON],
        html_errors=found[Bucket.HTML],
        wcag_violations=found[Bucket.WCAG],
        domain_errors=found[Bucket.DOMAIN],
        passed=passed,
        warnings=found[Bucket.WARNINGS],
    )


__all__ = [
    "TOOL_ERROR",
    "FileValidationViolation",
    "ProjectValidationReport",
    "compile_python_file",
    "iter_workspace_validation",
    "parse_pssa_line",
    "run_domain_verification",
    "run_eslint",
    "run_gates",
    "run_html_validate",
    "run_jsonlint",
    "run_markdownlint",
    "run_pa11y_wcag",
    "run_psscriptanalyzer",
    "run_typescript_check",
    "run_yamllint",
    "scan_file_for_emojis",
    "validate_project",
    "validate_workspace",
]
