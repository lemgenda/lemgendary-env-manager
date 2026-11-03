"""npm drift across the Node workspaces."""

import json
from pathlib import Path
from typing import Any

from env_manager._logging import get_logger
from env_manager.health.models import MIN_SHARED_DECLARATIONS, NpmDriftEntry

_log = get_logger(__name__)


def read_json_object(path: Path) -> dict[str, Any]:
    """Return the JSON object stored at ``path``, or ``{}`` when unavailable.

    A missing file is an expected condition and returns ``{}`` silently. An
    unreadable file, invalid JSON or a non-object document is logged.
    """
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        _log.warning("Cannot read JSON from %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        _log.warning("JSON document at %s is not an object", path)
        return {}
    return data


def read_node_modules_versions(workspace_dir: Path) -> dict[str, str]:
    """Return the installed version of each package in ``node_modules``."""
    installed: dict[str, str] = {}
    node_modules = workspace_dir / "node_modules"
    if not node_modules.is_dir():
        return installed

    try:
        for entry in node_modules.iterdir():
            if not entry.is_dir():
                continue
            name = entry.name
            if name.startswith("@"):
                for scoped in entry.iterdir():
                    ver = read_json_object(scoped / "package.json").get("version")
                    if ver:
                        installed[f"{name}/{scoped.name}"] = str(ver)
            else:
                ver = read_json_object(entry / "package.json").get("version")
                if ver:
                    installed[name] = str(ver)
    except OSError as exc:
        _log.warning("node_modules walk failed for %s: %s", workspace_dir, exc)

    return installed


def read_package_json_specifiers(
    workspace_dir: Path,
) -> tuple[dict[str, str], set[str]]:
    """Return the declared specifiers of ``package.json`` and the dev-only package names."""
    specifiers: dict[str, str] = {}
    dev_names: set[str] = set()
    pkg_json = workspace_dir / "package.json"
    data = read_json_object(pkg_json)
    if not data:
        return specifiers, dev_names

    for name, spec in (data.get("dependencies") or {}).items():
        specifiers[name] = str(spec)
    for name, spec in (data.get("devDependencies") or {}).items():
        specifiers[name] = str(spec)
        dev_names.add(name)

    return specifiers, dev_names


def _installed_workspaces(base_dir: Path) -> list[Path]:
    """Return workspace folders that have both a package.json and node_modules."""
    if not base_dir or not base_dir.is_dir():
        return []
    return [
        child
        for child in base_dir.iterdir()
        if child.is_dir()
        and (child / "package.json").is_file()
        and (child / "node_modules").is_dir()
    ]


def _drift_entry(
    pkg: str,
    workspaces: list[Path],
    installed: dict[str, dict[str, str]],
    declared: dict[str, tuple[dict[str, str], set[str]]],
) -> NpmDriftEntry:
    """Build the drift row for one package across every workspace."""
    versions = {ws.name: installed[ws.name].get(pkg) for ws in workspaces}
    observed = {ver for ver in versions.values() if ver}
    return NpmDriftEntry(
        package_name=pkg,
        versions=versions,
        specifiers={ws.name: declared[ws.name][0].get(pkg) for ws in workspaces},
        has_drift=len(observed) > 1,
        is_dev_dependency=any(pkg in declared[ws.name][1] for ws in workspaces),
    )


def compute_npm_drift(base_dir: Path) -> list[NpmDriftEntry]:
    """Identify version drift for npm packages across workspaces."""
    workspaces = _installed_workspaces(base_dir)
    if not workspaces:
        return []

    installed = {ws.name: read_node_modules_versions(ws) for ws in workspaces}
    declared = {ws.name: read_package_json_specifiers(ws) for ws in workspaces}

    declaration_counts: dict[str, int] = {}
    for spec_map, _dev in declared.values():
        for name in spec_map:
            declaration_counts[name] = declaration_counts.get(name, 0) + 1

    shared = sorted(
        name for name, count in declaration_counts.items() if count >= MIN_SHARED_DECLARATIONS
    )
    return [_drift_entry(pkg, workspaces, installed, declared) for pkg in shared]
