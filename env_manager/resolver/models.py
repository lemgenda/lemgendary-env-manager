"""The outdated-package record and requirement helpers shared by the resolver modules."""

from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass
from typing import Any

from packaging.markers import UndefinedComparison, UndefinedEnvironmentName
from packaging.requirements import InvalidRequirement, Requirement

from env_manager._logging import get_logger

_log = get_logger(__name__)


@dataclass
class OutdatedPackage:
    """Represents a package that has a newer version available."""
    name: str
    current_version: str
    latest_version: str
    package_type: str

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a plain dict for JSON output."""
        return asdict(self)


def normalize(name: str) -> str:
    """Return the key used to compare package names: lower case, ``-`` and ``.`` as ``_``."""
    return name.lower().replace("-", "_").replace(".", "_")


def marker_applies(req: Requirement) -> bool:
    """Return True when ``req`` has no marker or its marker holds here.

    A marker that cannot be evaluated in this environment is treated as not
    applying; the reason is logged so a malformed requirement stays visible.
    """
    if req.marker is None:
        return True
    try:
        return bool(req.marker.evaluate())
    except (UndefinedComparison, UndefinedEnvironmentName) as exc:
        _log.debug("Skipping requirement %s: marker not evaluable: %s", req, exc)
        return False


def active_requirements(req_strs: Iterable[str], owner: str) -> Iterator[Requirement]:
    """Yield the requirements of ``owner`` that constrain a standard install here.

    Unparsable requirements are logged and skipped. Extras-conditional
    requirements (optional groups such as ``package[testing]``) are skipped
    too: they are not active constraints, and treating them as such causes
    false blocker reports. The extra is detected through ``str(marker)``, a
    stable public API. Requirements whose marker does not hold are skipped.
    """
    for req_str in req_strs:
        try:
            req = Requirement(req_str)
        except InvalidRequirement as exc:
            _log.debug("Ignoring unparsable requirement %r of %s: %s", req_str, owner, exc)
            continue
        if req.marker is not None and "extra" in str(req.marker):
            continue
        if marker_applies(req):
            yield req
