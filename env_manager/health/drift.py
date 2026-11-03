"""Cross-project Python drift: version drift, manifest coverage and single-manifest packages."""

from pathlib import Path

from env_manager.health.manifests import load_project_manifest, load_project_pins
from env_manager.health.models import (
    BOOTSTRAP_NORMALIZED,
    MIN_SHARED_DECLARATIONS,
    OPENCV_VARIANTS_NORMALIZED,
    ManifestCoverageEntry,
    PinInfo,
    ProjectHealth,
    SingleManifestEntry,
    UpgradeStatus,
    VersionDriftEntry,
    normalize_package_name,
)
from env_manager.health.project import _classify_project_upgrades


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
        project_pins[p.name] = load_project_pins(req_file)

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

    # Manifest names are PEP 503-normalized; pip reports raw names
    # (``pydantic_core``), so normalize the installed keys the same way.
    installed_by_project = {
        p.name: {normalize_package_name(k): v for k, v in p.installed_packages.items()}
        for p in projects
    }

    entries: list[VersionDriftEntry] = []
    for pkg in shared_packages:
        versions: dict[str, str | None] = {}
        pins: dict[str, PinInfo] = {}
        upgrades: dict[str, UpgradeStatus] = {}
        pin_types: set[str] = set()
        observed_versions: set[str] = set()

        for p in projects:
            pin = project_pins.get(p.name, {}).get(pkg)
            installed_ver = installed_by_project[p.name].get(pkg) or None

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


def compute_manifest_coverage(
    projects: list[ProjectHealth],
) -> list[ManifestCoverageEntry]:
    """Reconcile every manifest's declared set against its installed set."""
    entries: list[ManifestCoverageEntry] = []

    for p in projects:
        manifest = load_project_manifest(Path(p.project_dir) / "requirements.txt")
        declared = set(manifest.pins.keys())

        installed_norm = {
            normalize_package_name(k): v for k, v in p.installed_packages.items()
        }
        installed = set(installed_norm.keys()) - BOOTSTRAP_NORMALIZED

        missing_set = declared - installed
        if any(v in installed for v in OPENCV_VARIANTS_NORMALIZED):
            missing_set = {
                pkg for pkg in missing_set if pkg not in OPENCV_VARIANTS_NORMALIZED
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
        p.name: load_project_manifest(Path(p.project_dir) / "requirements.txt")
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
