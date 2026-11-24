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


def sync_package_json_from_lock(project_dir: Path) -> list[tuple[str, str, str]]:
    """Update version specs in package.json to match installed lockfile versions.

    npm update updates packages in node_modules and package-lock.json but preserves
    the declared range in package.json unless explicitly synchronized. This helper
    raises declared lower bounds (e.g. ^1.1.2 -> ^1.2.1, ~2.12.0 -> ~2.12.5) to
    match the newly installed packages.
    """
    pkg_file = project_dir / "package.json"
    lock_file = project_dir / "package-lock.json"
    if not pkg_file.is_file() or not lock_file.is_file():
        return []
    try:
        pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
        lock = json.loads(lock_file.read_text(encoding="utf-8")).get("packages", {})
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _log.warning("Failed to parse package.json/package-lock.json for %s: %s", project_dir, exc)
        return []
    updated: list[tuple[str, str, str]] = []
    for dep_type in ("dependencies", "devDependencies", "peerDependencies"):
        if dep_type not in pkg or not isinstance(pkg[dep_type], dict):
            continue
        for name, spec in pkg[dep_type].items():
            installed = lock.get(f"node_modules/{name}", {}).get("version")
            if not installed:
                continue
            prefix = ""
            if spec.startswith("^"):
                prefix = "^"
            elif spec.startswith("~"):
                prefix = "~"
            current_ver = spec.lstrip("^~")
            if current_ver != installed:
                new_spec = f"{prefix}{installed}"
                pkg[dep_type][name] = new_spec
                updated.append((name, spec, new_spec))
    if updated:
        try:
            pkg_file.write_text(
                json.dumps(pkg, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            _log.error("Failed to write updated package.json for %s: %s", project_dir, exc)
    return updated


def apply_npm_update(project_dir: Path) -> tuple[bool, str]:
    """Run ``npm update`` in ``project_dir`` and sync package.json; return ``(ok, output)``."""
    npm_path = shutil.which("npm")
    if not npm_path:
        return False, "npm not found on PATH."
    try:
        proc = run_command_simple([npm_path, "update"], timeout=300, cwd=project_dir)
        if proc.returncode == 0:
            sync_package_json_from_lock(project_dir)
            return True, proc.stdout
        return False, proc.stderr or proc.stdout or "npm update failed."
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("npm update failed for %s: %s", project_dir, exc)
        return False, str(exc)
