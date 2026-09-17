"""Dependency resolution and safe upgrade module."""

import json
import os
import re
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import InvalidVersion, Version

from env_manager._logging import get_logger
from env_manager.utils import pip_env, run_pip_with_recovery
from env_manager.venv_manager import get_venv_python_path, is_venv_valid

_log = get_logger(__name__)

_BOOTSTRAP_PACKAGES = {"pip", "wheel", "setuptools", "pkg_resources"}


@dataclass
class OutdatedPackage:
    """Represents a package that has a newer version available."""
    name: str
    current_version: str
    latest_version: str
    package_type: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DependencyResolutionPlan:
    """Plan for safe package upgrades."""
    project_name: str
    outdated_packages: List[OutdatedPackage]
    safe_upgrades: List[str]
    has_updates: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─── Normalization helpers ──────────────────────────────────────────────────

def _normalize(name: str) -> str:
    return name.lower().replace("-", "_").replace(".", "_")


def _version_lt(a: str, b: str) -> bool:
    try:
        return Version(a.split("+")[0]) < Version(b.split("+")[0])
    except (InvalidVersion, AttributeError):
        return False


# ─── Installed-set helpers ──────────────────────────────────────────────────

def venv_package_count(project_dir: Path) -> int:
    """Return number of non-bootstrap packages installed in the venv."""
    if not is_venv_valid(project_dir):
        return 0
    python_path = get_venv_python_path(project_dir)
    try:
        proc = run_pip_with_recovery(
            python_path, ["list", "--format=json"], timeout=30,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return 0
        data = json.loads(proc.stdout.strip())
        names = {item.get("name", "").lower() for item in data}
        return len(names - _BOOTSTRAP_PACKAGES)
    except Exception:
        return 0


def venv_has_real_packages(project_dir: Path) -> bool:
    return venv_package_count(project_dir) > 0


def _installed_package_versions(project_dir: Path) -> Dict[str, str]:
    """Return {normalized_name: version} for every installed package."""
    if not is_venv_valid(project_dir):
        return {}
    python_path = get_venv_python_path(project_dir)
    try:
        proc = run_pip_with_recovery(
            python_path, ["list", "--format=json"], timeout=30,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout.strip())
            return {_normalize(item["name"]): item["version"] for item in data}
    except Exception as exc:
        _log.debug("pip list failed for %s: %s", project_dir, exc)
    return {}


# ─── Reverse-dependency analysis (via pip inspect) ──────────────────────────

def _get_reverse_dependencies(project_dir: Path) -> Dict[str, List[Tuple[str, str]]]:
    """Return {dep_norm_name: [(requiring_pkg_norm_name, specifier_str), ...]}."""
    if not is_venv_valid(project_dir):
        return {}

    python_path = get_venv_python_path(project_dir)
    try:
        proc = run_pip_with_recovery(
            python_path, ["inspect", "--local"], timeout=60,
        )
        if proc.returncode != 0 or not proc.stdout.strip():
            return {}
        data = json.loads(proc.stdout.strip())
    except Exception as exc:
        _log.debug("pip inspect failed for %s: %s", project_dir, exc)
        return {}

    reverse: Dict[str, List[Tuple[str, str]]] = {}
    for pkg in data.get("installed", []):
        meta = pkg.get("metadata") or {}
        req_name_raw = meta.get("name") or ""
        req_name = _normalize(req_name_raw)
        if not req_name:
            continue
        for req_str in meta.get("requires_dist") or []:
            try:
                r = Requirement(req_str)
            except InvalidRequirement:
                continue
            if r.marker is not None:
                try:
                    if not r.marker.evaluate():
                        continue
                except Exception:
                    continue
            dep_name = _normalize(r.name)
            reverse.setdefault(dep_name, []).append((req_name, str(r.specifier)))
    return reverse


def _check_reverse_deps(
    pkg_name: str,
    new_version: str,
    reverse_deps: Dict[str, List[Tuple[str, str]]],
    exclude_requirer: Optional[Set[str]] = None,
) -> Optional[str]:
    """Return a reason string if upgrading pkg_name would violate a requirement."""
    key = _normalize(pkg_name)
    exclude = exclude_requirer or set()
    try:
        new_ver = Version(new_version.split("+")[0])
    except InvalidVersion:
        return f"cannot parse candidate version {new_version!r}"

    for req_pkg, spec in reverse_deps.get(key, []):
        if req_pkg == key or req_pkg in exclude:
            continue
        if not spec:
            continue
        try:
            spec_set = Requirement(f"x{spec}").specifier
        except InvalidRequirement:
            continue
        if new_ver not in spec_set:
            return f"{req_pkg} requires {pkg_name}{spec}"
    return None


# ─── Raw outdated query ─────────────────────────────────────────────────────

def find_outdated_packages(project_dir: Path) -> List[OutdatedPackage]:
    """Query outdated packages. Returns raw results (no safety filtering)."""
    if not is_venv_valid(project_dir):
        return []

    python_path = get_venv_python_path(project_dir)
    try:
        proc = run_pip_with_recovery(
            python_path, ["list", "--outdated", "--format=json"], timeout=90,
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


# ─── pip report parsing ─────────────────────────────────────────────────────

def _parse_pip_report(stdout: str) -> Optional[Dict[str, Any]]:
    idx = stdout.find("{")
    if idx < 0:
        return None
    try:
        return json.loads(stdout[idx:])
    except json.JSONDecodeError:
        return None


def _parse_would_install_text(stdout: str) -> Dict[str, str]:
    for line in stdout.splitlines():
        line = line.strip()
        if not line.lower().startswith("would install"):
            continue
        rest = line[len("Would install"):].strip()
        result: Dict[str, str] = {}
        for token in rest.split():
            for i in range(len(token) - 1, -1, -1):
                if token[i] == "-" and i + 1 < len(token) and token[i + 1].isdigit():
                    result[_normalize(token[:i])] = token[i + 1:]
                    break
        return result
    return {}


# ─── Constraint-preserving dry-run ──────────────────────────────────────────

def _write_constraints_file(pins: Dict[str, str]) -> str:
    fd, path = tempfile.mkstemp(prefix="pip_constraints_", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for name, version in pins.items():
                f.write(f"{name}=={version}\n")
    except Exception:
        try:
            os.unlink(path)
        except OSError as exc:
            _log.debug("Failed unlinking temp constraints file %s: %s", path, exc)
        raise
    return path


def constraint_preserving_dry_run(
    project_dir: Path,
    targets: List[str],
    current_state: Optional[Dict[str, str]] = None,
    index_url: Optional[str] = None,
    extra_index_url: Optional[str] = None,
    timeout: int = 300,
) -> Tuple[bool, Dict[str, str], str]:
    """Dry-run upgrading ``targets`` while pinning everything else."""
    if not is_venv_valid(project_dir) or not targets:
        return True, {}, "nothing to do"

    if current_state is None:
        current_state = _installed_package_versions(project_dir)

    target_norm = {_normalize(t) for t in targets}
    constraint_pins = {
        name: version
        for name, version in current_state.items()
        if name not in target_norm
    }

    try:
        constraint_path = _write_constraints_file(constraint_pins)
    except Exception as exc:
        return False, {}, f"failed to write constraints: {exc}"

    python_path = get_venv_python_path(project_dir)
    args = [
        "install",
        "--dry-run",
        "--upgrade",
        "--upgrade-strategy", "only-if-needed",
        "--constraint", constraint_path,
        "--report", "-",
        "--quiet",
    ]
    if index_url:
        args.extend(["--index-url", index_url])
    if extra_index_url:
        args.extend(["--extra-index-url", extra_index_url])
    args.extend(targets)

    try:
        proc = run_pip_with_recovery(python_path, args, timeout=timeout)
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-8:]
            return False, {}, " | ".join(tail) or "pip dry-run failed"

        report = _parse_pip_report(proc.stdout)
        if report is None:
            would_install = _parse_would_install_text(proc.stdout)
        else:
            would_install = {}
            for item in report.get("install", []):
                meta = item.get("metadata") or {}
                name = meta.get("name")
                version = meta.get("version")
                if name and version:
                    would_install[_normalize(name)] = version

        changed: Dict[str, str] = {}
        for name, new_ver in would_install.items():
            old_ver = current_state.get(name)
            if old_ver is None or old_ver != new_ver:
                changed[name] = new_ver

        return True, changed, "ok"
    except Exception as exc:
        return False, {}, str(exc)
    finally:
        try:
            os.unlink(constraint_path)
        except OSError as exc:
            _log.debug("Failed unlinking constraint file %s: %s", constraint_path, exc)


# ─── Safety classifier ──────────────────────────────────────────────────────

def classify_safe_upgrades(
    project_dir: Path,
    candidates: List[OutdatedPackage],
    cuda_index_url: Optional[str] = None,
) -> Tuple[List[OutdatedPackage], List[Tuple[OutdatedPackage, str]]]:
    """Split outdated candidates into (safe, blocked)."""
    if not candidates or not is_venv_valid(project_dir):
        return [], [(pkg, "invalid venv") for pkg in candidates]

    current_state = _installed_package_versions(project_dir)
    reverse_deps = _get_reverse_dependencies(project_dir)

    torch_candidates = [pkg for pkg in candidates if pkg.package_type == "wheel-cuda"]
    normal_candidates = [pkg for pkg in candidates if pkg.package_type != "wheel-cuda"]

    safe: List[OutdatedPackage] = []
    blocked: List[Tuple[OutdatedPackage, str]] = []

    if normal_candidates:
        passed_stage1: List[OutdatedPackage] = []
        for pkg in normal_candidates:
            reason = _check_reverse_deps(
                pkg.name, pkg.latest_version, reverse_deps,
                exclude_requirer={_normalize(pkg.name)},
            )
            if reason is None:
                passed_stage1.append(pkg)
            else:
                blocked.append((pkg, reason))

        if passed_stage1:
            names = [pkg.name for pkg in passed_stage1]
            ok, changed, msg = constraint_preserving_dry_run(
                project_dir, names, current_state=current_state,
                extra_index_url=cuda_index_url,
            )
            if ok:
                for pkg in passed_stage1:
                    if _normalize(pkg.name) in changed:
                        safe.append(pkg)
                    else:
                        blocked.append((
                            pkg,
                            "kept pinned by another installed package's requirement",
                        ))
            else:
                _log.info("Batch dry-run failed for %s: %s. Falling back per-package.",
                          project_dir, msg)
                for pkg in passed_stage1:
                    per_ok, per_changed, per_msg = constraint_preserving_dry_run(
                        project_dir, [pkg.name], current_state=current_state,
                        extra_index_url=cuda_index_url,
                    )
                    if per_ok and _normalize(pkg.name) in per_changed:
                        safe.append(pkg)
                    else:
                        reason = (
                            "batch and per-package dry-run both failed"
                            if not per_ok
                            else "kept pinned by another installed package's requirement"
                        )
                        blocked.append((pkg, reason))

    for pkg in torch_candidates:
        reason = _check_reverse_deps(
            pkg.name, pkg.latest_version, reverse_deps,
            exclude_requirer={_normalize(t) for t in (
                "torch", "torchvision", "torchaudio"
            )},
        )
        if reason is not None:
            blocked.append((pkg, reason))
            continue

        if cuda_index_url:
            ok, changed, msg = constraint_preserving_dry_run(
                project_dir, [pkg.name], current_state=current_state,
                index_url=cuda_index_url,
            )
            if ok and _normalize(pkg.name) in changed:
                safe.append(pkg)
            else:
                blocked.append((
                    pkg,
                    f"CUDA dry-run did not change version: {msg}" if ok
                    else f"CUDA dry-run failed: {msg}",
                ))
        else:
            ok, changed, _ = constraint_preserving_dry_run(
                project_dir, [pkg.name], current_state=current_state,
            )
            if ok and _normalize(pkg.name) in changed:
                safe.append(pkg)
            else:
                blocked.append((pkg, "no safe resolution available"))

    return safe, blocked


# ─── Legacy helpers ─────────────────────────────────────────────────────────

def create_safe_upgrade_plan(project_dir: Path) -> DependencyResolutionPlan:
    raw = find_outdated_packages(project_dir)
    safe, _ = classify_safe_upgrades(project_dir, raw)
    return DependencyResolutionPlan(
        project_name=project_dir.name,
        outdated_packages=raw,
        safe_upgrades=[pkg.name for pkg in safe],
        has_updates=len(raw) > 0,
    )


def apply_safe_upgrades(project_dir: Path, packages: List[str]) -> Tuple[bool, str]:
    if not is_venv_valid(project_dir) or not packages:
        return True, "No packages to upgrade or environment invalid."
    python_path = get_venv_python_path(project_dir)
    proc = run_pip_with_recovery(
        python_path, ["install", "--upgrade"] + packages, timeout=900,
    )
    if proc.returncode == 0:
        return True, proc.stdout
    return False, proc.stderr or proc.stdout or "Pip upgrade failed."


def _pip_dry_run(
    project_dir: Path,
    packages: List[str],
    index_url: Optional[str] = None,
    extra_index_url: Optional[str] = None,
    timeout: int = 300,
) -> Tuple[bool, Dict[str, str], str]:
    """Simple (non-constraint) dry-run. Kept for backward compatibility."""
    if not is_venv_valid(project_dir) or not packages:
        return True, {}, "nothing to do"

    python_path = get_venv_python_path(project_dir)
    args = [
        "install", "--dry-run", "--upgrade",
        "--upgrade-strategy", "only-if-needed",
        "--report", "-", "--quiet",
    ]
    if index_url:
        args.extend(["--index-url", index_url])
    if extra_index_url:
        args.extend(["--extra-index-url", extra_index_url])
    args.extend(packages)

    proc = run_pip_with_recovery(python_path, args, timeout=timeout)
    if proc.returncode != 0:
        return False, {}, (proc.stderr or proc.stdout or "")[-300:]

    report = _parse_pip_report(proc.stdout)
    if report is None:
        return True, _parse_would_install_text(proc.stdout), "ok-text"

    result: Dict[str, str] = {}
    for item in report.get("install", []):
        meta = item.get("metadata") or {}
        name = meta.get("name")
        version = meta.get("version")
        if name and version:
            result[_normalize(name)] = version
    return True, result, "ok"