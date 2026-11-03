"""The default gate registry: every check ``validate_project`` runs, in order.

To add a gate, append a ``Gate`` to ``GATES`` (or call ``register_gate``);
the runner does not change.
"""

from env_manager.validation.domain.datasets import audit_dataset_manifests
from env_manager.validation.domain.docs_hub import (
    audit_docs_synchronization,
    audit_documentation_suite,
    audit_manifest_synchronization,
)
from env_manager.validation.domain.models_training import audit_models_hub, audit_training_suite
from env_manager.validation.domain.project_hygiene import (
    asset_store_folders,
    audit_git_hooks,
    audit_readme_changelog,
)
from env_manager.validation.gate import Bucket, Gate, ProjectContext, for_projects
from env_manager.validation.gates.absolute_paths import run_absolute_path_scan
from env_manager.validation.gates.data_formats import run_jsonlint, run_yamllint
from env_manager.validation.gates.ecosystem_ssot import (
    ECOSYSTEM_SSOT_SEVERITY,
    run_ecosystem_ssot,
)
from env_manager.validation.gates.node_linters import (
    run_eslint,
    run_markdownlint,
    run_typescript_check,
)
from env_manager.validation.gates.powershell import run_psscriptanalyzer
from env_manager.validation.gates.pylint_gate import run_pylint
from env_manager.validation.gates.suppressions import run_suppression_scan
from env_manager.validation.gates.web import run_html_validate, run_pa11y_wcag


def is_node(ctx: ProjectContext) -> bool:
    """Node projects: flagged as such, or holding a ``package.json``."""
    return ctx.is_node_project or (ctx.project_dir / "package.json").exists()


def has_static_html(ctx: ProjectContext) -> bool:
    """Static HTML sites (an ``index.html`` or ``papers/``), not Tauri apps."""
    root = ctx.project_dir
    has_html = (root / "index.html").exists() or (root / "papers").exists()
    return has_html and not (root / "src-tauri").exists()


def is_code_project(ctx: ProjectContext) -> bool:
    """Every project except the data-only asset stores."""
    return ctx.name not in asset_store_folders()


GATES: list[Gate] = [
    Gate("yamllint", Bucket.YAML, run_yamllint, external=True),
    Gate("jsonlint", Bucket.JSON, run_jsonlint),
    Gate("markdownlint", Bucket.LINT, run_markdownlint, external=True),
    Gate("eslint", Bucket.LINT, run_eslint, applies=is_node, external=True),
    Gate("tsc", Bucket.LINT, run_typescript_check, applies=is_node, external=True),
    Gate("html-validate", Bucket.HTML, run_html_validate, applies=has_static_html, external=True),
    Gate("pa11y", Bucket.WCAG, run_pa11y_wcag, applies=has_static_html, external=True),
    Gate("psscriptanalyzer", Bucket.LINT, run_psscriptanalyzer, external=True),
    Gate("pylint", Bucket.LINT, run_pylint, external=True),
    Gate("suppressions", Bucket.LINT, run_suppression_scan),
    Gate("absolute-paths", Bucket.LINT, run_absolute_path_scan),
    Gate("readme-changelog", Bucket.DOMAIN, audit_readme_changelog, applies=is_code_project),
    Gate("docs-sync", Bucket.DOMAIN, audit_docs_synchronization,
         applies=for_projects("lemgendary-docs")),
    Gate("docs-manifests", Bucket.DOMAIN, audit_manifest_synchronization,
         applies=for_projects("lemgendary-docs")),
    Gate("docs-suite", Bucket.DOMAIN, audit_documentation_suite,
         applies=for_projects("lemgendary-docs")),
    Gate("dataset-manifests", Bucket.DOMAIN, audit_dataset_manifests,
         applies=for_projects("lemgendary-datasets", "LemGendaryDatasets")),
    Gate("training-suite", Bucket.DOMAIN, audit_training_suite,
         applies=for_projects("lemgendary-training-suite")),
    Gate("models-hub", Bucket.DOMAIN, audit_models_hub,
         applies=for_projects("lemgendary-models", "LemGendaryModels")),
    Gate("git-hooks", Bucket.DOMAIN, audit_git_hooks),
    Gate("ecosystem_ssot", Bucket.DOMAIN, run_ecosystem_ssot, severity=ECOSYSTEM_SSOT_SEVERITY),
]
"""Gates in run order. The compile and emoji walk runs before all of them."""


def register_gate(gate: Gate) -> None:
    """Add ``gate`` to the default registry."""
    GATES.append(gate)
