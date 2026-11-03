"""Domain gate for dataset manifests in the dataset compiler and the compiled manifolds repo."""

from pathlib import Path

import yaml

from env_manager.validation.domain.project_hygiene import audit_staging_residue
from env_manager.validation.models import MANIFEST_READ_ERRORS, FileValidationViolation

MANIFOLD_PARQUET_COUNT = 8
"""Parquet shards every compiled dataset manifold must contain."""


def audit_dataset_manifests(project_dir: Path) -> list[FileValidationViolation]:
    """Verify dataset manifest schema correctness and manifold metadata integrity."""
    violations: list[FileValidationViolation] = []
    unified_yaml = project_dir / "unified_data.yaml"
    if unified_yaml.exists():
        try:
            data = yaml.safe_load(unified_yaml.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                violations.append(
                    FileValidationViolation(
                        file_path=str(unified_yaml),
                        line_number=1,
                        violation_type="manifest_schema_error",
                        message="unified_data.yaml root must be a dictionary",
                    )
                )
            elif "_registry_metadata" not in data:
                violations.append(
                    FileValidationViolation(
                        file_path=str(unified_yaml),
                        line_number=1,
                        violation_type="manifest_schema_error",
                        message="unified_data.yaml missing required '_registry_metadata' block",
                    )
                )
        except MANIFEST_READ_ERRORS as exc:
            violations.append(
                FileValidationViolation(
                    file_path=str(unified_yaml),
                    line_number=0,
                    violation_type="manifest_parse_error",
                    message=str(exc),
                )
            )

    models_yaml = project_dir / "models" / "models_metadata.yaml"
    if not models_yaml.exists():
        models_yaml = project_dir / "models_metadata.yaml"
    if models_yaml.exists():
        try:
            data = yaml.safe_load(models_yaml.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                violations.append(
                    FileValidationViolation(
                        file_path=str(models_yaml),
                        line_number=1,
                        violation_type="manifest_schema_error",
                        message="models_metadata.yaml root must be a dictionary",
                    )
                )
            elif "task_metadata" not in data:
                violations.append(
                    FileValidationViolation(
                        file_path=str(models_yaml),
                        line_number=1,
                        violation_type="manifest_schema_error",
                        message="models_metadata.yaml missing required 'task_metadata' block",
                    )
                )
        except MANIFEST_READ_ERRORS as exc:
            violations.append(
                FileValidationViolation(
                    file_path=str(models_yaml),
                    line_number=0,
                    violation_type="manifest_parse_error",
                    message=str(exc),
                )
            )

    info_files = (
        list(project_dir.glob("*/dataset_info.yaml"))
        + list(project_dir.glob("*/*/dataset_info.yaml"))
    )
    for d_info in info_files:
        try:
            info = yaml.safe_load(d_info.read_text(encoding="utf-8"))
            if not isinstance(info, dict):
                violations.append(
                    FileValidationViolation(
                        file_path=str(d_info),
                        line_number=1,
                        violation_type="manifold_schema_error",
                        message="dataset_info.yaml root must be a dictionary",
                    )
                )
            else:
                for req_key in ["count", "task"]:
                    if req_key not in info:
                        violations.append(
                            FileValidationViolation(
                                file_path=str(d_info),
                                line_number=1,
                                violation_type="manifold_schema_error",
                                message=f"dataset_info.yaml missing required field '{req_key}'",
                            )
                        )
        except MANIFEST_READ_ERRORS as exc:
            violations.append(
                FileValidationViolation(
                    file_path=str(d_info),
                    line_number=0,
                    violation_type="manifold_parse_error",
                    message=str(exc),
                )
            )

    # ── Manifold Directory Integrity ─────────────────────────────────────────
    manifold_dirs = [
        d for d in project_dir.iterdir()
        if d.is_dir() and d.name.startswith("LemGendized")
    ]
    for manifold in manifold_dirs:
        for req_f in ["README.md", "dataset_info.yaml", "category.txt", "classes.txt"]:
            if not (manifold / req_f).exists():
                violations.append(
                    FileValidationViolation(
                        file_path=str(manifold),
                        line_number=1,
                        violation_type="manifold_missing_file",
                        message=f"{manifold.name} missing required file '{req_f}'",
                    )
                )

        if manifold.name == "LemGendizedForexUniverseLarge":
            meta_json = manifold / "dataset-metadata.json"
            if not meta_json.exists():
                violations.append(
                    FileValidationViolation(
                        file_path=str(meta_json),
                        line_number=1,
                        violation_type="manifold_missing_file",
                        message=f"{manifold.name} missing dataset-metadata.json",
                    )
                )
            parquets = list(manifold.glob("*.parquet"))
            if len(parquets) != MANIFOLD_PARQUET_COUNT:
                violations.append(
                    FileValidationViolation(
                        file_path=str(manifold),
                        line_number=1,
                        violation_type="manifold_parquet_count",
                        message=(
                            f"{manifold.name} expected {MANIFOLD_PARQUET_COUNT} Parquet files, "
                            f"found {len(parquets)}"
                        ),
                    )
                )

    # ── Staging Residue Check ────────────────────────────────────────────────
    violations.extend(audit_staging_residue(
        project_dir, "staging_residue",
        "Detected unapproved staging residue", "Detected unapproved temporary file",
    ))

    return violations
