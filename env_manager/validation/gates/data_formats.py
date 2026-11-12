"""YAML and JSON lint gates."""

import json
import subprocess
from pathlib import Path

from env_manager.validation.models import FileValidationViolation, tool_failure, unexplained_exit
from env_manager.validation.tooling import get_env_manager_python, regex_violations, run_tool
from env_manager.workspace import ENV_MANAGER_DIR


def run_yamllint(project_dir: Path) -> list[FileValidationViolation]:
    """Run yamllint on all YAML files in the project using env-manager venv."""
    violations: list[FileValidationViolation] = []

    skip_dirs = {
        ".git", ".venv", "node_modules", "__pycache__", "dist", "build", "target",
        ".pytest_cache", "raw-sets", "checkpoints", "weights", ".agents", "data",
        "scratch", ".cache", "export", "runs", ".lemtrain_server", ".lgd_server",
        ".env_server",
    }
    yaml_files = [
        f for f in project_dir.rglob("*.yaml")
        if not any(part in skip_dirs for part in f.parts)
    ] + [
        f for f in project_dir.rglob("*.yml")
        if not any(part in skip_dirs for part in f.parts)
    ]

    if not yaml_files:
        return violations

    python_path = get_env_manager_python()
    if not python_path:
        return [tool_failure("yamllint", project_dir, "no Python interpreter found")]

    mgr_dir = ENV_MANAGER_DIR
    cfg = None
    for candidate in [
        project_dir / ".yamllint.yaml",
        project_dir / ".yamllint",
        mgr_dir / ".yamllint.yaml",
        mgr_dir / ".yamllint",
        project_dir.parent / ".yamllint.yaml",
        project_dir.parent / ".yamllint",
    ]:
        if candidate.exists():
            cfg = str(candidate)
            break

    cmd = [python_path, "-m", "yamllint", "-f", "parsable"]
    if cfg:
        cmd.extend(["-c", cfg])
    cmd.extend([str(f) for f in yaml_files])

    try:
        proc = run_tool(cmd, timeout=120, cwd=project_dir, use_pip_env=True)
        # yamllint parsable format: path:line:col: [level] message (rule)
        violations.extend(regex_violations(
            proc.stdout,
            r"^(?P<path>.+?):(?P<line>\d+):\d+: \[(?P<rule>\w+)\] (?P<msg>.+)$",
            "yaml_",
        ))
        # yamllint exits 1 when it finds problems and 2 on a config or tool
        # error; "No module named yamllint" also exits 1 with nothing parsed.
        if proc.returncode not in (0, 1):
            violations.append(tool_failure(
                "yamllint", project_dir,
                f"exit code {proc.returncode}: {(proc.stderr or '').strip()}",
            ))
        else:
            violations.extend(unexplained_exit("yamllint", project_dir, proc, len(violations)))
    except (OSError, subprocess.SubprocessError) as exc:
        violations.append(tool_failure("yamllint", project_dir, str(exc)))
    return violations


def run_jsonlint(project_dir: Path) -> list[FileValidationViolation]:
    """Audit all JSON configuration and manifest files for syntax and structure errors."""
    violations: list[FileValidationViolation] = []
    skip_dirs = {
        ".git", ".venv", "node_modules", "__pycache__", "dist", "build", "target",
        ".pytest_cache", "raw-sets", "checkpoints", "weights", ".agents", "data",
        "scratch", ".cache", ".antigravity", "export", "runs", ".lemtrain_server",
        ".lgd_server", ".env_server",
    }
    for root, dirs, files in Path(project_dir).walk():
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for f in files:
            if f.endswith(".json"):
                f_path = root / f
                try:
                    content = f_path.read_text(encoding="utf-8")
                    json.loads(content)
                except json.JSONDecodeError as exc:
                    violations.append(
                        FileValidationViolation(
                            file_path=str(f_path),
                            line_number=exc.lineno,
                            violation_type="json_syntax_error",
                            message=f"{exc.msg} (col {exc.colno})",
                        )
                    )
                except (OSError, ValueError) as exc:
                    violations.append(
                        FileValidationViolation(
                            file_path=str(f_path),
                            line_number=0,
                            violation_type="json_read_error",
                            message=str(exc),
                        )
                    )
    return violations
