"""Health audit and cross-project consistency module.

Audits virtual environments, identifies missing packages, detects version
drift and pin-type mismatches across projects, and produces full-coverage
inventories that reconcile every manifest against every installed venv.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    from packaging.markers import Marker
except ImportError:
    Marker = None

try:
    from rich.table import Table
    _RICH_AVAILABLE = True
except ImportError:
    Table = None  # type: ignore
    _RICH_AVAILABLE = False

from env_manager.bootstrap import BootstrapStatus, verify_prerequisites
from env_manager._logging import get_logger
from env_manager.dependency_resolver import (
    OutdatedPackage,
    classify_safe_upgrades,
    find_outdated_packages,
)
from env_manager.npm_manager import NpmPackageStatus, audit_npm_workspaces
from env_manager.requirements_manager import parse_requirements_file
from env_manager.system_probe import HardwareProfile, probe_hardware
from env_manager.venv_manager import (
    ProjectVenvInfo,
    discover_projects,
    get_installed_packages,
)

_log = get_logger(__name__)

# Packages that exist in every fresh venv; excluded from coverage math.
_BOOTSTRAP_NORMALIZED: Set[str] = {"pip", "wheel", "setuptools", "pkg-resources"}

# Packages served by the PyTorch index; must be checked against it rather
# than PyPI when a CUDA/ROCm backend is available.
_TORCH_FAMILY_NORMALIZED: Set[str] = {"torch", "torchvision", "torchaudio"}


# ─── Data structures ────────────────────────────────────────────────────────

@dataclass
class ProjectHealth:
    """Detailed health status for a single project."""
    name: str
    project_dir: str
    venv_exists: bool
    python_version: Optional[str]
    total_required: int
    total_installed: int
    missing_packages: List[str]
    installed_packages: Dict[str, str]
    is_healthy: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PinInfo:
    """How a single package is pinned in a project's manifest."""
    pin_type: str          # "exact" | "range" | "floating" | "transitive" | "absent"
    specifier: str
    installed: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectManifest:
    """Parsed view of a project's requirements.txt."""
    pins: Dict[str, PinInfo] = field(default_factory=dict)
    platform_skipped: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pins": {k: v.to_dict() for k, v in self.pins.items()},
            "platform_skipped": list(self.platform_skipped),
        }


@dataclass
class UpgradeStatus:
    """Per-project upgrade status for a single package."""
    current: Optional[str]
    latest: Optional[str]
    safe: bool
    reason: Optional[str] = None

    @property
    def is_outdated(self) -> bool:
        return self.latest is not None and self.latest != self.current

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class VersionDriftEntry:
    """Package version comparison across projects, with pin and safety info."""
    package_name: str
    versions: Dict[str, Optional[str]]
    pins: Dict[str, PinInfo]
    upgrades: Dict[str, UpgradeStatus]
    has_drift: bool
    has_pin_mismatch: bool
    projects_declared: int

    def to_dict(self) -> Dict[str, Any]:
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
    missing: List[str]           # declared but not installed
    extra: List[str]             # installed but not declared (transitive)
    platform_skipped: List[str]  # declared but skipped by an OS-specific marker

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SingleManifestEntry:
    """A package declared in exactly one manifest (drift-matrix invisible)."""
    package_name: str
    project: str
    installed_version: Optional[str]
    pin_type: str
    specifier: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NpmDriftEntry:
    """Package version comparison for npm workspaces."""
    package_name: str
    versions: Dict[str, Optional[str]]
    specifiers: Dict[str, Optional[str]]
    has_drift: bool
    is_dev_dependency: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HealthAuditReport:
    """Comprehensive ecosystem health audit report."""
    bootstrap: BootstrapStatus
    hardware: HardwareProfile
    projects: List[ProjectHealth]
    version_drift: List[VersionDriftEntry]
    manifest_coverage: List[ManifestCoverageEntry]
    single_manifest_packages: List[SingleManifestEntry]
    npm_packages: List[NpmPackageStatus]
    npm_drift: List[NpmDriftEntry]
    overall_healthy: bool

    def to_dict(self) -> Dict[str, Any]:
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

        if entry.marker and Marker:
            try:
                matches = Marker(entry.marker).evaluate()
            except Exception as exc:
                _log.debug("Marker evaluation failed for '%s': %s", entry.marker, exc)
                matches = True  # conservative: keep as declared
            if not matches:
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
def _load_project_pins(req_file: Path) -> Dict[str, PinInfo]:
    return _load_project_manifest(req_file).pins


# ─── Upgrade classification ─────────────────────────────────────────────────

def _classify_torch_via_cuda_index(
    project_dir: Path,
    current_state: Dict[str, str],
    cuda_index_url: str,
) -> Dict[str, UpgradeStatus]:
    """Resolve torch-family upgrade candidates against the CUDA index."""
    try:
        from env_manager.updater import _check_torch_cuda_upgrades
    except Exception as exc:
        _log.debug("Cannot import CUDA-aware torch check: %s", exc)
        return {}

    try:
        candidates, _reason = _check_torch_cuda_upgrades(
            project_dir, cuda_index_url, current_state,
        )
    except Exception as exc:
        _log.debug("CUDA-aware torch check failed for %s: %s", project_dir, exc)
        return {}

    result: Dict[str, UpgradeStatus] = {}
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
    cuda_index_url: Optional[str] = None,
) -> Dict[str, UpgradeStatus]:
    """Return {normalized_name: UpgradeStatus} for outdated packages."""
    p_dir = Path(project.project_dir)
    if not project.venv_exists:
        return {}

    try:
        raw: List[OutdatedPackage] = find_outdated_packages(p_dir)
    except Exception as exc:
        _log.debug("find_outdated_packages failed for %s: %s", project.name, exc)
        return {}

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

    result: Dict[str, UpgradeStatus] = {}

    if non_torch_raw:
        try:
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
        except Exception as exc:
            _log.debug("classify_safe_upgrades failed for %s: %s", project.name, exc)

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
            try:
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
            except Exception as exc:
                _log.debug("Torch classification failed for %s: %s", project.name, exc)

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

    missing: List[str] = []
    total_required = 0

    if req_file.exists():
        parsed = parse_requirements_file(req_file)
        for entry in parsed:
            if entry.is_index_url:
                continue
            if entry.marker and Marker:
                try:
                    if not Marker(entry.marker).evaluate():
                        continue
                except Exception as exc:
                    _log.debug(
                        "PEP 508 marker evaluation failed for '%s': %s",
                        entry.marker, exc,
                    )
            total_required += 1
            pkg_name = normalize_package_name(entry.name)
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
    projects: List[ProjectHealth],
    base_dir: Optional[Path] = None,
    cuda_index_url: Optional[str] = None,
    include_safety: bool = True,
) -> List[VersionDriftEntry]:
    """Identify version discrepancies, pin mismatches, and safe upgrades."""
    if not projects:
        return []

    project_pins: Dict[str, Dict[str, PinInfo]] = {}
    for p in projects:
        req_file = Path(p.project_dir) / "requirements.txt"
        project_pins[p.name] = _load_project_pins(req_file)

    declaration_counts: Dict[str, int] = {}
    for pins in project_pins.values():
        for name in pins.keys():
            declaration_counts[name] = declaration_counts.get(name, 0) + 1

    shared_packages = sorted(
        name for name, count in declaration_counts.items() if count >= 2
    )

    upgrade_map: Dict[str, Dict[str, UpgradeStatus]] = {}
    if include_safety:
        for p in projects:
            upgrade_map[p.name] = _classify_project_upgrades(
                p, cuda_index_url=cuda_index_url,
            )

    entries: List[VersionDriftEntry] = []
    for pkg in shared_packages:
        versions: Dict[str, Optional[str]] = {}
        pins: Dict[str, PinInfo] = {}
        upgrades: Dict[str, UpgradeStatus] = {}
        pin_types: Set[str] = set()
        observed_versions: Set[str] = set()

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
            else:
                if installed_ver:
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
    projects: List[ProjectHealth],
) -> List[ManifestCoverageEntry]:
    """Reconcile every manifest's declared set against its installed set."""
    entries: List[ManifestCoverageEntry] = []

    for p in projects:
        manifest = _load_project_manifest(Path(p.project_dir) / "requirements.txt")
        declared = set(manifest.pins.keys())

        installed_norm = {
            normalize_package_name(k): v for k, v in p.installed_packages.items()
        }
        installed = set(installed_norm.keys()) - _BOOTSTRAP_NORMALIZED

        missing = sorted(declared - installed)
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
    projects: List[ProjectHealth],
) -> List[SingleManifestEntry]:
    """Return every package declared in exactly one manifest."""
    project_manifests = {
        p.name: _load_project_manifest(Path(p.project_dir) / "requirements.txt")
        for p in projects
    }

    counts: Dict[str, int] = {}
    for manifest in project_manifests.values():
        for name in manifest.pins.keys():
            counts[name] = counts.get(name, 0) + 1

    entries: List[SingleManifestEntry] = []
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

def _read_node_modules_versions(workspace_dir: Path) -> Dict[str, str]:
    installed: Dict[str, str] = {}
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
                    pkg_json = scoped / "package.json"
                    if pkg_json.is_file():
                        try:
                            data = json.loads(pkg_json.read_text(encoding="utf-8"))
                            ver = data.get("version")
                            if ver:
                                installed[f"{name}/{scoped.name}"] = str(ver)
                        except Exception:
                            continue
            else:
                pkg_json = entry / "package.json"
                if pkg_json.is_file():
                    try:
                        data = json.loads(pkg_json.read_text(encoding="utf-8"))
                        ver = data.get("version")
                        if ver:
                            installed[name] = str(ver)
                    except Exception:
                        continue
    except Exception as exc:
        _log.debug("node_modules walk failed for %s: %s", workspace_dir, exc)

    return installed


def _read_package_json_specifiers(
    workspace_dir: Path,
) -> Tuple[Dict[str, str], Set[str]]:
    specifiers: Dict[str, str] = {}
    dev_names: Set[str] = set()
    pkg_json = workspace_dir / "package.json"
    if not pkg_json.is_file():
        return specifiers, dev_names

    try:
        data = json.loads(pkg_json.read_text(encoding="utf-8"))
    except Exception as exc:
        _log.debug("package.json parse failed for %s: %s", workspace_dir, exc)
        return specifiers, dev_names

    for name, spec in (data.get("dependencies") or {}).items():
        specifiers[name] = str(spec)
    for name, spec in (data.get("devDependencies") or {}).items():
        specifiers[name] = str(spec)
        dev_names.add(name)

    return specifiers, dev_names


def compute_npm_drift(base_dir: Path) -> List[NpmDriftEntry]:
    """Identify version drift for npm packages across workspaces."""
    workspaces: List[Path] = []
    if base_dir and base_dir.is_dir():
        for child in base_dir.iterdir():
            if not child.is_dir():
                continue
            if (child / "package.json").is_file() and (child / "node_modules").is_dir():
                workspaces.append(child)

    if not workspaces:
        return []

    workspace_versions: Dict[str, Dict[str, str]] = {}
    workspace_specifiers: Dict[str, Dict[str, str]] = {}
    workspace_dev: Dict[str, Set[str]] = {}

    for ws in workspaces:
        workspace_versions[ws.name] = _read_node_modules_versions(ws)
        specifiers, dev_names = _read_package_json_specifiers(ws)
        workspace_specifiers[ws.name] = specifiers
        workspace_dev[ws.name] = dev_names

    declaration_counts: Dict[str, int] = {}
    for spec_map in workspace_specifiers.values():
        for name in spec_map:
            declaration_counts[name] = declaration_counts.get(name, 0) + 1

    shared = sorted(name for name, count in declaration_counts.items() if count >= 2)

    entries: List[NpmDriftEntry] = []
    for pkg in shared:
        versions: Dict[str, Optional[str]] = {}
        specs: Dict[str, Optional[str]] = {}
        observed: Set[str] = set()
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
    "transitive": "·",
    "absent":     "",
}


def _format_cell(
    version: Optional[str],
    pin: Optional[PinInfo],
    upgrade: Optional[UpgradeStatus],
) -> str:
    """Format one drift-matrix cell."""
    if not version:
        return "-"

    pin_type = pin.pin_type if pin else ""
    glyph = _PIN_GLYPH.get(pin_type, "")
    base = f"{version}{glyph}" if glyph else version

    if upgrade and upgrade.is_outdated and upgrade.latest:
        arrow = "↑" if upgrade.safe else "⊘"
        return f"{base} → {upgrade.latest} {arrow}"

    return base


def render_drift_matrix(entries: List[VersionDriftEntry]) -> Optional["Table"]:
    """Return a Rich Table showing Python drift, pins, and upgrade status."""
    if not _RICH_AVAILABLE or Table is None:
        return None

    projects: List[str] = []
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
        row: List[str] = [entry.package_name]
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
    entries: List[ManifestCoverageEntry],
) -> Optional["Table"]:
    """Return a Rich Table showing declared-vs-installed coverage per project."""
    if not _RICH_AVAILABLE or Table is None:
        return None

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
    entries: List[SingleManifestEntry],
) -> Optional["Table"]:
    """Return a Rich Table showing packages declared in only one manifest."""
    if not _RICH_AVAILABLE or Table is None:
        return None

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


def render_npm_drift_matrix(entries: List[NpmDriftEntry]) -> Optional["Table"]:
    """Return a Rich Table showing npm workspace drift."""
    if not _RICH_AVAILABLE or Table is None:
        return None

    workspaces: List[str] = []
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
        row: List[str] = [entry.package_name]
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
    base_dir: Optional[Path] = None,
    include_safety: bool = True,
) -> HealthAuditReport:
    """Execute complete health audit across all projects and host system."""
    bootstrap = verify_prerequisites()
    hardware = probe_hardware()
    discovered = discover_projects(base_dir)

    projects_health: List[ProjectHealth] = []
    for d in discovered:
        if d.is_node_project:
            continue
        health = audit_project_health(d)
        projects_health.append(health)

    cuda_index_url = getattr(hardware, "recommended_torch_index", None)
    drift = compute_version_drift(
        projects_health,
        base_dir=base_dir,
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