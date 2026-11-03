"""Writing installed venv versions back into the requirements manifests."""

import re
import shutil
from pathlib import Path

from env_manager._logging import get_logger
from env_manager.upgrade.models import PROTECTED_FROM_SYNC
from env_manager.venv_manager import (
    get_installed_packages,
    is_venv_valid,
)
from env_manager.workspace import workspace_root

_log = get_logger(__name__)

PROJECT_TO_MANIFEST: dict[str, str] = {
    "lemgendary-training-suite": "requirements-training.txt",
    "lemgendary-datasets": "requirements-datasets.txt",
    "lemgendary-env-manager": "requirements-env-manager.txt",
    "lemgendary-docs": "requirements-documentation.txt",
}

_PASSTHROUGH_PREFIXES = ("#", "--extra-index-url", "--index-url", "-i ")


def _synced_line(line: str, installed: dict[str, str]) -> str:
    """Return ``line`` re-pinned to the installed version, or unchanged.

    Blank lines, comments, index options, unparseable lines, protected
    packages and packages missing from the venv pass through untouched.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith(_PASSTHROUGH_PREFIXES):
        return line
    match = re.match(r"^([A-Za-z0-9_\-\.]+)", stripped)
    if not match:
        return line
    raw_name = match.group(1)
    pkg_name = raw_name.lower().replace("_", "-").replace(".", "-")
    if pkg_name in PROTECTED_FROM_SYNC:
        return line
    installed_ver = installed.get(pkg_name) or installed.get(raw_name.lower())
    if not installed_ver:
        return line
    marker_match = re.search(r";(.+)$", stripped)
    marker = f"; {marker_match.group(1).strip()}" if marker_match else ""
    return f"{raw_name}=={installed_ver}{marker}"


def _sync_project(p_dir: Path, manifest_path: Path) -> tuple[bool, str]:
    """Re-pin one project's manifest from its venv and copy it into the project."""
    if not p_dir.exists():
        return False, f"Project directory not found: {p_dir}"
    if not is_venv_valid(p_dir):
        return False, "No valid venv found."
    installed = get_installed_packages(p_dir)
    if not installed:
        return False, "No installed packages."

    existing_lines: list[str] = []
    if manifest_path.exists():
        existing_lines = manifest_path.read_text(encoding="utf-8").splitlines()
    new_lines = [_synced_line(line, installed) for line in existing_lines]
    try:
        manifest_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        shutil.copy2(manifest_path, p_dir / "requirements.txt")
    except OSError as exc:
        _log.error("Manifest sync for %s failed: %s", p_dir.name, exc)
        return False, str(exc)
    return True, f"Manifest {manifest_path.name} updated and synced."


def sync_manifests_from_venvs(
    base_dir: Path | None = None,
) -> dict[str, tuple[bool, str]]:
    """Read installed versions from each venv and write back to manifests.

    Packages in :data:`PROTECTED_FROM_SYNC` are passed through untouched,
    regardless of the venv's current state. This prevents a transient
    install-time drift from permanently corrupting a manifest.
    """
    if base_dir is None:
        base_dir = workspace_root()

    manifests_dir = base_dir / "lemgendary-env-manager" / "requirements"
    manifests_dir.mkdir(parents=True, exist_ok=True)

    return {
        proj_name: _sync_project(base_dir / proj_name, manifests_dir / manifest_name)
        for proj_name, manifest_name in PROJECT_TO_MANIFEST.items()
    }
