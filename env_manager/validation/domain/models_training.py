"""Domain gates for the trained models hub and the model training suite."""

from pathlib import Path

import yaml

from env_manager.validation.domain.project_hygiene import audit_staging_residue
from env_manager.validation.models import MANIFEST_READ_ERRORS, FileValidationViolation


def audit_models_hub(project_dir: Path) -> list[FileValidationViolation]:
    """Verify model directory integrity, checkpoints, and zero residue."""
    violations: list[FileValidationViolation] = []
    root_readme = project_dir / "README.md"
    if not root_readme.exists():
        violations.append(
            FileValidationViolation(
                file_path=str(root_readme),
                line_number=1,
                violation_type="missing_readme",
                message="Root README.md is missing in models hub",
            )
        )

    model_dirs = [
        d for d in project_dir.iterdir()
        if d.is_dir()
        and not d.name.startswith(".")
        and d.name not in ["__pycache__", ".githooks", "typings"]
    ]
    for m_dir in model_dirs:
        m_readme = m_dir / "README.md"
        if not m_readme.exists():
            violations.append(
                FileValidationViolation(
                    file_path=str(m_dir),
                    line_number=1,
                    violation_type="missing_model_readme",
                    message=f"Model directory '{m_dir.name}' is missing README.md",
                )
            )
        checkpoints_dir = m_dir / "checkpoints"
        if checkpoints_dir.exists():
            for pth in checkpoints_dir.glob("*.pth"):
                if pth.stat().st_size == 0:
                    violations.append(
                        FileValidationViolation(
                            file_path=str(pth),
                            line_number=1,
                            violation_type="corrupt_checkpoint",
                            message=f"Zero-byte checkpoint detected: {pth.name}",
                        )
                    )

    violations.extend(audit_staging_residue(
        project_dir, "model_residue",
        "Detected staging residue", "Detected temporary file",
    ))

    return violations


def audit_training_suite(project_dir: Path) -> list[FileValidationViolation]:
    """Verify training suite configuration and neural model registry."""
    violations: list[FileValidationViolation] = []
    v2_models = project_dir / "unified_models_v2.yaml"
    if v2_models.exists():
        try:
            data = yaml.safe_load(v2_models.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                violations.append(
                    FileValidationViolation(
                        file_path=str(v2_models),
                        line_number=1,
                        violation_type="model_registry_error",
                        message="unified_models_v2.yaml root must be a dictionary",
                    )
                )
            elif "_registry_metadata" not in data:
                violations.append(
                    FileValidationViolation(
                        file_path=str(v2_models),
                        line_number=1,
                        violation_type="model_registry_error",
                        message="unified_models_v2.yaml missing '_registry_metadata'",
                    )
                )
        except MANIFEST_READ_ERRORS as exc:
            violations.append(
                FileValidationViolation(
                    file_path=str(v2_models),
                    line_number=0,
                    violation_type="model_registry_parse_error",
                    message=str(exc),
                )
            )
    return violations
