"""Health audit records and package-name normalization shared by the audit modules."""

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from env_manager.bootstrap import BootstrapStatus
from env_manager.npm_manager import NpmPackageStatus
from env_manager.system_probe import HardwareProfile

MIN_SHARED_DECLARATIONS = 2
"""A package counts as shared, and is drift-checked, when this many manifests declare it."""


# Packages that exist in every fresh venv; excluded from coverage math.
BOOTSTRAP_NORMALIZED: set[str] = {"pip", "wheel", "setuptools", "pkg-resources"}


# Packages served by the PyTorch index; must be checked against it rather
# than PyPI when a CUDA/ROCm backend is available.
TORCH_FAMILY_NORMALIZED: set[str] = {"torch", "torchvision", "torchaudio"}


# OpenCV distributions that satisfy the 'cv2' runtime namespace.
OPENCV_VARIANTS_NORMALIZED: set[str] = {
    "opencv-python",
    "opencv-python-headless",
    "opencv-contrib-python",
    "opencv-contrib-python-headless",
}


@dataclass
class ProjectHealth:
    """Detailed health status for a single project."""
    name: str
    project_dir: str
    venv_exists: bool
    python_version: str | None
    total_required: int
    total_installed: int
    missing_packages: list[str]
    installed_packages: dict[str, str]
    is_healthy: bool

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a plain dict for JSON output."""
        return asdict(self)


@dataclass
class PinInfo:
    """How a single package is pinned in a project's manifest."""
    pin_type: str          # "exact" | "range" | "floating" | "transitive" | "absent"
    specifier: str
    installed: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a plain dict for JSON output."""
        return asdict(self)


@dataclass
class ProjectManifest:
    """Parsed view of a project's requirements.txt."""
    pins: dict[str, PinInfo] = field(default_factory=dict)
    platform_skipped: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return the pins and the platform-skipped package list as a dict."""
        return {
            "pins": {k: v.to_dict() for k, v in self.pins.items()},
            "platform_skipped": list(self.platform_skipped),
        }


@dataclass
class UpgradeStatus:
    """Per-project upgrade status for a single package."""
    current: str | None
    latest: str | None
    safe: bool
    reason: str | None = None

    @property
    def is_outdated(self) -> bool:
        """True when a newer version than the installed one is known."""
        return self.latest is not None and self.latest != self.current

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a plain dict for JSON output."""
        return asdict(self)


@dataclass
class VersionDriftEntry:
    """Package version comparison across projects, with pin and safety info."""
    package_name: str
    versions: dict[str, str | None]
    pins: dict[str, PinInfo]
    upgrades: dict[str, UpgradeStatus]
    has_drift: bool
    has_pin_mismatch: bool
    projects_declared: int

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a dict, with each project's upgrade status serialized."""
        d = asdict(self)
        d["upgrades"] = {
            proj: status.to_dict() for proj, status in self.upgrades.items()
        }
        return d


@dataclass
class ManifestCoverageEntry:
    """Declared-vs-installed reconciliation for a single project."""
    project_name: str
    declared_count: int
    installed_count: int
    missing: list[str]           # declared but not installed
    extra: list[str]             # installed but not declared (transitive)
    platform_skipped: list[str]  # declared but skipped by an OS-specific marker

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a plain dict for JSON output."""
        return asdict(self)


@dataclass
class SingleManifestEntry:
    """A package declared in exactly one manifest (drift-matrix invisible)."""
    package_name: str
    project: str
    installed_version: str | None
    pin_type: str
    specifier: str

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a plain dict for JSON output."""
        return asdict(self)


@dataclass
class NpmDriftEntry:
    """Package version comparison for npm workspaces."""
    package_name: str
    versions: dict[str, str | None]
    specifiers: dict[str, str | None]
    has_drift: bool
    is_dev_dependency: bool

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a plain dict for JSON output."""
        return asdict(self)


@dataclass
class HealthAuditReport:
    """Comprehensive ecosystem health audit report."""
    bootstrap: BootstrapStatus
    hardware: HardwareProfile
    projects: list[ProjectHealth]
    version_drift: list[VersionDriftEntry]
    manifest_coverage: list[ManifestCoverageEntry]
    single_manifest_packages: list[SingleManifestEntry]
    npm_packages: list[NpmPackageStatus]
    npm_drift: list[NpmDriftEntry]
    overall_healthy: bool

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a plain dict for JSON output."""
        return asdict(self)


def normalize_package_name(name: str) -> str:
    """Canonicalize package name per PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()
