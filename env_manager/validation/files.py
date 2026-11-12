"""Walking a project tree for the per-file gates."""

from collections.abc import Iterator
from pathlib import Path

from env_manager._logging import get_logger

_log = get_logger(__name__)

MAX_SCAN_BYTES = 1024 * 1024
"""Files larger than this are skipped by the per-file gates."""

SKIP_DIRS = frozenset({
    ".git", ".venv", "node_modules", "__pycache__", "dist", "build", "target",
    ".pytest_cache", "raw-sets", "checkpoints", "weights", "data",
    "scratch", ".cache", "images", "targets", "labels", "train", "val", "test",
    "lr", "hr", "export", "runs", ".lemtrain_server", ".lgd_server", ".env_server",
})
"""Directory names never scanned: build output, caches, venvs and dataset trees."""

TEXT_FILE_EXTENSIONS = frozenset({
    ".py", ".ps1", ".psm1", ".sh", ".bat", ".cmd", ".ts", ".tsx", ".js", ".jsx", ".mjs",
    ".cjs", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".md", ".html", ".css",
    ".ipynb", ".rs",
})
"""Every text file type that the text scans read (code, config and docs)."""

SCANNED_HIDDEN_DIRS = frozenset({".githooks", ".github", ".agents"})
"""Hidden folders that hold project code (hooks, CI workflows, agent skills)."""

HOOKS_DIR = ".githooks"
"""Git hooks have no file extension; every file in this folder is text."""


def is_text_source(path: Path) -> bool:
    """True when ``path`` is a text file the text scans read."""
    return path.suffix.lower() in TEXT_FILE_EXTENSIONS or HOOKS_DIR in path.parts


def iter_project_files(project_dir: Path) -> Iterator[Path]:
    """Yield every file under ``project_dir`` that a per-file gate may read.

    Skips ``SKIP_DIRS``, hidden directories other than ``SCANNED_HIDDEN_DIRS``
    and files over ``MAX_SCAN_BYTES``.
    A file that cannot be stat'ed is logged and skipped.
    """
    for root, dirs, files in Path(project_dir).walk():
        dirs[:] = [
            d for d in dirs
            if d not in SKIP_DIRS and (not d.startswith(".") or d in SCANNED_HIDDEN_DIRS)
        ]
        for name in files:
            path = root / name
            try:
                if path.stat().st_size > MAX_SCAN_BYTES:
                    continue
            except OSError as exc:
                _log.warning("Cannot stat %s, skipping: %s", path, exc)
                continue
            yield path
