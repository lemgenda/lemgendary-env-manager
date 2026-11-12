"""Writing installed venv versions back into the requirements manifests.

Only exact ``==`` pins are re-pinned. Floors (``>=``), ranges, extras
(``uvicorn[standard]``) and environment markers (``; sys_platform ...``)
are kept as written, and local build tags (``+cu134``, ``+cpu``) never
reach a manifest because the index that served the wheel is a host fact,
not a requirement.
"""

import shutil
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from env_manager._logging import get_logger
from env_manager.ecosystem import ecosystem
from env_manager.upgrade.models import PROTECTED_FROM_SYNC
from env_manager.upgrade.npm_manifest_sync import sync_npm_manifest_from_project
from env_manager.venv_manager import (
    get_installed_packages,
    is_venv_valid,
)
from env_manager.workspace import workspace_root

_log = get_logger(__name__)


_PASSTHROUGH_PREFIXES = ("#", "-")
"""Comments and pip options (``--extra-index-url``, ``-e``, ``-r``) pass through."""

_PROTECTED = frozenset(canonicalize_name(name) for name in PROTECTED_FROM_SYNC)


def public_version(version: str) -> str:
    """Return ``version`` without its local build tag (``2.15.0+cu134`` -> ``2.15.0``)."""
    return version.split("+", 1)[0]


def synced_line(line: str, installed: dict[str, str]) -> str:
    """Return ``line`` re-pinned to the installed version, or unchanged.

    ``installed`` is keyed by canonical package name. Blank lines, comments,
    pip options, unparseable lines, protected packages, packages missing
    from the venv and any requirement that is not a single ``==`` pin pass
    through untouched. The marker text is kept exactly as written.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith(_PASSTHROUGH_PREFIXES):
        return line
    head, has_marker, marker_text = stripped.partition(";")
    try:
        req = Requirement(head.strip())
    except InvalidRequirement as exc:
        _log.debug("Leaving unparseable requirement line %r as written: %s", stripped, exc)
        return line
    name = canonicalize_name(req.name)
    specifiers = list(req.specifier)
    if name in _PROTECTED or len(specifiers) != 1 or specifiers[0].operator != "==":
        return line
    installed_ver = installed.get(name)
    if not installed_ver:
        return line
    version = public_version(installed_ver)
    if version == specifiers[0].version:
        return line
    extras = f"[{','.join(sorted(req.extras))}]" if req.extras else ""
    marker = f"; {marker_text.strip()}" if has_marker else ""
    return f"{req.name}{extras}=={version}{marker}"


def _sync_project(p_dir: Path, manifest_path: Path) -> tuple[bool, str]:
    """Re-pin one project's manifest from its venv and copy it into the project."""
    if not p_dir.exists():
        return False, f"Project directory not found: {p_dir}"
    if not is_venv_valid(p_dir):
        return False, "No valid venv found."
    installed: dict[str, str] = {
        str(canonicalize_name(name)): version
        for name, version in get_installed_packages(p_dir).items()
    }
    if not installed:
        return False, "No installed packages."

    existing_lines: list[str] = []
    if manifest_path.exists():
        existing_lines = manifest_path.read_text(encoding="utf-8").splitlines()
    new_lines = [synced_line(line, installed) for line in existing_lines]
    try:
        manifest_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        shutil.copy2(manifest_path, p_dir / "requirements.txt")
    except OSError as exc:
        _log.error("Manifest sync for %s failed: %s", p_dir.name, exc)
        return False, str(exc)
    return True, f"Manifest {manifest_path.name} updated and synced."


def sync_manifests_from_venvs(
    base_dir: Path | None = None,
    project: str | None = None,
) -> dict[str, tuple[bool, str]]:
    """Read installed versions from each venv and write back to the manifests.

    With ``project`` set only that project's manifests are written. The npm
    ``package.json`` of a project with an ``npm_manifest`` is copied back
    into the central manifest too (``"<folder> (npm)"`` result key).
    Packages in :data:`PROTECTED_FROM_SYNC` are passed through untouched.
    Callers run this only after the upgraded projects passed validation.
    """
    if base_dir is None:
        base_dir = workspace_root()

    manifests_dir = base_dir / ecosystem().self_folder() / "requirements"
    manifests_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, tuple[bool, str]] = {}
    for entry in ecosystem().projects:
        if project is not None and entry.folder != project:
            continue
        p_dir = base_dir / entry.folder
        if entry.requirements_manifest is not None:
            results[entry.folder] = _sync_project(
                p_dir, manifests_dir / entry.requirements_manifest,
            )
        if entry.npm_manifest is not None and (p_dir / "package.json").exists():
            results[f"{entry.folder} (npm)"] = sync_npm_manifest_from_project(
                p_dir / "package.json", manifests_dir / entry.npm_manifest,
            )
    return results
