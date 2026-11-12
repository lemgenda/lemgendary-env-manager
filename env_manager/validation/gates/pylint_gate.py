"""Pylint gate: runs Pylint over a Python project with the project's own config.

Pylint runs from the project's ``.venv`` when it has one, so imports
resolve against the project's dependencies, else from the env-manager
interpreter. Pylint must be installed there (it is pinned in every Python
manifest under ``requirements/``); when it is not, the gate fails closed
with a ``tool_error``. A project without its own Pylint configuration is
linted with the env-manager ``.pylintrc``.
"""

import json
import subprocess
from pathlib import Path

from env_manager.validation.files import SKIP_DIRS, iter_project_files
from env_manager.validation.models import FileValidationViolation, tool_failure
from env_manager.validation.tooling import get_env_manager_python, run_tool
from env_manager.venv_manager import get_venv_python_path
from env_manager.workspace import ENV_MANAGER_DIR

PYLINT_TIMEOUT_S = 600
"""Upper bound for one Pylint run over a whole project."""

PYLINT_FATAL = 1
PYLINT_USAGE_ERROR = 32
"""Pylint exit-status bits meaning Pylint itself failed (fatal message, bad usage)."""


PYLINT_CONFIG_FILES = ("pylintrc", ".pylintrc")
"""Dedicated Pylint config files Pylint discovers in the working directory."""

PYLINT_SECTION_FILES = {
    "pyproject.toml": "[tool.pylint",
    "setup.cfg": "[pylint",
    "tox.ini": "[pylint",
}
"""Shared config files that configure Pylint only when they hold its section."""

DEFAULT_PYLINTRC = ENV_MANAGER_DIR / ".pylintrc"
"""Fallback config for projects that ship none of their own."""


def has_own_pylint_config(project_dir: Path) -> bool:
    """True when Pylint would find a configuration in ``project_dir`` itself."""
    if any((project_dir / name).is_file() for name in PYLINT_CONFIG_FILES):
        return True
    for name, section in PYLINT_SECTION_FILES.items():
        path = project_dir / name
        if path.is_file() and section in path.read_text(encoding="utf-8", errors="replace"):
            return True
    return False


def rcfile_args(project_dir: Path) -> list[str]:
    """``--rcfile`` pointing at the env-manager config when the project has none."""
    if has_own_pylint_config(project_dir) or not DEFAULT_PYLINTRC.is_file():
        return []
    return [f"--rcfile={DEFAULT_PYLINTRC}"]


def has_python_sources(project_dir: Path) -> bool:
    """True when the project holds at least one scannable ``.py`` file."""
    return any(p.suffix == ".py" for p in iter_project_files(project_dir))


def _pylint_python(project_dir: Path) -> str | None:
    venv_python = get_venv_python_path(project_dir)
    if venv_python.exists():
        return str(venv_python)
    return get_env_manager_python()


def parse_pylint_json(stdout: str) -> list[FileValidationViolation]:
    """Turn Pylint ``--output-format=json`` output into violations.

    Raises ``ValueError`` when the output is not a JSON list of messages.
    """
    messages = json.loads(stdout) if stdout.strip() else []
    if not isinstance(messages, list):
        raise ValueError("Pylint JSON output is not a list")
    return [
        FileValidationViolation(
            file_path=str(msg.get("path", "")),
            line_number=int(msg.get("line") or 0),
            violation_type=f"pylint/{msg.get('symbol', msg.get('message-id', 'unknown'))}",
            message=str(msg.get("message", "")),
        )
        for msg in messages
    ]


def run_pylint(project_dir: Path) -> list[FileValidationViolation]:
    """Run Pylint recursively over ``project_dir`` and report every message."""
    if not has_python_sources(project_dir):
        return []
    python_path = _pylint_python(project_dir)
    if not python_path:
        return [tool_failure("pylint", project_dir, "no Python interpreter found")]
    cmd = [
        python_path, "-m", "pylint", "--recursive=y", "--output-format=json",
        *rcfile_args(project_dir),
        f"--ignore={','.join(sorted(SKIP_DIRS))}", ".",
    ]
    try:
        proc = run_tool(cmd, timeout=PYLINT_TIMEOUT_S, cwd=project_dir, use_pip_env=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return [tool_failure("pylint", project_dir, str(exc))]
    try:
        found = parse_pylint_json(proc.stdout)
    except ValueError as exc:
        detail = (proc.stderr or proc.stdout or "").strip() or str(exc)
        return [tool_failure("pylint", project_dir, f"exit code {proc.returncode}: {detail}")]
    if proc.returncode & (PYLINT_FATAL | PYLINT_USAGE_ERROR) and not found:
        detail = (proc.stderr or "").strip() or "no output"
        return [tool_failure("pylint", project_dir, f"exit code {proc.returncode}: {detail}")]
    for v in found:
        v.file_path = str(project_dir / v.file_path)
    return found
