"""
LemGendary Environment Manager Shared Utilities
================================================
Common helpers for cache reclamation, process execution, and version inspection.
"""

import shutil
import subprocess
from pathlib import Path
from typing import Optional, Tuple


def purge_project_cache(project_dir: Path) -> Tuple[int, int]:
    """Purge __pycache__ and bytecode artifacts from a project directory, ignoring .venv."""
    cleaned_count = 0
    total_reclaimed = 0

    for item in project_dir.rglob("__pycache__"):
        if ".venv" in item.parts:
            continue
        if item.is_dir():
            try:
                size = sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
                shutil.rmtree(item)
                total_reclaimed += size
                cleaned_count += 1
            except Exception:
                pass

    for pattern in ["*.pyc", "*.pyo", "*.pyd"]:
        for item in project_dir.rglob(pattern):
            if ".venv" in item.parts:
                continue
            if item.is_file():
                try:
                    size = item.stat().st_size
                    item.unlink()
                    total_reclaimed += size
                    cleaned_count += 1
                except Exception:
                    pass

    return cleaned_count, total_reclaimed


def run_command_simple(
    cmd: list[str],
    timeout: int = 5,
    cwd: Optional[Path] = None
) -> subprocess.CompletedProcess[str]:
    """Execute a simple subprocess with consistent timeout and capture defaults."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(cwd) if cwd else None,
        check=False,
    )
