"""Upgrade plan records, upgrade ordering and package-name helpers."""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from env_manager.ecosystem import ecosystem
from env_manager.resolver.models import OutdatedPackage, normalize
from env_manager.venv_manager import (
    get_installed_packages,
)

TORCH_FAMILY: tuple[str, ...] = ("torch", "torchvision", "torchaudio")


def upgrade_order() -> list[str]:
    """Managed project folders in upgrade order (``projects[].upgrade_order``)."""
    return [p.folder for p in ecosystem().managed_projects()]


# Paired packages where manual curation matters more than "latest wins".
PROTECTED_FROM_SYNC: set = {
    "ruff",
    "pydantic",
    "pydantic-core",
    "pydantic_core",
}


@dataclass
class ProjectUpgradePlan:
    """Per-project upgrade plan."""
    name: str
    project_dir: str
    is_node_project: bool
    outdated_packages: list[OutdatedPackage]
    safe_packages: list[OutdatedPackage] = field(default_factory=list)
    blocked_packages: list[tuple[OutdatedPackage, str]] = field(default_factory=list)
    torch_cuda_packages: list[OutdatedPackage] = field(default_factory=list)
    has_updates: bool = False
    has_safe_updates: bool = False
    cuda_available: bool = False
    cuda_index_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a dict, pairing each blocked package with its reason."""
        d = asdict(self)
        d["blocked_packages"] = [
            {"package": asdict(pkg), "reason": reason}
            for pkg, reason in self.blocked_packages
        ]
        return d


@dataclass
class EcosystemUpgradePlan:
    """Full ecosystem upgrade plan across all managed projects."""
    projects: list[ProjectUpgradePlan]
    total_outdated: int
    total_safe: int
    total_blocked: int
    has_any_updates: bool
    has_any_safe_updates: bool
    cuda_detected: bool = False
    cuda_index_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a plain dict for JSON output."""
        return asdict(self)


@dataclass
class UpgradeEvent:
    """Single telemetry event from the upgrade pipeline."""
    project: str
    package: str
    old_version: str
    new_version: str
    status: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a plain dict for JSON output."""
        return asdict(self)


def is_torch_family(name: str) -> bool:
    """Return True when ``name`` is torch, torchvision or torchaudio."""
    return normalize(name) in {normalize(t) for t in TORCH_FAMILY}


def venv_package_map(project_dir: Path) -> dict[str, str]:
    """Return the project's installed packages keyed by normalized name."""
    return {normalize(k): v for k, v in get_installed_packages(project_dir).items()}


def pinned_specs(packages: list[OutdatedPackage]) -> list[str]:
    """Return ``name==latest_version`` requirement specs for vetted candidates."""
    return [f"{pkg.name}=={pkg.latest_version}" for pkg in packages]
