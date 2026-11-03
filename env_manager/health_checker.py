"""Health audit and cross-project consistency module.

Audits virtual environments, identifies missing packages, detects version
drift and pin-type mismatches across projects, and produces full-coverage
inventories that reconcile every manifest against every installed venv.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from packaging.markers import (
    InvalidMarker,
    Marker,
    UndefinedComparison,
    UndefinedEnvironmentName,
)
from rich.table import Table

from env_manager._logging import get_logger
from env_manager.bootstrap import BootstrapStatus, verify_prerequisites
from env_manager.dependency_resolver import (
    OutdatedPackage,
    classify_safe_upgrades,
    find_outdated_packages,
)
from env_manager.npm_manager import NpmPackageStatus, audit_npm_workspaces
from env_manager.requirements_manager import parse_requirements_file
from env_manager.system_probe import HardwareProfile, probe_hardware
from env_manager.updater import _check_torch_cuda_upgrades
from env_manager.venv_manager import (
    ProjectVenvInfo,
    discover_projects,
    get_installed_packages,
)

_log = get_logger(__name__)

MIN_SHARED_DECLARATIONS = 2
"""A package counts as shared, and is drift-checked, when this many manifests declare it."""

# Packages that exist in every fresh venv; excluded from coverage math.
_BOOTSTRAP_NORMALIZED: set[str] = {"pip", "wheel", "setuptools", "pkg-resources"}

# Packages served by the PyTorch index; must be checked against it rather
# than PyPI when a CUDA/ROCm backend is available.
_TORCH_FAMILY_NORMALIZED: set[str] = {"torch", "torchvision", "torchaudio"}

# OpenCV distributions that satisfy the 'cv2' runtime namespace.
_OPENCV_VARIANTS_NORMALIZED: set[str] = {
    "opencv-python",
    "opencv-python-headless",
    "opencv-contrib-python",
    "opencv-contrib-python-headless",
}


# ─── Data structures ────────────────────────────────────────────────────────

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


# ─── Name normalization ─────────────────────────────────────────────────────

def normalize_package_name(name: str) -> str:
    """Canonicalize package name per PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _classify_pin_type(specifier: str) -> str:
    spec = (specifier or "").strip()
    if not spec:
        return "floating"
    if spec.startswith("==") and "," not in spec:
        return "exact"
    return "range"


# ─── Manifest parsing ───────────────────────────────────────────────────────

def _marker_matches(marker: str) -> bool:
    """Evaluate a PEP 508 marker for this platform.

    An unparsable or non-evaluable marker counts as matching (the entry is
    kept as declared, the conservative choice) and the reason is logged.
    """
    try:
        return bool(Marker(marker).evaluate())
    except (InvalidMarker, UndefinedComparison, UndefinedEnvironmentName) as exc:
        _log.warning("PEP 508 marker %r could not be evaluated, keeping entry: %s", marker, exc)
        return True


def _load_project_manifest(req_file: Path) -> ProjectManifest:
    """Parse a requirements.txt into a ProjectManifest.

    Entries whose PEP 508 environment marker does not evaluate on this
    platform are recorded in ``platform_skipped`` rather than dropped.
    """
    manifest = ProjectManifest()
    if not req_file.exists():
        return manifest

    for entry in parse_requirements_file(req_file):
        if entry.is_index_url or entry.is_editable:
            continue

        if entry.marker and not _marker_matches(entry.marker):
            manifest.platform_skipped.append(entry.name)
            continue

        name = normalize_package_name(entry.name)
        manifest.pins[name] = PinInfo(
            pin_type=_classify_pin_type(entry.specifier),
            specifier=entry.specifier or "",
        )

    return manifest


# Keep the old name as a thin compatibility shim for callers that only
# need the pin map.
def _load_project_pins(req_file: Path) -> dict[str, PinInfo]:
    return _load_project_manifest(req_file).pins


# ─── Upgrade classification ─────────────────────────────────────────────────

def _classify_torch_via_cuda_index(
    project_dir: Path,
    current_state: dict[str, str],
    cuda_index_url: str,
) -> dict[str, UpgradeStatus]:
    """Resolve torch-family upgrade candidates against the CUDA index."""
    candidates, reason = _check_torch_cuda_upgrades(
        project_dir, cuda_index_url, current_state,
    )
    if not candidates:
        _log.debug("No CUDA torch candidates for %s: %s", project_dir, reason)

    result: dict[str, UpgradeStatus] = {}
    for pkg in candidates:
        result[normalize_package_name(pkg.name)] = UpgradeStatus(
            current=pkg.current_version,
            latest=pkg.latest_version,
            safe=True,
            reason=None,
        )
    return result


def _classify_project_upgrades(
    project: ProjectHealth,
    cuda_index_url: str | None = None,
) -> dict[str, UpgradeStatus]:
    """Return {normalized_name: UpgradeStatus} for outdated packages."""
    p_dir = Path(project.project_dir)
    if not project.venv_exists:
        return {}

    raw: list[OutdatedPackage] = find_outdated_packages(p_dir)
    if not raw:
        return {}

    torch_raw = [
        p for p in raw
        if normalize_package_name(p.name) in _TORCH_FAMILY_NORMALIZED
    ]
    non_torch_raw = [
        p for p in raw
        if normalize_package_name(p.name) not in _TORCH_FAMILY_NORMALIZED
    ]

    result: dict[str, UpgradeStatus] = {}

    if non_torch_raw:
        safe, blocked = classify_safe_upgrades(
            p_dir, non_torch_raw, cuda_index_url=cuda_index_url,
        )
        for pkg in safe:
            result[normalize_package_name(pkg.name)] = UpgradeStatus(
                current=pkg.current_version,
                latest=pkg.latest_version,
                safe=True, reason=None,
            )
        for pkg, reason in blocked:
            result[normalize_package_name(pkg.name)] = UpgradeStatus(
                current=pkg.current_version,
                latest=pkg.latest_version,
                safe=False, reason=reason,
            )

    if torch_raw:
        if cuda_index_url:
            current_state = {
                normalize_package_name(k): v
                for k, v in get_installed_packages(p_dir).items()
            }
            cuda_result = _classify_torch_via_cuda_index(
                p_dir, current_state, cuda_index_url,
            )
            result.update(cuda_result)
        else:
            safe, blocked = classify_safe_upgrades(p_dir, torch_raw)
            for pkg in safe:
                result[normalize_package_name(pkg.name)] = UpgradeStatus(
                    current=pkg.current_version,
                    latest=pkg.latest_version,
                    safe=True, reason=None,
                )
            for pkg, reason in blocked:
                result[normalize_package_name(pkg.name)] = UpgradeStatus(
                    current=pkg.current_version,
                    latest=pkg.latest_version,
                    safe=False, reason=reason,
                )

    return result


# ─── Project audit ──────────────────────────────────────────────────────────

def audit_project_health(project_info: ProjectVenvInfo) -> ProjectHealth:
    """Audit single project virtual environment and requirements."""
    p_dir = Path(project_info.project_dir)
    req_file = p_dir / "requirements.txt"
    installed = get_installed_packages(p_dir) if project_info.is_valid else {}
    canonical_installed = {
        normalize_package_name(k): v for k, v in installed.items()
    }

    missing: list[str] = []
    total_required = 0

    if project_info.is_node_project:
        pkg_json = p_dir / "package.json"
        total_req = 0
        if pkg_json.is_file():
            data = _read_json_object(pkg_json)
            total_req = len(data.get("dependencies") or {}) + len(data.get("devDependencies") or {})
        node_mods = _read_node_modules_versions(p_dir) if project_info.node_modules_present else {}
        return ProjectHealth(
            name=project_info.name,
            project_dir=project_info.project_dir,
            venv_exists=project_info.node_modules_present,
            python_version=None,
            total_required=total_req,
            total_installed=len(node_mods),
            missing_packages=[] if project_info.node_modules_present else ["node_modules"],
            installed_packages=node_mods,
            is_healthy=project_info.node_modules_present,
        )

    if req_file.exists():
        parsed = parse_requirements_file(req_file)
        has_installed_opencv = any(
            v in canonical_installed for v in _OPENCV_VARIANTS_NORMALIZED
        )
        for entry in parsed:
            if entry.is_index_url:
                continue
            if entry.marker and not _marker_matches(entry.marker):
                continue
            total_required += 1
            pkg_name = normalize_package_name(entry.name)
            if pkg_name in _OPENCV_VARIANTS_NORMALIZED and has_installed_opencv:
                continue
            if pkg_name not in canonical_installed:
                missing.append(entry.name)

    is_healthy = project_info.is_valid and len(missing) == 0

    return ProjectHealth(
        name=project_info.name,
        project_dir=project_info.project_dir,
        venv_exists=project_info.is_valid,
        python_version=project_info.python_version,
        total_required=total_required,
        total_installed=len(installed),
        missing_packages=missing,
        installed_packages=installed,
        is_healthy=is_healthy,
    )


# ─── Drift computation ──────────────────────────────────────────────────────

def compute_version_drift(
    projects: list[ProjectHealth],
    cuda_index_url: str | None = None,
    include_safety: bool = True,
) -> list[VersionDriftEntry]:
    """Identify version discrepancies, pin mismatches, and safe upgrades."""
    if not projects:
        return []

    project_pins: dict[str, dict[str, PinInfo]] = {}
    for p in projects:
        req_file = Path(p.project_dir) / "requirements.txt"
        project_pins[p.name] = _load_project_pins(req_file)

    declaration_counts: dict[str, int] = {}
    for pins in project_pins.values():
        for name in pins.keys():
            declaration_counts[name] = declaration_counts.get(name, 0) + 1

    shared_packages = sorted(
        name for name, count in declaration_counts.items() if count >= MIN_SHARED_DECLARATIONS
    )

    upgrade_map: dict[str, dict[str, UpgradeStatus]] = {}
    if include_safety:
        for p in projects:
            upgrade_map[p.name] = _classify_project_upgrades(
                p, cuda_index_url=cuda_index_url,
            )

    entries: list[VersionDriftEntry] = []
    for pkg in shared_packages:
        versions: dict[str, str | None] = {}
        pins: dict[str, PinInfo] = {}
        upgrades: dict[str, UpgradeStatus] = {}
        pin_types: set[str] = set()
        observed_versions: set[str] = set()

        for p in projects:
            pin = project_pins.get(p.name, {}).get(pkg)
            installed_ver = p.installed_packages.get(pkg) or None

            if pin is not None:
                pins[p.name] = PinInfo(
                    pin_type=pin.pin_type,
                    specifier=pin.specifier,
                    installed=installed_ver,
                )
                pin_types.add(pin.pin_type)
            elif installed_ver:
                pins[p.name] = PinInfo(
                    pin_type="transitive", specifier="", installed=installed_ver,
                )
            else:
                pins[p.name] = PinInfo(
                    pin_type="absent", specifier="", installed=None,
                )

            versions[p.name] = installed_ver
            if installed_ver:
                observed_versions.add(installed_ver)

            if include_safety:
                status = upgrade_map.get(p.name, {}).get(pkg)
                upgrades[p.name] = status or UpgradeStatus(
                    current=installed_ver, latest=None, safe=False, reason=None,
                )
            else:
                upgrades[p.name] = UpgradeStatus(
                    current=installed_ver, latest=None, safe=False, reason=None,
                )

        has_drift = len(observed_versions) > 1
        has_pin_mismatch = len({t for t in pin_types if t != "transitive"}) > 1

        entries.append(
            VersionDriftEntry(
                package_name=pkg,
                versions=versions,
                pins=pins,
                upgrades=upgrades,
                has_drift=has_drift,
                has_pin_mismatch=has_pin_mismatch,
                projects_declared=declaration_counts.get(pkg, 0),
            )
        )

    return entries


# ─── Coverage & single-manifest ─────────────────────────────────────────────

def compute_manifest_coverage(
    projects: list[ProjectHealth],
) -> list[ManifestCoverageEntry]:
    """Reconcile every manifest's declared set against its installed set."""
    entries: list[ManifestCoverageEntry] = []

    for p in projects:
        manifest = _load_project_manifest(Path(p.project_dir) / "requirements.txt")
        declared = set(manifest.pins.keys())

        installed_norm = {
            normalize_package_name(k): v for k, v in p.installed_packages.items()
        }
        installed = set(installed_norm.keys()) - _BOOTSTRAP_NORMALIZED

        missing_set = declared - installed
        if any(v in installed for v in _OPENCV_VARIANTS_NORMALIZED):
            missing_set = {
                pkg for pkg in missing_set if pkg not in _OPENCV_VARIANTS_NORMALIZED
            }

        missing = sorted(missing_set)
        extra = sorted(installed - declared)

        entries.append(
            ManifestCoverageEntry(
                project_name=p.name,
                declared_count=len(declared),
                installed_count=len(installed),
                missing=missing,
                extra=extra,
                platform_skipped=sorted(manifest.platform_skipped),
            )
        )

    return entries


def compute_single_manifest_packages(
    projects: list[ProjectHealth],
) -> list[SingleManifestEntry]:
    """Return every package declared in exactly one manifest."""
    project_manifests = {
        p.name: _load_project_manifest(Path(p.project_dir) / "requirements.txt")
        for p in projects
    }

    counts: dict[str, int] = {}
    for manifest in project_manifests.values():
        for name in manifest.pins.keys():
            counts[name] = counts.get(name, 0) + 1

    entries: list[SingleManifestEntry] = []
    for p in projects:
        manifest = project_manifests[p.name]
        installed_norm = {
            normalize_package_name(k): v for k, v in p.installed_packages.items()
        }
        for pkg_name, pin in manifest.pins.items():
            if counts.get(pkg_name, 0) != 1:
                continue
            entries.append(
                SingleManifestEntry(
                    package_name=pkg_name,
                    project=p.name,
                    installed_version=installed_norm.get(pkg_name),
                    pin_type=pin.pin_type,
                    specifier=pin.specifier,
                )
            )

    entries.sort(key=lambda e: (e.project, e.package_name))
    return entries


# ─── NPM drift ──────────────────────────────────────────────────────────────

def _read_json_object(path: Path) -> dict[str, Any]:
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


def _read_node_modules_versions(workspace_dir: Path) -> dict[str, str]:
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
                    ver = _read_json_object(scoped / "package.json").get("version")
                    if ver:
                        installed[f"{name}/{scoped.name}"] = str(ver)
            else:
                ver = _read_json_object(entry / "package.json").get("version")
                if ver:
                    installed[name] = str(ver)
    except OSError as exc:
        _log.warning("node_modules walk failed for %s: %s", workspace_dir, exc)

    return installed


def _read_package_json_specifiers(
    workspace_dir: Path,
) -> tuple[dict[str, str], set[str]]:
    specifiers: dict[str, str] = {}
    dev_names: set[str] = set()
    pkg_json = workspace_dir / "package.json"
    data = _read_json_object(pkg_json)
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
        workspace_versions[ws.name] = _read_node_modules_versions(ws)
        specifiers, dev_names = _read_package_json_specifiers(ws)
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


# ─── Rendering ──────────────────────────────────────────────────────────────

_PIN_GLYPH = {
    "exact":      "=",
    "range":      "~",
    "floating":   "*",
    "transitive": ".",
    "absent":     "",
}


def _format_cell(
    version: str | None,
    pin: PinInfo | None,
    upgrade: UpgradeStatus | None,
) -> str:
    """Format one drift-matrix cell."""
    if not version:
        return "-"

    pin_type = pin.pin_type if pin else ""
    glyph = _PIN_GLYPH.get(pin_type, "")
    base = f"{version}{glyph}" if glyph else version

    if upgrade and upgrade.is_outdated and upgrade.latest:
        arrow = "^" if upgrade.safe else "!"
        return f"{base} -> {upgrade.latest} {arrow}"

    return base


def render_drift_matrix(entries: list[VersionDriftEntry]) -> Table:
    """Return a Rich Table showing Python drift, pins, and upgrade status."""
    projects: list[str] = []
    for e in entries:
        for name in e.versions.keys():
            if name not in projects:
                projects.append(name)

    table = Table(title="Python Package Drift & Upgrade Status", show_lines=False)
    table.add_column("Package", style="cyan", no_wrap=True)
    for proj in projects:
        table.add_column(proj, min_width=18, overflow="fold")
    table.add_column("Drift", justify="center", no_wrap=True)
    table.add_column("Safe ↑", justify="center", no_wrap=True)

    for entry in entries:
        row: list[str] = [entry.package_name]
        safe_count = 0
        for proj in projects:
            ver = entry.versions.get(proj)
            pin = entry.pins.get(proj)
            status = entry.upgrades.get(proj)
            if status and status.safe and status.is_outdated:
                safe_count += 1
            row.append(_format_cell(ver, pin, status))

        drift_marker = "[red]yes[/red]" if entry.has_drift else "[green]no[/green]"
        if entry.has_pin_mismatch:
            drift_marker = f"{drift_marker} [yellow]pin≠[/yellow]"
        row.append(drift_marker)
        row.append(str(safe_count) if safe_count else "-")
        table.add_row(*row)

    return table


def render_manifest_coverage_table(
    entries: list[ManifestCoverageEntry],
) -> Table:
    """Return a Rich Table showing declared-vs-installed coverage per project."""
    table = Table(
        title="Manifest Coverage (Declared vs Installed)",
        show_lines=False,
    )
    table.add_column("Project", style="cyan", no_wrap=True)
    table.add_column("Declared", justify="right")
    table.add_column("Installed", justify="right")
    table.add_column("Missing", justify="right")
    table.add_column("Extra", justify="right", style="yellow")
    table.add_column("Platform-Skipped", justify="right", style="dim")

    for e in entries:
        missing_cell = (
            f"[red]{len(e.missing)}[/red]" if e.missing else "[green]0[/green]"
        )
        extra_cell = (
            f"[yellow]{len(e.extra)}[/yellow]" if e.extra else "0"
        )
        skipped_cell = (
            f"[dim]{len(e.platform_skipped)}[/dim]" if e.platform_skipped else "0"
        )
        table.add_row(
            e.project_name,
            str(e.declared_count),
            str(e.installed_count),
            missing_cell,
            extra_cell,
            skipped_cell,
        )

    return table


def render_single_manifest_table(
    entries: list[SingleManifestEntry],
) -> Table:
    """Return a Rich Table showing packages declared in only one manifest."""
    table = Table(
        title="Packages Declared in Only One Manifest",
        show_lines=False,
    )
    table.add_column("Project", style="cyan", no_wrap=True)
    table.add_column("Package", style="white", no_wrap=True)
    table.add_column("Pin", style="yellow", no_wrap=True)
    table.add_column("Installed", style="green")

    for e in entries:
        glyph = _PIN_GLYPH.get(e.pin_type, "")
        spec = e.specifier if e.specifier else "-"
        pin_display = f"{glyph} {spec}".strip() if glyph else spec
        installed = e.installed_version or "[red]MISSING[/red]"
        table.add_row(e.project, e.package_name, pin_display, installed)

    return table


def render_npm_drift_matrix(entries: list[NpmDriftEntry]) -> Table:
    """Return a Rich Table showing npm workspace drift."""
    workspaces: list[str] = []
    for e in entries:
        for name in e.versions.keys():
            if name not in workspaces:
                workspaces.append(name)

    table = Table(title="NPM Package Drift Across Workspaces", show_lines=False)
    table.add_column("Package", style="cyan", no_wrap=True)
    for ws in workspaces:
        table.add_column(ws, min_width=18, overflow="fold")
    table.add_column("Drift", justify="center", no_wrap=True)
    table.add_column("Kind", justify="center", no_wrap=True)

    for entry in entries:
        row: list[str] = [entry.package_name]
        for ws in workspaces:
            ver = entry.versions.get(ws)
            spec = entry.specifiers.get(ws)
            if ver and spec:
                row.append(f"{ver}  ({spec})")
            elif ver:
                row.append(ver)
            elif spec:
                row.append(f"-  ({spec})")
            else:
                row.append("-")
        row.append("[red]yes[/red]" if entry.has_drift else "[green]no[/green]")
        row.append("dev" if entry.is_dev_dependency else "prod")
        table.add_row(*row)

    return table


# ─── Full audit ─────────────────────────────────────────────────────────────

def run_full_health_audit(
    base_dir: Path | None = None,
    include_safety: bool = True,
) -> HealthAuditReport:
    """Execute complete health audit across all projects and host system."""
    bootstrap = verify_prerequisites()
    hardware = probe_hardware()
    discovered = discover_projects(base_dir)

    projects_health: list[ProjectHealth] = []
    for d in discovered:
        health = audit_project_health(d)
        projects_health.append(health)

    cuda_index_url = getattr(hardware, "recommended_torch_index", None)
    drift = compute_version_drift(
        projects_health,
        cuda_index_url=cuda_index_url,
        include_safety=include_safety,
    )
    coverage = compute_manifest_coverage(projects_health)
    singles = compute_single_manifest_packages(projects_health)

    npm_packages = audit_npm_workspaces(base_dir)
    npm_drift = compute_npm_drift(base_dir) if base_dir else []

    all_proj_healthy = (
        all(p.is_healthy for p in projects_health) if projects_health else False
    )
    npm_healthy = (
        all(s.node_modules_present for s in npm_packages) if npm_packages else True
    )
    overall_healthy = (
        bootstrap.python_valid
        and bootstrap.git_installed
        and all_proj_healthy
        and npm_healthy
    )

    return HealthAuditReport(
        bootstrap=bootstrap,
        hardware=hardware,
        projects=projects_health,
        version_drift=drift,
        manifest_coverage=coverage,
        single_manifest_packages=singles,
        npm_packages=npm_packages,
        npm_drift=npm_drift,
        overall_healthy=overall_healthy,
    )
