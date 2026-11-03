"""Requirements manifest synchronization and parsing module.

Maintains centralized manifests and synchronizes them to target project roots,
ensuring environment markers and index URLs are properly configured.

Single source of truth (review finding D-06): the central manifests in
``lemgendary-env-manager/requirements/`` are the only files meant to be
edited. Sibling ``requirements.txt`` / ``package.json`` files are generated
copies. Sync is one-way (central -> sibling), but it never takes anything
away from a sibling: when the central copy would lower a sibling's version
floor or drop a package the sibling still lists, the sibling is left
untouched and the drift is reported (``ok=False`` with a ``DRIFT:`` message)
so the owner can bring the central manifest up to date first.

The project's own release identity (``package.json`` ``version``, see
:data:`NPM_RELEASE_KEYS`) is not dependency information: the central npm
manifest does not hold it, drift ignores it and sync keeps the sibling's value.
"""

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from env_manager._logging import get_logger
from env_manager.ecosystem import ecosystem
from env_manager.workspace import workspace_root

_log = get_logger(__name__)

NPM_RELEASE_KEYS = frozenset({"version"})
"""``package.json`` fields the project owns per release; never compared or synced."""


@dataclass
class ParsedRequirement:
    """Individual parsed requirement entry."""
    raw_line: str
    name: str
    specifier: str
    marker: str | None = None
    is_editable: bool = False
    is_index_url: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert entry to dictionary."""
        return asdict(self)


def parse_requirements_file(file_path: Path) -> list[ParsedRequirement]:
    """Parse requirements file into structured entries."""
    if not file_path.exists():
        return []

    results: list[ParsedRequirement] = []
    lines = file_path.read_text(encoding="utf-8").splitlines()

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("--extra-index-url") or stripped.startswith("-i "):
            results.append(
                ParsedRequirement(
                    raw_line=stripped,
                    name="__index_url__",
                    specifier=stripped,
                    is_index_url=True,
                )
            )
            continue

        if stripped.startswith("-e "):
            results.append(
                ParsedRequirement(
                    raw_line=stripped,
                    name=stripped.replace("-e ", "").strip(),
                    specifier="",
                    is_editable=True,
                )
            )
            continue

        try:
            req = Requirement(stripped)
            marker_str = str(req.marker) if req.marker else None
            spec_str = str(req.specifier) if req.specifier else ""
            results.append(
                ParsedRequirement(
                    raw_line=stripped,
                    name=req.name.lower(),
                    specifier=spec_str,
                    marker=marker_str,
                )
            )
            continue
        except InvalidRequirement as exc:
            _log.debug("packaging.Requirement parse failed for line '%s', using fallback "
                       "parser: %s", stripped, exc)

        # Fallback regex parser
        match = re.match(r"^([A-Za-z0-9_\-\.\[\]]+)\s*([<>=!~]+[^;]*)?(?:;\s*(.*))?$", stripped)
        if match:
            name = match.group(1).lower()
            spec = (match.group(2) or "").strip()
            marker = (match.group(3) or "").strip() or None
            results.append(
                ParsedRequirement(
                    raw_line=stripped,
                    name=name,
                    specifier=spec,
                    marker=marker,
                )
            )
        else:
            results.append(
                ParsedRequirement(
                    raw_line=stripped,
                    name=stripped.lower(),
                    specifier="",
                )
            )
    return results


# ─── Downgrade guard (review finding D-06) ─────────────────────────────────

DRIFT_PREFIX = "DRIFT:"

# Operators whose version is a lower bound of the allowed range.
_FLOOR_OPERATORS = {"==", "===", ">=", "~=", ">"}


def _spec_floor(specifier: str) -> Version | None:
    """Return the lowest version a specifier allows, or None if unbounded/unknown."""
    if not specifier:
        return None
    try:
        spec_set = SpecifierSet(specifier)
    except InvalidSpecifier:
        _log.warning("Cannot parse version specifier '%s'; treating as unbounded", specifier)
        return None
    floors: list[Version] = []
    for spec in spec_set:
        if spec.operator not in _FLOOR_OPERATORS:
            continue
        raw = spec.version[:-2] if spec.version.endswith(".*") else spec.version
        try:
            floors.append(Version(raw))
        except InvalidVersion:
            _log.warning("Cannot parse version '%s' in '%s'", raw, specifier)
    return max(floors) if floors else None


def _requirement_index(
    entries: list[ParsedRequirement],
) -> dict[tuple[str, str], ParsedRequirement]:
    """Index package entries by (normalized name, marker); skip index URLs and editables."""
    index: dict[tuple[str, str], ParsedRequirement] = {}
    for entry in entries:
        if entry.is_index_url or entry.is_editable:
            continue
        name = re.sub(r"[-_.]+", "-", entry.name.split("[", 1)[0]).lower()
        index[(name, entry.marker or "")] = entry
    return index


def find_requirement_drift(central_path: Path, sibling_path: Path) -> list[str]:
    """List every way copying ``central_path`` over ``sibling_path`` would take something away.

    Reported cases (anything else, e.g. equal pins or central upgrades, is not drift):

    * the central floor version is lower than the sibling's (``torch==2.14`` over ``==2.15``);
    * the sibling pins a floor but the central entry has none (pin would be dropped);
    * the sibling lists a package the central manifest does not.
    """
    central = _requirement_index(parse_requirements_file(central_path))
    sibling = _requirement_index(parse_requirements_file(sibling_path))
    drift: list[str] = []
    for key, sib_entry in sibling.items():
        label = sib_entry.name + (f" ; {key[1]}" if key[1] else "")
        cen_entry = central.get(key)
        if cen_entry is None:
            pin = sib_entry.specifier or "unpinned"
            drift.append(f"{label}: listed in sibling ({pin}) but missing from central")
            continue
        sib_floor = _spec_floor(sib_entry.specifier)
        if sib_floor is None:
            continue
        cen_floor = _spec_floor(cen_entry.specifier)
        if cen_floor is None:
            drift.append(
                f"{label}: central '{cen_entry.specifier or 'unpinned'}' "
                f"drops sibling pin '{sib_entry.specifier}'"
            )
        elif cen_floor < sib_floor:
            drift.append(
                f"{label}: central '{cen_entry.specifier}' "
                f"is older than sibling '{sib_entry.specifier}'"
            )
    return drift


def _npm_floor(spec: str) -> Version | None:
    """Return the base version of an npm range such as ``^8.71.2`` or ``25.0.1``."""
    match = re.match(r"^\s*[\^~>=v]*\s*(\d+(?:\.\d+)*)", spec)
    if not match:
        return None
    try:
        return Version(match.group(1))
    except InvalidVersion as exc:
        _log.debug("Cannot parse npm version floor from %r: %s", spec, exc)
        return None


def find_package_json_drift(central: dict[str, Any], sibling: dict[str, Any]) -> list[str]:
    """npm equivalent of :func:`find_requirement_drift`.

    The project's own ``version`` (:data:`NPM_RELEASE_KEYS`) is not compared.
    """
    drift: list[str] = []
    for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        sib_deps = sibling.get(section) or {}
        cen_deps = central.get(section) or {}
        for name, sib_spec in sib_deps.items():
            if name not in cen_deps:
                drift.append(
                    f"{section}.{name}: listed in sibling ({sib_spec}) but missing from central"
                )
                continue
            sib_floor, cen_floor = _npm_floor(str(sib_spec)), _npm_floor(str(cen_deps[name]))
            if sib_floor is not None and (cen_floor is None or cen_floor < sib_floor):
                drift.append(
                    f"{section}.{name}: central '{cen_deps[name]}' "
                    f"is older than sibling '{sib_spec}'"
                )
    return drift


def _drift_message(target: Path, drift: list[str]) -> str:
    return (
        f"{DRIFT_PREFIX} {target} left unchanged; central manifest would remove or downgrade "
        f"{len(drift)} entr{'y' if len(drift) == 1 else 'ies'}: " + "; ".join(drift)
        + ". Update the central manifest first."
    )


def sync_manifest_to_project(
    manifest_path: Path,
    target_project_dir: Path,
) -> tuple[bool, str]:
    """Synchronize a centralized requirements manifest into a project directory safely.

    Refuses (returns ``False`` with a ``DRIFT:`` message, file untouched) when
    the write would downgrade or drop a sibling pin; see :func:`find_requirement_drift`.
    """
    if not manifest_path.exists():
        return False, f"Manifest file not found: {manifest_path}"

    target_req_path = target_project_dir / "requirements.txt"
    if target_req_path.exists():
        drift = find_requirement_drift(manifest_path, target_req_path)
        if drift:
            message = _drift_message(target_req_path, drift)
            _log.warning(message)
            return False, message
    try:
        content = manifest_path.read_text(encoding="utf-8")

        clean_lines = []
        for line in content.splitlines():
            stripped = line.strip()
            if stripped:
                # Normalise "spec;marker" to "spec; marker".
                if ";" in stripped:
                    parts = stripped.split(";", 1)
                    clean_lines.append(f"{parts[0].strip()}; {parts[1].strip()}")
                else:
                    clean_lines.append(stripped)

        target_req_path.write_text("\n".join(clean_lines) + "\n", encoding="utf-8")
        return True, f"Synchronized sanitized {manifest_path.name} to {target_req_path}."
    except OSError as exc:
        _log.error("Failed to synchronize %s to %s: %s", manifest_path, target_req_path, exc)
        return False, f"Failed to synchronize manifest: {exc}"


def merge_npm_manifest(central: dict[str, Any], sibling: dict[str, Any]) -> dict[str, Any]:
    """The central manifest with the sibling's own release fields (:data:`NPM_RELEASE_KEYS`).

    Keys keep the sibling's order; keys only the central manifest has follow.
    """
    def value(key: str) -> Any:
        return sibling[key] if key in NPM_RELEASE_KEYS else central[key]

    def wanted(key: str) -> bool:
        return key in (sibling if key in NPM_RELEASE_KEYS else central)

    keys = [k for k in sibling if wanted(k)]
    keys += [k for k in central if k not in sibling and wanted(k)]
    return {key: value(key) for key in keys}


def _sync_npm_manifest(proj_name: str, manifest: Path, proj_dir: Path) -> tuple[bool, str]:
    """Write a centralized package.json manifest into a project unless the project drifted.

    The project's own ``version`` is kept (see :func:`merge_npm_manifest`).
    """
    target_pkg = proj_dir / "package.json"
    try:
        central = json.loads(manifest.read_text(encoding="utf-8"))
        current = target_pkg.read_bytes().decode("utf-8") if target_pkg.exists() else None
        sibling = json.loads(current) if current is not None else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return False, f"Cannot compare {manifest.name} with {target_pkg}: {exc}"
    if not (isinstance(central, dict) and isinstance(sibling, dict)):
        return False, f"Cannot compare {manifest.name} with {target_pkg}: not a JSON object"
    drift = find_package_json_drift(central, sibling)
    if drift:
        message = _drift_message(target_pkg, drift)
        _log.warning(message)
        return False, message
    merged = merge_npm_manifest(central, sibling)
    newline = "\r\n" if current is not None and "\r\n" in current else "\n"
    text = (json.dumps(merged, indent=2, ensure_ascii=False) + "\n").replace("\n", newline)
    try:
        # Avoid touching package.json if content is already identical
        if current != text:
            target_pkg.write_bytes(text.encode("utf-8"))
    except OSError as exc:
        _log.error("Failed to synchronize %s to %s: %s", manifest, target_pkg, exc)
        return False, f"Failed to synchronize {proj_name} npm manifest: {exc}"
    return True, f"Synchronized {manifest.name} to {target_pkg}."


def sync_all_manifests(base_dir: Path | None = None) -> dict[str, tuple[bool, str]]:
    """Synchronize all centralized manifests to their respective sibling projects."""
    if base_dir is None:
        base_dir = workspace_root()

    eco = ecosystem()
    manifests_dir = base_dir / eco.self_folder() / "requirements"

    # Manifest file names per project come from ecosystem.json
    # (``projects[].requirements_manifest`` and ``projects[].npm_manifest``).
    results: dict[str, tuple[bool, str]] = {}
    for project in eco.projects:
        if project.requirements_manifest is None:
            continue
        proj_dir = base_dir / project.folder
        if proj_dir.is_dir():
            results[project.folder] = sync_manifest_to_project(
                manifests_dir / project.requirements_manifest, proj_dir,
            )
        else:
            results[project.folder] = (
                False, f"Project directory does not exist: {proj_dir}",
            )

    for project in eco.projects:
        if project.npm_manifest is None:
            continue
        manifest = manifests_dir / project.npm_manifest
        proj_dir = base_dir / project.folder
        if manifest.exists() and proj_dir.is_dir():
            results[f"{project.folder} (npm)"] = _sync_npm_manifest(
                project.folder, manifest, proj_dir,
            )

    return results
