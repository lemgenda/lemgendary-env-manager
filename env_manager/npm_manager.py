"""Node.js and NPM package management module.

Audits and tracks package.json dependencies (e.g. markdownlint-cli,
html-validator-cli) across repository roots and documentation packages.
"""

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from env_manager._logging import get_logger
from env_manager.utils import pip_env

_log = get_logger(__name__)


@dataclass
class NpmDependencyDetail:
    """Detailed information for a single NPM package dependency."""
    name: str
    declared_version: str
    installed_version: Optional[str]
    is_dev: bool
    is_installed: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class NpmPackageStatus:
    """Status of an NPM package file."""
    location: str
    package_json_path: str
    dependencies: Dict[str, str]
    dev_dependencies: Dict[str, str]
    node_modules_present: bool
    detailed_packages: List[NpmDependencyDetail]
    audit_summary: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def check_npm_package(package_dir: Path) -> Optional[NpmPackageStatus]:
    """Inspect package.json at a given directory."""
    pkg_json_file = package_dir / "package.json"
    if not pkg_json_file.exists():
        return None

    try:
        data = json.loads(pkg_json_file.read_text(encoding="utf-8"))
        deps = data.get("dependencies", {})
        dev_deps = data.get("devDependencies", {})
        nm_dir = package_dir / "node_modules"
        node_modules = nm_dir.exists()

        detailed_pkgs: List[NpmDependencyDetail] = []

        def _inspect_pkg(name: str, decl_ver: str, is_dev: bool):
            inst_ver = None
            # Scoped packages live under node_modules/@scope/name.
            if name.startswith("@"):
                parts = name.split("/", 1)
                if len(parts) == 2:
                    pkg_nm_json = nm_dir / parts[0] / parts[1] / "package.json"
                else:
                    pkg_nm_json = nm_dir / name / "package.json"
            else:
                pkg_nm_json = nm_dir / name / "package.json"

            if pkg_nm_json.exists():
                try:
                    p_data = json.loads(pkg_nm_json.read_text(encoding="utf-8"))
                    inst_ver = p_data.get("version")
                except Exception:
                    inst_ver = None
            detailed_pkgs.append(
                NpmDependencyDetail(
                    name=name,
                    declared_version=decl_ver,
                    installed_version=inst_ver,
                    is_dev=is_dev,
                    is_installed=inst_ver is not None,
                )
            )

        for k, v in deps.items():
            _inspect_pkg(k, v, False)
        for k, v in dev_deps.items():
            _inspect_pkg(k, v, True)

        return NpmPackageStatus(
            location=package_dir.name or str(package_dir),
            package_json_path=str(pkg_json_file),
            dependencies=deps,
            dev_dependencies=dev_deps,
            node_modules_present=node_modules,
            detailed_packages=detailed_pkgs,
        )
    except Exception as exc:
        _log.warning("Failed to parse package.json at %s: %s", package_dir, exc)
        return None


def run_npm_install(package_dir: Path) -> tuple[bool, str]:
    """Execute npm install in the given directory.

    Uses ``pip_env()`` so the subprocess inherits UTF-8 encoding and colour
    disabling. npm ignores the ``PIP_*`` variables, but the ``PYTHONIOENCODING``
    and ``NO_COLOR`` settings apply to any tool's output and prevent the
    cp1252 crash that pip had.
    """
    npm_path = shutil.which("npm")
    if not npm_path:
        return False, "NPM executable not found on system PATH."

    try:
        proc = subprocess.run(
            [npm_path, "install"],
            cwd=str(package_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
            check=False,
            env=pip_env(),
        )
        if proc.returncode == 0:
            return True, proc.stdout
        return False, proc.stderr or proc.stdout or "NPM install failed."
    except subprocess.TimeoutExpired as exc:
        return False, f"npm install timed out after 600s: {exc}"
    except Exception as exc:
        return False, str(exc)


def audit_npm_workspaces(base_dir: Optional[Path] = None) -> List[NpmPackageStatus]:
    """Audit all known NPM package directories in the LemGendary ecosystem."""
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent.parent

    target_dirs = [
        base_dir / "lemgendary-env-manager",
        base_dir / "lemgendary-ai-studio-gui",
    ]

    results: List[NpmPackageStatus] = []
    for d in target_dirs:
        if d.exists() and d.is_dir():
            status = check_npm_package(d)
            if status:
                results.append(status)

    return results