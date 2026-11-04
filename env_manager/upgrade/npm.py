"""npm outdated query and npm update for the Node workspace."""

import json
import shutil
import subprocess
from pathlib import Path

from env_manager._logging import get_logger
from env_manager.dependency_resolver import (
    OutdatedPackage,
)
from env_manager.utils import run_command_simple

_log = get_logger(__name__)


def find_npm_outdated(project_dir: Path) -> list[OutdatedPackage]:
    """Return the outdated npm packages reported by ``npm outdated --json``."""
    npm_path = shutil.which("npm")
    if not npm_path:
        return []
    if not (project_dir / "package.json").exists():
        return []
    try:
        proc = run_command_simple([npm_path, "outdated", "--json"], timeout=90, cwd=project_dir)
        # npm outdated exits 1 whenever something is outdated; only output tells.
        if not proc.stdout.strip() and proc.returncode != 0:
            _log.warning(
                "npm outdated for %s exited with %d: %s",
                project_dir, proc.returncode, proc.stderr.strip(),
            )
        if proc.stdout.strip():
            data = json.loads(proc.stdout.strip())
            if not isinstance(data, dict):
                _log.warning("npm outdated for %s returned non-object JSON", project_dir)
                return []
            return [
                OutdatedPackage(
                    name=name,
                    current_version=str(info.get("current", "")),
                    latest_version=str(info.get("latest", "")),
                    package_type="npm",
                )
                for name, info in data.items()
                if isinstance(info, dict)
            ]
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        _log.warning("npm outdated query failed for %s: %s", project_dir, exc)
    return []


def apply_npm_update(project_dir: Path) -> tuple[bool, str]:
    """Run ``npm update`` in ``project_dir``; return ``(ok, output)``."""
    npm_path = shutil.which("npm")
    if not npm_path:
        return False, "npm not found on PATH."
    try:
        proc = run_command_simple([npm_path, "update"], timeout=300, cwd=project_dir)
        if proc.returncode == 0:
            return True, proc.stdout
        return False, proc.stderr or proc.stdout or "npm update failed."
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("npm update failed for %s: %s", project_dir, exc)
        return False, str(exc)
