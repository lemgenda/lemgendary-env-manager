"""npm outdated query and npm update for the Node workspace."""

import json
import shutil
import subprocess
from pathlib import Path

from env_manager._logging import get_logger
from env_manager.dependency_resolver import (
    OutdatedPackage,
)
from env_manager.utils import WINDOWS_NO_WINDOW

_log = get_logger(__name__)


def find_npm_outdated(project_dir: Path) -> list[OutdatedPackage]:
    """Return the outdated npm packages reported by ``npm outdated --json``."""
    npm_path = shutil.which("npm")
    if not npm_path:
        return []
    if not (project_dir / "package.json").exists():
        return []
    try:
        proc = subprocess.run(
            [npm_path, "outdated", "--json"],
            cwd=str(project_dir),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=90, check=False,
            creationflags=WINDOWS_NO_WINDOW,
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
        proc = subprocess.run(
            [npm_path, "update"],
            cwd=str(project_dir),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300, check=False,
            creationflags=WINDOWS_NO_WINDOW,
        )
        if proc.returncode == 0:
            return True, proc.stdout
        return False, proc.stderr or proc.stdout or "npm update failed."
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("npm update failed for %s: %s", project_dir, exc)
        return False, str(exc)
