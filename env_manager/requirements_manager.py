"""Requirements manifest synchronization and parsing module.

Maintains centralized manifests and synchronizes them to target project roots,
ensuring environment markers and index URLs are properly configured.
"""

import re
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from packaging.requirements import Requirement
except ImportError:
    Requirement = None

from env_manager._logging import get_logger

_log = get_logger(__name__)


@dataclass
class ParsedRequirement:
    """Individual parsed requirement entry."""
    raw_line: str
    name: str
    specifier: str
    marker: Optional[str] = None
    is_editable: bool = False
    is_index_url: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert entry to dictionary."""
        return asdict(self)


def parse_requirements_file(file_path: Path) -> List[ParsedRequirement]:
    """Parse requirements file into structured entries."""
    if not file_path.exists():
        return []

    results: List[ParsedRequirement] = []
    lines = file_path.read_text(encoding="utf-8").splitlines()

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped.startswith("--extra-index-url") or stripped.startswith("-i "):
            results.append(
                ParsedRequirement(
                    raw_line=stripped,
                    name="__index_url__",
                    specifier=stripped,
                    is_index_url=True,
                )
            )
            continue

        if stripped.startswith("-e "):
            results.append(
                ParsedRequirement(
                    raw_line=stripped,
                    name=stripped.replace("-e ", "").strip(),
                    specifier="",
                    is_editable=True,
                )
            )
            continue

        if Requirement:
            try:
                req = Requirement(stripped)
                marker_str = str(req.marker) if req.marker else None
                spec_str = str(req.specifier) if req.specifier else ""
                results.append(
                    ParsedRequirement(
                        raw_line=stripped,
                        name=req.name.lower(),
                        specifier=spec_str,
                        marker=marker_str,
                    )
                )
                continue
            except Exception as exc:
                _log.debug("packaging.Requirement parse failed for line '%s': %s", stripped, exc)

        # Fallback regex parser
        match = re.match(r"^([A-Za-z0-9_\-\.\[\]]+)\s*([<>=!~]+[^;]*)?(?:;\s*(.*))?$", stripped)
        if match:
            name = match.group(1).lower()
            spec = (match.group(2) or "").strip()
            marker = (match.group(3) or "").strip() or None
            results.append(
                ParsedRequirement(
                    raw_line=stripped,
                    name=name,
                    specifier=spec,
                    marker=marker,
                )
            )
        else:
            results.append(
                ParsedRequirement(
                    raw_line=stripped,
                    name=stripped.lower(),
                    specifier="",
                )
            )
    return results


def sync_manifest_to_project(
    manifest_path: Path,
    target_project_dir: Path,
) -> tuple[bool, str]:
    """Synchronize a centralized requirements manifest into a project directory."""
    if not manifest_path.exists():
        return False, f"Manifest file not found: {manifest_path}"

    target_req_path = target_project_dir / "requirements.txt"
    try:
        shutil.copy2(manifest_path, target_req_path)
        return True, f"Synchronized {manifest_path.name} to {target_req_path}."
    except Exception as exc:
        return False, f"Failed to synchronize manifest: {exc}"


def sync_all_manifests(base_dir: Optional[Path] = None) -> Dict[str, tuple[bool, str]]:
    """Synchronize all centralized manifests to their respective sibling projects."""
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent.parent

    env_mgr_dir = base_dir / "lemgendary-env-manager"
    manifests_dir = env_mgr_dir / "requirements"

    mapping = {
        "lemgendary-training-suite": manifests_dir / "requirements-training.txt",
        "lemgendary-datasets": manifests_dir / "requirements-datasets.txt",
        "lemgendary-env-manager": manifests_dir / "requirements-env-manager.txt",
    }

    results: Dict[str, tuple[bool, str]] = {}
    for proj_name, manifest in mapping.items():
        proj_dir = base_dir / proj_name
        if proj_dir.exists() and proj_dir.is_dir():
            res = sync_manifest_to_project(manifest, proj_dir)
            results[proj_name] = res
        else:
            results[proj_name] = (False, f"Project directory does not exist: {proj_dir}")

    # Synchronize GUI NPM package manifest
    gui_manifest = manifests_dir / "lemgendary-ai-studio-gui.package.json"
    gui_dir = base_dir / "lemgendary-ai-studio-gui"
    if gui_manifest.exists() and gui_dir.exists() and gui_dir.is_dir():
        try:
            shutil.copy2(gui_manifest, gui_dir / "package.json")
            results["lemgendary-ai-studio-gui"] = (True, f"Synchronized {gui_manifest.name} to {gui_dir / 'package.json'}.")
        except Exception as exc:
            results["lemgendary-ai-studio-gui"] = (False, f"Failed to synchronize GUI manifest: {exc}")

    return results
