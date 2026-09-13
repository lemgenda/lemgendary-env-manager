"""Git hook management for LemGendary ecosystem projects.

Ensures all projects have standardized pre-commit hooks that delegate
validation to lemgendary-env-manager instead of referencing retired
individual verification scripts.
"""

from pathlib import Path
import subprocess
from typing import List, Optional, Tuple

PRE_COMMIT_TEMPLATE = """#!/bin/sh
# LemGendary Ecosystem Pre-Commit Hook
# Standardized hook enforcing full validation via lemgendary-env-manager

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_NAME="$(basename "$ROOT_DIR")"
WORKSPACE_ROOT="$(cd "$ROOT_DIR/.." && pwd)"

echo "[PRE-COMMIT] Running LemGendary Ecosystem Validation for $PROJECT_NAME..."

LEM_ENV_EXE="$WORKSPACE_ROOT/lemgendary-env-manager/.venv/Scripts/lem-env.exe"
LEM_ENV_PY="$WORKSPACE_ROOT/lemgendary-env-manager/.venv/Scripts/python.exe"

if [ -f "$LEM_ENV_EXE" ]; then
    "$LEM_ENV_EXE" validate --project "$PROJECT_NAME"
    EXIT_CODE=$?
elif [ -f "$LEM_ENV_PY" ]; then
    export PYTHONPATH="$WORKSPACE_ROOT/lemgendary-env-manager:$PYTHONPATH"
    "$LEM_ENV_PY" -m env_manager.cli validate --project "$PROJECT_NAME"
    EXIT_CODE=$?
else
    echo "[ERROR] lem-env not found at $WORKSPACE_ROOT/lemgendary-env-manager. Cannot validate."
    exit 1
fi

if [ $EXIT_CODE -ne 0 ]; then
    echo "====================================================="
    echo "COMMIT BLOCKED: Validation failed for $PROJECT_NAME."
    echo "Run 'lem-env validate --project $PROJECT_NAME' to inspect."
    echo "====================================================="
    exit $EXIT_CODE
fi

echo "[PRE-COMMIT] Validation passed for $PROJECT_NAME."
exit 0
"""


def install_git_hooks(
    base_dir: Optional[Path] = None,
    target_project: Optional[str] = None,
) -> List[Tuple[str, bool, str]]:
    """Install or update standardized pre-commit hooks across ecosystem projects.

    Returns a list of tuples: (project_name, success, message)
    """
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent.parent

    results: List[Tuple[str, bool, str]] = []

    # Target directories
    candidate_projects = [
        "lemgendary-training-suite",
        "lemgendary-datasets",
        "lemgendary-env-manager",
        "lemgendary-ai-studio-gui",
        "lemgendary-docs",
        "LemGendaryDatasets",
        "LemGendaryModels",
    ]

    for proj_name in candidate_projects:
        if target_project and proj_name != target_project:
            continue

        proj_dir = base_dir / proj_name
        if not proj_dir.is_dir():
            continue

        git_dir = proj_dir / ".git"
        if not git_dir.exists():
            continue

        try:
            # 1. Ensure .githooks directory exists
            githooks_dir = proj_dir / ".githooks"
            githooks_dir.mkdir(parents=True, exist_ok=True)

            hook_file = githooks_dir / "pre-commit"
            hook_file.write_text(PRE_COMMIT_TEMPLATE, encoding="utf-8", newline="\n")

            # 2. Configure core.hooksPath
            subprocess.run(
                ["git", "config", "core.hooksPath", ".githooks"],
                cwd=str(proj_dir),
                capture_output=True,
                check=False,
            )

            # 3. If .git/hooks exists, sync pre-commit there as well as fallback
            legacy_hooks_dir = git_dir / "hooks"
            if legacy_hooks_dir.is_dir():
                legacy_hook = legacy_hooks_dir / "pre-commit"
                legacy_hook.write_text(PRE_COMMIT_TEMPLATE, encoding="utf-8", newline="\n")

            results.append((proj_name, True, "Standardized pre-commit hook installed"))
        except (OSError, subprocess.SubprocessError) as exc:
            results.append((proj_name, False, f"Failed to install hook: {exc}"))

    return results
