"""Node.js and NPM package management module.

Audits and tracks package.json dependencies (e.g. markdownlint-cli,
html-validator-cli) across repository roots and documentation packages.
"""

import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from collections.abc import Callable, Generator

from env_manager._logging import get_logger
from env_manager.utils import WINDOWS_NO_WINDOW, drain_lines, pip_env, stream_process_lines
from env_manager.workspace import workspace_root

_log = get_logger(__name__)


@dataclass
class NpmDependencyDetail:
    """Detailed information for a single NPM package dependency."""
    name: str
    declared_version: str
    installed_version: str | None
    is_dev: bool
    is_installed: bool

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a plain dict for JSON output."""
        return asdict(self)


@dataclass
class NpmPackageStatus:
    """Status of an NPM package file."""
    location: str
    package_json_path: str
    dependencies: dict[str, str]
    dev_dependencies: dict[str, str]
    node_modules_present: bool
    detailed_packages: list[NpmDependencyDetail]
    audit_summary: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the fields as a plain dict for JSON output."""
        return asdict(self)


def check_npm_package(package_dir: Path) -> NpmPackageStatus | None:
    """Inspect package.json at a given directory."""
    pkg_json_file = package_dir / "package.json"
    if not pkg_json_file.exists():
        return None

    try:
        data = json.loads(pkg_json_file.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            _log.warning("package.json at %s is not a JSON object", package_dir)
            return None
        deps = data.get("dependencies", {})
        dev_deps = data.get("devDependencies", {})
        nm_dir = package_dir / "node_modules"
        node_modules = nm_dir.exists()

        detailed_pkgs: list[NpmDependencyDetail] = []

        def _inspect_pkg(name: str, decl_ver: str, is_dev: bool):
            inst_ver = None
            # Scoped packages live under node_modules/@scope/name.
            if name.startswith("@"):
                scope, sep, short_name = name.partition("/")
                if sep:
                    pkg_nm_json = nm_dir / scope / short_name / "package.json"
                else:
                    pkg_nm_json = nm_dir / name / "package.json"
            else:
                pkg_nm_json = nm_dir / name / "package.json"

            if pkg_nm_json.exists():
                try:
                    p_data = json.loads(pkg_nm_json.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    _log.warning("Unreadable installed package manifest %s: %s", pkg_nm_json, exc)
                else:
                    if isinstance(p_data, dict):
                        inst_ver = p_data.get("version")
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
    except (OSError, ValueError) as exc:
        _log.warning("Failed to parse package.json at %s: %s", package_dir, exc)
        return None


def stream_npm_install(
    package_dir: Path,
    timeout: int = 600,
) -> Generator[str, None, tuple[bool, str]]:
    """Execute npm install in the given directory and stream output lines."""
    npm_path = shutil.which("npm")
    if not npm_path:
        return False, "NPM executable not found on system PATH."

    try:
        with subprocess.Popen(
            [npm_path, "install"],
            cwd=str(package_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=pip_env(),
            creationflags=WINDOWS_NO_WINDOW,
        ) as proc:
            lines: list[str] = []
            for _, raw_line in stream_process_lines(proc, timeout):
                stripped = raw_line.strip()
                if stripped:
                    lines.append(stripped)
                    yield stripped
            if proc.returncode == 0:
                return True, "\n".join(lines) or "ok"
            return False, "\n".join(lines) or "NPM install failed."
    except subprocess.TimeoutExpired as exc:
        _log.warning("npm install in %s timed out after %ss", package_dir, timeout)
        return False, f"npm install timed out after {timeout}s: {exc}"
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        _log.warning("npm install in %s could not be run: %s", package_dir, exc)
        return False, str(exc)


def run_npm_install(
    package_dir: Path,
    on_line: Callable[[str], None] | None = None,
) -> tuple[bool, str]:
    """Execute npm install in the given directory."""
    return drain_lines(stream_npm_install(package_dir), on_line)


def audit_npm_workspaces(base_dir: Path | None = None) -> list[NpmPackageStatus]:
    """Audit all known NPM package directories in the LemGendary ecosystem."""
    if base_dir is None:
        base_dir = workspace_root()

    target_dirs = [
        base_dir / "lemgendary-env-manager",
        base_dir / "lemgendary-ai-studio-gui",
    ]

    results: list[NpmPackageStatus] = []
    for d in target_dirs:
        if d.exists() and d.is_dir():
            status = check_npm_package(d)
            if status:
                results.append(status)

    return results
