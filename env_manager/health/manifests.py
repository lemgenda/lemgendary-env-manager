"""Reading a project's requirements manifest into pins, honouring environment markers."""

from pathlib import Path

from packaging.markers import (
    InvalidMarker,
    Marker,
    UndefinedComparison,
    UndefinedEnvironmentName,
)

from env_manager._logging import get_logger
from env_manager.health.models import PinInfo, ProjectManifest, normalize_package_name
from env_manager.requirements_manager import parse_requirements_file

_log = get_logger(__name__)


def classify_pin_type(specifier: str) -> str:
    """Return "floating" for no specifier, "exact" for a single ``==`` pin, else "range"."""
    spec = (specifier or "").strip()
    if not spec:
        return "floating"
    if spec.startswith("==") and "," not in spec:
        return "exact"
    return "range"


def marker_matches(marker: str) -> bool:
    """Evaluate a PEP 508 marker for this platform.

    An unparsable or non-evaluable marker counts as matching (the entry is
    kept as declared, the conservative choice) and the reason is logged.
    """
    try:
        return bool(Marker(marker).evaluate())
    except (InvalidMarker, UndefinedComparison, UndefinedEnvironmentName) as exc:
        _log.warning("PEP 508 marker %r could not be evaluated, keeping entry: %s", marker, exc)
        return True


def load_project_manifest(req_file: Path) -> ProjectManifest:
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

        if entry.marker and not marker_matches(entry.marker):
            manifest.platform_skipped.append(entry.name)
            continue

        name = normalize_package_name(entry.name)
        manifest.pins[name] = PinInfo(
            pin_type=classify_pin_type(entry.specifier),
            specifier=entry.specifier or "",
        )

    return manifest


# Keep the old name as a thin compatibility shim for callers that only
# need the pin map.
def load_project_pins(req_file: Path) -> dict[str, PinInfo]:
    """Return the pins of ``req_file`` keyed by normalized package name."""
    return load_project_manifest(req_file).pins
