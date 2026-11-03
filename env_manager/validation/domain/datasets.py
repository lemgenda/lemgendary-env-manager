"""Domain gate for dataset manifests in the dataset compiler and the compiled manifolds repo."""

from pathlib import Path

import yaml

from env_manager.validation.domain.project_hygiene import audit_staging_residue
from env_manager.validation.models import MANIFEST_READ_ERRORS, FileValidationViolation

MANIFOLD_PARQUET_COUNT = 8
"""Parquet shards every compiled dataset manifold must contain."""

MANIFOLD_REQUIRED_FILES = ("README.md", "dataset_info.yaml", "category.txt", "classes.txt")
"""Files every ``LemGendized*`` manifold directory must contain."""

FOREX_MANIFOLD = "LemGendizedForexUniverseLarge"
"""The manifold that also needs ``dataset-metadata.json`` and a fixed Parquet shard count."""


def _violation(path: Path, line: int, kind: str, message: str) -> FileValidationViolation:
    """Return a violation for ``path``."""
    return FileValidationViolation(
        file_path=str(path), line_number=line, violation_type=kind, message=message,
    )


def _yaml_mapping_violations(
    path: Path, required: tuple[str, ...], prefix: str, missing_fmt: str,
) -> list[FileValidationViolation]:
    """Check that ``path`` parses to a mapping holding every ``required`` key.

    Violation types are ``<prefix>_parse_error`` and ``<prefix>_schema_error``;
    ``missing_fmt`` formats the message for a missing ``{key}``.
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except MANIFEST_READ_ERRORS as exc:
        return [_violation(path, 0, f"{prefix}_parse_error", str(exc))]
    if not isinstance(data, dict):
        return [_violation(path, 1, f"{prefix}_schema_error",
                           f"{path.name} root must be a dictionary")]
    return [
        _violation(path, 1, f"{prefix}_schema_error", missing_fmt.format(key=key))
        for key in required if key not in data
    ]


def _manifold_violations(manifold: Path) -> list[FileValidationViolation]:
    """Check one manifold directory's required files (and the Forex shard layout)."""
    violations = [
        _violation(manifold, 1, "manifold_missing_file",
                   f"{manifold.name} missing required file '{req_f}'")
        for req_f in MANIFOLD_REQUIRED_FILES if not (manifold / req_f).exists()
    ]
    if manifold.name != FOREX_MANIFOLD:
        return violations
    meta_json = manifold / "dataset-metadata.json"
    if not meta_json.exists():
        violations.append(_violation(meta_json, 1, "manifold_missing_file",
                                     f"{manifold.name} missing dataset-metadata.json"))
    parquets = list(manifold.glob("*.parquet"))
    if len(parquets) != MANIFOLD_PARQUET_COUNT:
        violations.append(_violation(
            manifold, 1, "manifold_parquet_count",
            f"{manifold.name} expected {MANIFOLD_PARQUET_COUNT} Parquet files, "
            f"found {len(parquets)}",
        ))
    return violations


def audit_dataset_manifests(project_dir: Path) -> list[FileValidationViolation]:
    """Verify dataset manifest schema correctness and manifold metadata integrity."""
    violations: list[FileValidationViolation] = []
    unified_yaml = project_dir / "unified_data.yaml"
    if unified_yaml.exists():
        violations += _yaml_mapping_violations(
            unified_yaml, ("_registry_metadata",), "manifest",
            "unified_data.yaml missing required '{key}' block",
        )

    models_yaml = project_dir / "models" / "models_metadata.yaml"
    if not models_yaml.exists():
        models_yaml = project_dir / "models_metadata.yaml"
    if models_yaml.exists():
        violations += _yaml_mapping_violations(
            models_yaml, ("task_metadata",), "manifest",
            "models_metadata.yaml missing required '{key}' block",
        )

    info_files = (
        list(project_dir.glob("*/dataset_info.yaml"))
        + list(project_dir.glob("*/*/dataset_info.yaml"))
    )
    for d_info in info_files:
        violations += _yaml_mapping_violations(
            d_info, ("count", "task"), "manifold",
            "dataset_info.yaml missing required field '{key}'",
        )

    for manifold in project_dir.iterdir():
        if manifold.is_dir() and manifold.name.startswith("LemGendized"):
            violations += _manifold_violations(manifold)

    violations.extend(audit_staging_residue(
        project_dir, "staging_residue",
        "Detected unapproved staging residue", "Detected unapproved temporary file",
    ))
    return violations
