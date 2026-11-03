"""Walking a project tree for the per-file gates."""

from collections.abc import Iterator
from pathlib import Path

from env_manager._logging import get_logger

_log = get_logger(__name__)

MAX_SCAN_BYTES = 1024 * 1024
"""Files larger than this are skipped by the per-file gates."""

SKIP_DIRS = frozenset({
    ".git", ".venv", "node_modules", "__pycache__", "dist", "build", "target",
    ".pytest_cache", "raw-sets", "checkpoints", "weights", ".agents", "data",
    "scratch", ".cache", "images", "targets", "labels", "train", "val", "test",
    "lr", "hr", "export", "runs", ".lemtrain_server", ".lgd_server", ".env_server",
})
"""Directory names never scanned: build output, caches, venvs and dataset trees."""

SCANNABLE_EXTENSIONS = frozenset({
    ".py", ".ps1", ".json", ".yaml", ".yml", ".md", ".toml",
    ".ts", ".tsx", ".html", ".css",
})
"""Text file types that the source-text gates read."""


def iter_project_files(project_dir: Path) -> Iterator[Path]:
    """Yield every file under ``project_dir`` that a per-file gate may read.

    Skips ``SKIP_DIRS``, hidden directories and files over ``MAX_SCAN_BYTES``.
    A file that cannot be stat'ed is logged and skipped.
    """
    for root, dirs, files in Path(project_dir).walk():
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            path = root / name
            try:
                if path.stat().st_size > MAX_SCAN_BYTES:
                    continue
            except OSError as exc:
                _log.warning("Cannot stat %s, skipping: %s", path, exc)
                continue
            yield path
