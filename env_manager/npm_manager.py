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


@dataclass
class NpmPackageStatus:
    """Status of an NPM package file."""
    location: str
    package_json_path: str
    dependencies: Dict[str, str]
    dev_dependencies: Dict[str, str]
    node_modules_present: bool
    audit_summary: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert status to dictionary."""
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
        node_modules = (package_dir / "node_modules").exists()

        return NpmPackageStatus(
            location=package_dir.name or str(package_dir),
            package_json_path=str(pkg_json_file),
            dependencies=deps,
            dev_dependencies=dev_deps,
            node_modules_present=node_modules,
        )
    except Exception:
        return None


def run_npm_install(package_dir: Path) -> tuple[bool, str]:
    """Execute npm install in the given directory."""
    npm_path = shutil.which("npm")
    if not npm_path:
        return False, "NPM executable not found on system PATH."

    try:
        proc = subprocess.run(
            [npm_path, "install"],
            cwd=str(package_dir),
            capture_output=True,
            text=True,
            timeout=180,
            check=False,
        )
        if proc.returncode == 0:
            return True, proc.stdout
        return False, proc.stderr or proc.stdout or "NPM install failed."
    except Exception as exc:
        return False, str(exc)


def audit_npm_workspaces(base_dir: Optional[Path] = None) -> List[NpmPackageStatus]:
    """Audit all known NPM package directories in the LemGendary ecosystem."""
    if base_dir is None:
        base_dir = Path(__file__).resolve().parent.parent.parent

    target_dirs = [
        base_dir,
        base_dir / "lemgendary-docs",
        base_dir / "lemgendary-ai-studio-gui",
    ]

    results: List[NpmPackageStatus] = []
    for d in target_dirs:
        if d.exists() and d.is_dir():
            status = check_npm_package(d)
            if status:
                results.append(status)

    return results
