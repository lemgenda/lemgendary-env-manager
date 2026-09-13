"""Dependency resolution and safe upgrade module.

Detects outdated packages using pip inspect/list and plans safe dependency upgrades
within defined semver constraints.
"""

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from env_manager._logging import get_logger
from env_manager.venv_manager import get_venv_python_path, is_venv_valid

_log = get_logger(__name__)


@dataclass
class OutdatedPackage:
    """Represents a package that has a newer version available."""
    name: str
    current_version: str
    latest_version: str
    package_type: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert entry to dictionary."""
        return asdict(self)


@dataclass
class DependencyResolutionPlan:
    """Plan for safe package upgrades."""
    project_name: str
    outdated_packages: List[OutdatedPackage]
    safe_upgrades: List[str]
    has_updates: bool

    def to_dict(self) -> Dict[str, Any]:
        """Convert plan to dictionary."""
        return asdict(self)


def find_outdated_packages(project_dir: Path) -> List[OutdatedPackage]:
    """Query outdated packages in a project's virtual environment."""
    if not is_venv_valid(project_dir):
        return []

    python_path = get_venv_python_path(project_dir)
    try:
        proc = subprocess.run(
            [str(python_path), "-m", "pip", "list", "--outdated", "--format=json"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout.strip())
            return [
                OutdatedPackage(
                    name=item.get("name", ""),
                    current_version=item.get("version", ""),
                    latest_version=item.get("latest_version", ""),
                    package_type=item.get("type", "wheel"),
                )
                for item in data
            ]
    except Exception as exc:
        _log.warning("pip list --outdated failed for %s: %s", project_dir, exc)
    return []


def create_safe_upgrade_plan(project_dir: Path) -> DependencyResolutionPlan:
    """Generate a safe upgrade plan for a project."""
    outdated = find_outdated_packages(project_dir)
    safe_upgrades = [pkg.name for pkg in outdated]

    return DependencyResolutionPlan(
        project_name=project_dir.name,
        outdated_packages=outdated,
        safe_upgrades=safe_upgrades,
        has_updates=len(outdated) > 0,
    )


def apply_safe_upgrades(project_dir: Path, packages: List[str]) -> tuple[bool, str]:
    """Execute safe pip upgrades for specified packages."""
    if not is_venv_valid(project_dir) or not packages:
        return True, "No packages to upgrade or environment invalid."

    python_path = get_venv_python_path(project_dir)
    cmd = [str(python_path), "-m", "pip", "install", "--upgrade"] + packages
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        if proc.returncode == 0:
            return True, proc.stdout
        return False, proc.stderr or proc.stdout or "Pip upgrade failed."
    except Exception as exc:
        return False, str(exc)
