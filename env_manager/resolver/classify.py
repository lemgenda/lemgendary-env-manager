"""Splitting outdated candidates into safe and blocked upgrades."""

from dataclasses import dataclass, field
from pathlib import Path

from env_manager._logging import get_logger
from env_manager.resolver.dry_run import constraint_preserving_dry_run
from env_manager.resolver.models import OutdatedPackage, normalize
from env_manager.resolver.pip_json import PipJsonError, installed_package_versions
from env_manager.resolver.reverse_deps import (
    check_reverse_deps,
    diagnose_pinned_reason,
    get_reverse_dependencies,
)
from env_manager.venv_manager import is_venv_valid

_log = get_logger(__name__)

CO_DEPENDENT_PAIRS: dict[str, set[str]] = {
    "pydantic": {"pydantic-core", "pydantic_core"},
    "pydantic-core": {"pydantic"},
    "pydantic_core": {"pydantic"},
}
"""Tightly coupled packages that must be upgraded in lockstep."""

TORCH_FAMILY_NORMALIZED = frozenset(normalize(t) for t in ("torch", "torchvision", "torchaudio"))


@dataclass
class _Context:
    """What every vetting step needs: the venv, its installed state and requirements."""

    project_dir: Path
    current_state: dict[str, str]
    reverse_deps: dict[str, list[tuple[str, str]]]
    cuda_index_url: str | None


@dataclass
class _Verdicts:
    """The safe and blocked candidates collected so far."""

    safe: list[OutdatedPackage] = field(default_factory=list)
    blocked: list[tuple[OutdatedPackage, str]] = field(default_factory=list)


def _reverse_dep_survivors(
    ctx: _Context, candidates: list[OutdatedPackage], out: _Verdicts,
) -> list[OutdatedPackage]:
    """Block candidates an installed package's requirement forbids; return the rest.

    A co-dependent twin that is also an upgrade candidate does not block
    the package in the reverse-dependency check.
    """
    candidate_names = {normalize(pkg.name) for pkg in candidates}
    survivors: list[OutdatedPackage] = []
    for pkg in candidates:
        pkg_norm = normalize(pkg.name)
        exclude_req = {pkg_norm} | {
            normalize(twin) for twin in CO_DEPENDENT_PAIRS.get(pkg_norm, set())
            if normalize(twin) in candidate_names
        }
        reason = check_reverse_deps(
            pkg.name, pkg.latest_version, ctx.reverse_deps, exclude_requirer=exclude_req,
        )
        if reason is None:
            survivors.append(pkg)
        else:
            out.blocked.append((pkg, reason))
    return survivors


def _pinned_reason(ctx: _Context, pkg: OutdatedPackage) -> str:
    """Explain why the dry-run kept ``pkg`` at its installed version."""
    return diagnose_pinned_reason(pkg.name, pkg.latest_version, ctx.current_state)


def _vet_normal(ctx: _Context, candidates: list[OutdatedPackage], out: _Verdicts) -> None:
    """Dry-run the survivors as one batch; fall back to one dry-run each if it fails."""
    survivors = _reverse_dep_survivors(ctx, candidates, out)
    if not survivors:
        return
    ok, changed, msg = constraint_preserving_dry_run(
        ctx.project_dir, [pkg.name for pkg in survivors], current_state=ctx.current_state,
        extra_index_url=ctx.cuda_index_url,
    )
    if ok:
        for pkg in survivors:
            if normalize(pkg.name) in changed:
                out.safe.append(pkg)
            else:
                out.blocked.append((pkg, _pinned_reason(ctx, pkg)))
        return

    _log.info("Batch dry-run failed for %s: %s. Falling back per-package.",
              ctx.project_dir, msg)
    for pkg in survivors:
        per_ok, per_changed, _per_msg = constraint_preserving_dry_run(
            ctx.project_dir, [pkg.name], current_state=ctx.current_state,
            extra_index_url=ctx.cuda_index_url,
        )
        if per_ok and normalize(pkg.name) in per_changed:
            out.safe.append(pkg)
        elif not per_ok:
            out.blocked.append((pkg, "batch and per-package dry-run both failed"))
        else:
            out.blocked.append((pkg, _pinned_reason(ctx, pkg)))


def _vet_torch(ctx: _Context, pkg: OutdatedPackage, out: _Verdicts) -> None:
    """Vet one CUDA wheel: reverse deps outside the torch family, then a dry-run."""
    reason = check_reverse_deps(
        pkg.name, pkg.latest_version, ctx.reverse_deps,
        exclude_requirer=set(TORCH_FAMILY_NORMALIZED),
    )
    if reason is not None:
        out.blocked.append((pkg, reason))
        return

    ok, changed, msg = constraint_preserving_dry_run(
        ctx.project_dir, [pkg.name], current_state=ctx.current_state,
        index_url=ctx.cuda_index_url,
    )
    if ok and normalize(pkg.name) in changed:
        out.safe.append(pkg)
    elif not ctx.cuda_index_url:
        out.blocked.append((pkg, "no safe resolution available"))
    elif ok:
        out.blocked.append((pkg, f"CUDA dry-run did not change version: {msg}"))
    else:
        out.blocked.append((pkg, f"CUDA dry-run failed: {msg}"))


def classify_safe_upgrades(
    project_dir: Path,
    candidates: list[OutdatedPackage],
    cuda_index_url: str | None = None,
) -> tuple[list[OutdatedPackage], list[tuple[OutdatedPackage, str]]]:
    """Split outdated candidates into (safe, blocked)."""
    if not candidates or not is_venv_valid(project_dir):
        return [], [(pkg, "invalid venv") for pkg in candidates]

    try:
        ctx = _Context(
            project_dir, installed_package_versions(project_dir),
            get_reverse_dependencies(project_dir), cuda_index_url,
        )
    except PipJsonError as exc:
        # Fail closed: without the installed set and its requirements no
        # candidate can be proven safe.
        _log.error("Blocking all %d upgrade candidates for %s: %s",
                   len(candidates), project_dir.name, exc)
        return [], [(pkg, f"installed package state unavailable: {exc}") for pkg in candidates]

    out = _Verdicts()
    normal = [pkg for pkg in candidates if pkg.package_type != "wheel-cuda"]
    if normal:
        _vet_normal(ctx, normal, out)
    for pkg in candidates:
        if pkg.package_type == "wheel-cuda":
            _vet_torch(ctx, pkg, out)
    return out.safe, out.blocked
