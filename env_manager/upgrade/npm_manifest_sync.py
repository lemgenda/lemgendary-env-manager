"""Copying a project's upgraded npm dependency ranges back into its central manifest.

``npm update`` rewrites the project's ``package.json``. Without this step the
central manifest in ``requirements/`` would fall behind and the next
central-to-project sync would refuse the now newer project file as drift.
"""

import json
from pathlib import Path
from typing import Any

from env_manager._logging import get_logger

_log = get_logger(__name__)

NPM_DEPENDENCY_SECTIONS = (
    "dependencies",
    "devDependencies",
    "peerDependencies",
    "optionalDependencies",
)
"""``package.json`` sections whose ranges flow back from the project."""


def merge_dependency_sections(central: dict[str, Any], project: dict[str, Any]) -> dict[str, Any]:
    """Return ``central`` with every dependency section taken from ``project``.

    Other keys, including the release ``version``, stay as the central
    manifest has them. A section the project dropped is dropped too.
    """
    merged = {k: v for k, v in central.items() if k not in NPM_DEPENDENCY_SECTIONS}
    for section in NPM_DEPENDENCY_SECTIONS:
        if section in project:
            merged[section] = project[section]
    ordered = [k for k in central if k in merged]
    ordered += [k for k in merged if k not in central]
    return {key: merged[key] for key in ordered}


def sync_npm_manifest_from_project(project_pkg: Path, central_manifest: Path) -> tuple[bool, str]:
    """Write ``project_pkg``'s dependency sections into ``central_manifest``."""
    try:
        project = json.loads(project_pkg.read_text(encoding="utf-8"))
        current = central_manifest.read_text(encoding="utf-8") if central_manifest.exists() else ""
        central = json.loads(current) if current else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _log.error("Cannot read %s or %s: %s", project_pkg, central_manifest, exc)
        return False, f"Cannot read npm manifests: {exc}"
    if not (isinstance(project, dict) and isinstance(central, dict)):
        return False, f"{project_pkg.name} or {central_manifest.name} is not a JSON object"
    text = json.dumps(merge_dependency_sections(central, project), indent=2,
                      ensure_ascii=False) + "\n"
    if text == current:
        return True, f"{central_manifest.name} already matches {project_pkg}."
    try:
        central_manifest.write_text(text, encoding="utf-8")
    except OSError as exc:
        _log.error("Cannot write %s: %s", central_manifest, exc)
        return False, f"Cannot write {central_manifest.name}: {exc}"
    return True, f"{central_manifest.name} updated from {project_pkg}."
