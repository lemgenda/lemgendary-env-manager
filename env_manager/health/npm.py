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


def compute_npm_drift(base_dir: Path) -> list[NpmDriftEntry]:
    """Identify version drift for npm packages across workspaces."""
    workspaces: list[Path] = []
    if base_dir and base_dir.is_dir():
        for child in base_dir.iterdir():
            if not child.is_dir():
                continue
            if (child / "package.json").is_file() and (child / "node_modules").is_dir():
                workspaces.append(child)

    if not workspaces:
        return []

    workspace_versions: dict[str, dict[str, str]] = {}
    workspace_specifiers: dict[str, dict[str, str]] = {}
    workspace_dev: dict[str, set[str]] = {}

    for ws in workspaces:
        workspace_versions[ws.name] = read_node_modules_versions(ws)
        specifiers, dev_names = read_package_json_specifiers(ws)
        workspace_specifiers[ws.name] = specifiers
        workspace_dev[ws.name] = dev_names

    declaration_counts: dict[str, int] = {}
    for spec_map in workspace_specifiers.values():
        for name in spec_map:
            declaration_counts[name] = declaration_counts.get(name, 0) + 1

    shared = sorted(
        name for name, count in declaration_counts.items() if count >= MIN_SHARED_DECLARATIONS
    )

    entries: list[NpmDriftEntry] = []
    for pkg in shared:
        versions: dict[str, str | None] = {}
        specs: dict[str, str | None] = {}
        observed: set[str] = set()
        is_dev = False

        for ws in workspaces:
            ver = workspace_versions.get(ws.name, {}).get(pkg)
            spec = workspace_specifiers.get(ws.name, {}).get(pkg)
            versions[ws.name] = ver
            specs[ws.name] = spec
            if ver:
                observed.add(ver)
            if pkg in workspace_dev.get(ws.name, set()):
                is_dev = True

        entries.append(
            NpmDriftEntry(
                package_name=pkg,
                versions=versions,
                specifiers=specs,
                has_drift=len(observed) > 1,
                is_dev_dependency=is_dev,
            )
        )

    return entries
