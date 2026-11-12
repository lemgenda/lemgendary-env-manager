"""Domain gates that apply to every code project: README changelog and git hooks."""

import re
import subprocess
from pathlib import Path

from env_manager._logging import get_logger
from env_manager.ecosystem import ecosystem
from env_manager.validation.models import FileValidationViolation
from env_manager.validation.tooling import run_tool

_log = get_logger(__name__)


def asset_store_folders() -> frozenset[str]:
    """Data-only repositories (``kind: asset`` in ``ecosystem.json``, with legacy folder names).

    They are exempt from the README changelog check.
    """
    return frozenset(
        name
        for project in ecosystem().projects if project.kind == "asset"
        for name in (project.folder, *project.legacy_folders)
    )


_HOOK_PY_SCRIPT_RE = re.compile(
    r'(?:python(?:\.exe)?|"\$VENV_PYTHON"|\$VENV_PYTHON)\s+([a-zA-Z0-9_\-\./\\]+\.py)'
)
"""Python script path invoked by a git hook line, e.g. ``python tools/check.py``."""


UNTRACKED_STATUS = "??"
"""``git status --porcelain`` code for an untracked file, which is not a change."""


def tracked_changes(porcelain: str) -> list[str]:
    """Paths of tracked files changed in the working tree or index.

    Untracked files (``??``) are skipped: scratch files are not changes
    that need a changelog entry. For a rename the new path is kept.
    """
    changed: list[str] = []
    for line in porcelain.splitlines():
        if len(line) < len("XY f") or line.startswith(UNTRACKED_STATUS):
            continue
        path_part = line[3:].strip()
        if " -> " in path_part:
            path_part = path_part.split(" -> ", 1)[1].strip()
        changed.append(path_part)
    return changed


def _changed_files(project_dir: Path) -> tuple[list[str], str]:
    """Changed files and where they come from: the working tree, else the last commit."""
    status = run_tool(["git", "status", "--porcelain"], cwd=project_dir, timeout=15)
    working = tracked_changes(status.stdout)
    if working:
        return working, "uncommitted changes in"
    diff = run_tool(["git", "diff", "HEAD~1", "--name-only"], cwd=project_dir, timeout=15)
    if diff.returncode != 0:
        return [], ""
    committed = [line.strip() for line in diff.stdout.splitlines() if line.strip()]
    return committed, "a last commit that changed"


def _readme_violation(readme_path: Path, kind: str, message: str) -> FileValidationViolation:
    return FileValidationViolation(
        file_path=str(readme_path), line_number=1, violation_type=kind, message=message,
    )


def audit_readme_changelog(project_dir: Path) -> list[FileValidationViolation]:
    """Verify that README.md's changelog was updated along with the project files.

    The changes checked are the tracked working-tree changes, or, on a
    clean tree, the files changed by the last commit.
    """
    readme_path = project_dir / "README.md"
    try:
        modified_files, origin = _changed_files(project_dir)
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("README changelog audit skipped for %s: %s", project_dir, exc)
        return []
    non_readme_changes = [f for f in modified_files if Path(f).name.lower() != "readme.md"]
    if not non_readme_changes:
        return []
    if not readme_path.exists():
        return [_readme_violation(
            readme_path, "readme_changelog_missing",
            f"Project '{project_dir.name}' has {len(non_readme_changes)} "
            "modified files but README.md does not exist.",
        )]
    readme_text = readme_path.read_text(encoding="utf-8", errors="replace")
    has_changelog = bool(
        re.search(r"##\s+(?:Changelog|CHANGELOG)", readme_text)
        or re.search(r"###\s+v\d+\.\d+", readme_text)
    )
    if not has_changelog:
        return [_readme_violation(
            readme_path, "readme_changelog_missing",
            f"Project '{project_dir.name}' has modified files but README.md "
            "lacks a valid Changelog section.",
        )]
    if len(non_readme_changes) == len(modified_files):
        return [_readme_violation(
            readme_path, "readme_changelog_unupdated",
            f"Project '{project_dir.name}' has {origin} {len(non_readme_changes)} "
            "files, but README.md changelog was not updated.",
        )]
    return []


RETIRED_HOOK_SCRIPTS = frozenset({
    "verify_training.py",
    "verify_datasets.py",
    "verify_docs.py",
    "verify_gui.py",
    "verify_manifold_datasets.py",
    "verify_models.py",
    "verify_env_manager.py",
})
"""Per-project verification scripts replaced by ``lem-env validate``."""


def _hook_files(project_dir: Path) -> list[Path]:
    """Return the git hook files in ``.githooks`` and ``.git/hooks``, each real file once."""
    seen: set[Path] = set()
    hooks: list[Path] = []
    for h_dir in (project_dir / ".githooks", project_dir / ".git" / "hooks"):
        if not h_dir.is_dir():
            continue
        for hook_file in h_dir.iterdir():
            if not hook_file.is_file() or hook_file.name.endswith(".sample"):
                continue
            try:
                canon = hook_file.resolve()
            except (OSError, RuntimeError) as exc:
                _log.warning("Cannot resolve git hook path %s: %s", hook_file, exc)
                canon = hook_file
            if canon not in seen:
                seen.add(canon)
                hooks.append(hook_file)
    return hooks


def _hook_line_violations(
    project_dir: Path, hook_file: Path, idx: int, line: str,
) -> list[FileValidationViolation]:
    """Flag retired scripts and missing Python targets on one non-comment hook line."""
    violations = [
        FileValidationViolation(
            file_path=str(hook_file),
            line_number=idx,
            violation_type="retired_git_hook_script",
            message=(
                f"Hook invokes retired verification script '{retired}'. "
                "Pre-commit hooks must delegate to "
                f"'lem-env validate --project {project_dir.name}'."
            ),
        )
        for retired in RETIRED_HOOK_SCRIPTS if retired in line
    ]
    violations += [
        FileValidationViolation(
            file_path=str(hook_file),
            line_number=idx,
            violation_type="missing_git_hook_target",
            message=f"Hook references nonexistent script '{py_script}'.",
        )
        for py_script in _HOOK_PY_SCRIPT_RE.findall(line)
        if not (project_dir / py_script).resolve().exists()
    ]
    return violations


def audit_git_hooks(project_dir: Path) -> list[FileValidationViolation]:
    """Verify that git hooks do not reference retired scripts or nonexistent files."""
    violations: list[FileValidationViolation] = []
    for hook_file in _hook_files(project_dir):
        try:
            content = hook_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            violations.append(FileValidationViolation(
                file_path=str(hook_file),
                line_number=0,
                violation_type="git_hook_read_error",
                message=f"Failed reading git hook: {exc}",
            ))
            continue
        for idx, line in enumerate(content.splitlines(), start=1):
            if not line.strip().startswith("#"):
                violations += _hook_line_violations(project_dir, hook_file, idx, line)
    return violations


RESIDUE_SUFFIXES = frozenset({".kaggle-partial", ".tmp", ".log"})
"""Top-level file types left behind by an interrupted download or build."""


def audit_staging_residue(
    project_dir: Path, violation_type: str, staging_msg: str, temp_msg: str,
) -> list[FileValidationViolation]:
    """Report ``staging_`` entries and temporary files at the top of ``project_dir``.

    ``staging_msg`` and ``temp_msg`` prefix the item name in the message.
    """
    found: list[FileValidationViolation] = []
    for item in project_dir.iterdir():
        if item.name.startswith((".staging_", "staging_")):
            message = f"{staging_msg}: {item.name}"
        elif item.is_file() and item.suffix.lower() in RESIDUE_SUFFIXES:
            message = f"{temp_msg}: {item.name}"
        else:
            continue
        found.append(FileValidationViolation(
            file_path=str(item), line_number=1, violation_type=violation_type, message=message,
        ))
    return found
