"""Git hook management for LemGendary ecosystem projects.

Ensures all projects have standardized pre-commit hooks that delegate
validation to lemgendary-env-manager instead of referencing retired
individual verification scripts.
"""

import os
import stat
import subprocess
from pathlib import Path

from env_manager.utils import run_command_simple
from env_manager.ecosystem import ecosystem
from env_manager.workspace import workspace_root

PRE_COMMIT_TEMPLATE = """#!/bin/sh
# LemGendary Ecosystem Pre-Commit Hook
# Standardized hook enforcing full validation via lemgendary-env-manager.
# Works from .githooks/ or .git/hooks/, on Windows (Git Bash), Linux and macOS.

# git runs hooks from the top of the work tree; ask git rather than guessing
# from the hook's own location, which differs between .githooks and .git/hooks.
ROOT_DIR="$(git rev-parse --show-toplevel)" || exit 1
PROJECT_NAME="$(basename "$ROOT_DIR")"
WORKSPACE_ROOT="$(cd "$ROOT_DIR/.." && pwd)"
ENV_MANAGER_DIR="$WORKSPACE_ROOT/lemgendary-env-manager"

echo "[PRE-COMMIT] Running LemGendary Ecosystem Validation for $PROJECT_NAME..."

LEM_ENV=""
LEM_ENV_PY=""
# Windows venvs use Scripts/ and .exe; POSIX venvs use bin/.
for BIN_DIR in "$ENV_MANAGER_DIR/.venv/Scripts" "$ENV_MANAGER_DIR/.venv/bin"; do
    for CANDIDATE in "$BIN_DIR/lem-env.exe" "$BIN_DIR/lem-env"; do
        if [ -z "$LEM_ENV" ] && [ -f "$CANDIDATE" ]; then
            LEM_ENV="$CANDIDATE"
        fi
    done
    for CANDIDATE in "$BIN_DIR/python.exe" "$BIN_DIR/python"; do
        if [ -z "$LEM_ENV_PY" ] && [ -f "$CANDIDATE" ]; then
            LEM_ENV_PY="$CANDIDATE"
        fi
    done
done

if [ -n "$LEM_ENV" ]; then
    "$LEM_ENV" validate --project "$PROJECT_NAME"
    EXIT_CODE=$?
elif [ -n "$LEM_ENV_PY" ]; then
    (cd "$ENV_MANAGER_DIR" && "$LEM_ENV_PY" -m env_manager.cli validate --project "$PROJECT_NAME")
    EXIT_CODE=$?
else
    echo "[ERROR] lem-env not found under $ENV_MANAGER_DIR/.venv. Cannot validate."
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

_EXECUTABLE_BITS = stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH


def _write_hook(hook_file: Path) -> None:
    """Write the pre-commit template to ``hook_file`` and make it executable."""
    hook_file.write_text(PRE_COMMIT_TEMPLATE, encoding="utf-8", newline="\n")
    os.chmod(hook_file, hook_file.stat().st_mode | _EXECUTABLE_BITS)


def _install_one(proj_dir: Path) -> tuple[bool, str]:
    """Install the hook in one repository; return ``(success, message)``."""
    githooks_dir = proj_dir / ".githooks"
    githooks_dir.mkdir(parents=True, exist_ok=True)
    _write_hook(githooks_dir / "pre-commit")

    proc = run_command_simple(
        ["git", "config", "core.hooksPath", ".githooks"], timeout=30, cwd=proj_dir,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        return False, f"git config core.hooksPath failed (exit {proc.returncode}): {detail}"

    # Keep a copy in .git/hooks as a fallback for clients that ignore hooksPath.
    legacy_hooks_dir = proj_dir / ".git" / "hooks"
    if legacy_hooks_dir.is_dir():
        _write_hook(legacy_hooks_dir / "pre-commit")
    return True, "Standardized pre-commit hook installed"


def install_git_hooks(
    base_dir: Path | None = None,
    target_project: str | None = None,
) -> list[tuple[str, bool, str]]:
    """Install or update standardized pre-commit hooks across ecosystem projects.

    Covers the ``git_hooked`` projects in ``ecosystem.json`` that are git
    repositories. Returns a list of tuples: (project_name, success, message)
    """
    if base_dir is None:
        base_dir = workspace_root()

    results: list[tuple[str, bool, str]] = []
    for project in ecosystem().git_hooked_projects():
        proj_name = project.folder
        if target_project and proj_name != target_project:
            continue
        proj_dir = base_dir / proj_name
        if not (proj_dir / ".git").exists():
            continue
        try:
            ok, message = _install_one(proj_dir)
        except (OSError, subprocess.SubprocessError) as exc:
            ok, message = False, f"Failed to install hook: {exc}"
        results.append((proj_name, ok, message))
    return results
