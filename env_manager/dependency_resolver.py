"""Dependency resolution and safe upgrade module."""



from env_manager._logging import get_logger
from env_manager.resolver.classify import classify_safe_upgrades
from env_manager.resolver.dry_run import constraint_preserving_dry_run
from env_manager.resolver.models import OutdatedPackage
from env_manager.resolver.pip_json import (
    PipJsonError,
    find_outdated_packages,
    installed_package_versions,
    venv_has_real_packages,
    venv_package_count,
)

_log = get_logger(__name__)


__all__ = [
    "OutdatedPackage",
    "PipJsonError",
    "classify_safe_upgrades",
    "constraint_preserving_dry_run",
    "find_outdated_packages",
    "installed_package_versions",
    "venv_has_real_packages",
    "venv_package_count",
]
