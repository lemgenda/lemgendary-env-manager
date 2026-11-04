"""Reverse-dependency analysis via ``pip inspect`` and PyPI metadata for blocked candidates."""

import json
import urllib.error
import urllib.request
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import InvalidVersion, Version

from env_manager._logging import get_logger
from env_manager.resolver.models import active_requirements, normalize
from env_manager.resolver.pip_json import run_pip_json
from env_manager.venv_manager import is_venv_valid

_log = get_logger(__name__)


def get_reverse_dependencies(project_dir: Path) -> dict[str, list[tuple[str, str]]]:
    """Return {dep_norm_name: [(requiring_pkg_norm_name, specifier_str), ...]}."""
    if not is_venv_valid(project_dir):
        return {}

    # Raises PipJsonError; classify_safe_upgrades fails closed on it.
    data = run_pip_json(project_dir, ["inspect", "--local"], timeout=60)

    reverse: dict[str, list[tuple[str, str]]] = {}
    for pkg in data.get("installed", []):
        meta = pkg.get("metadata") or {}
        req_name_raw = meta.get("name") or ""
        req_name = normalize(req_name_raw)
        if not req_name:
            continue
        for r in active_requirements(meta.get("requires_dist") or [], req_name_raw):
            dep_name = normalize(r.name)
            reverse.setdefault(dep_name, []).append((req_name, str(r.specifier)))
    return reverse


def check_reverse_deps(
    pkg_name: str,
    new_version: str,
    reverse_deps: dict[str, list[tuple[str, str]]],
    exclude_requirer: set[str] | None = None,
) -> str | None:
    """Return a reason string if upgrading pkg_name would violate a requirement."""
    key = normalize(pkg_name)
    exclude = exclude_requirer or set()
    try:
        new_ver = Version(new_version.split("+", maxsplit=1)[0])
    except InvalidVersion:
        return f"cannot parse candidate version {new_version!r}"

    for req_pkg, spec in reverse_deps.get(key, []):
        if req_pkg == key or req_pkg in exclude:
            continue
        if not spec:
            continue
        try:
            spec_set = Requirement(f"x{spec}").specifier
        except InvalidRequirement as exc:
            _log.debug("Ignoring unparsable specifier %r from %s: %s", spec, req_pkg, exc)
            continue
        if new_ver not in spec_set:
            return f"{req_pkg} requires {pkg_name}{spec}"
    return None


def _fetch_pypi_requires_dist(package_name: str, version: str) -> list[str]:
    """Fetch Requires-Dist metadata for a specific package version from PyPI.

    Returns an empty list on any network or parse failure so callers degrade
    gracefully without raising.
    """
    url = f"https://pypi.org/pypi/{package_name}/{version}/json"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("info", {}).get("requires_dist") or []
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
        _log.debug("PyPI metadata fetch failed for %s==%s: %s", package_name, version, exc)
        return []


def diagnose_pinned_reason(
    pkg_name: str,
    candidate_version: str,
    current_state: dict[str, str],
) -> str:
    """Return a human-readable explanation for why a candidate version is blocked.

    Fetches the candidate's Requires-Dist from PyPI and checks each non-extras
    dependency specifier against the currently installed (pinned) version. When
    a conflict is found the first one is returned as an actionable message.
    Falls back to the generic message if PyPI is unreachable or no conflict
    can be pinpointed.
    """
    requires_dist = _fetch_pypi_requires_dist(pkg_name, candidate_version)
    for r in active_requirements(requires_dist, pkg_name):
        if not r.specifier:
            continue
        dep_norm = normalize(r.name)
        installed_ver = current_state.get(dep_norm)
        if installed_ver is None:
            continue
        try:
            installed = Version(installed_ver.split("+")[0])
        except InvalidVersion as exc:
            _log.debug("Installed %s has unparsable version %r: %s", dep_norm, installed_ver, exc)
            continue
        if installed not in r.specifier:
            return (
                f"{pkg_name} {candidate_version} requires "
                f"{r.name}{r.specifier} but {installed_ver} is installed"
            )
    return "kept pinned by another installed package's requirement"
