"""Hermetic workspace for every test.

Each test gets a minimal fake workspace under ``tmp_path`` and the
workspace root is pointed at it, so no test reads or writes the real
sibling repositories. Git is fenced in with ``GIT_CEILING_DIRECTORIES`` so
it cannot discover a repository above the fixture.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path

import pytest

from env_manager.workspace import WORKSPACE_ROOT_ENV

_README = "# {name}\n\nFixture repository.\n\n## Changelog\n\n### v1.0.0\n\n- Fixture.\n"

_PYTHON_PROJECTS = (
    "lemgendary-env-manager",
    "lemgendary-datasets",
    "lemgendary-training-suite",
    "lemgendary-docs",
)

_MANIFESTS = (
    "requirements-training.txt",
    "requirements-datasets.txt",
    "requirements-env-manager.txt",
    "requirements-documentation.txt",
)

_SESSION_ROOT = pytest.StashKey[Path]()


def build_fake_workspace(root: Path) -> Path:
    """Create the smallest workspace the CLI, server and validator accept."""
    for name in (*_PYTHON_PROJECTS, "lemgendary-ai-studio-gui"):
        project = root / name
        project.mkdir(parents=True)
        (project / "README.md").write_text(_README.format(name=name), encoding="utf-8")

    manifests = root / "lemgendary-env-manager" / "requirements"
    manifests.mkdir()
    for manifest in _MANIFESTS:
        (manifests / manifest).write_text("rich>=13.0.0\n", encoding="utf-8")

    docs = root / "lemgendary-docs"
    baseline = {"document_baseline": "fixture"}
    (docs / "manifest").mkdir()
    (docs / "manifest" / "documents.json").write_text(
        json.dumps({**baseline, "total_publications": 0, "documents": []}, indent=2) + "\n",
        encoding="utf-8",
    )
    (docs / "canonical_capabilities.json").write_text(
        json.dumps(baseline, indent=2) + "\n", encoding="utf-8"
    )
    (docs / "canonical_capabilities.yaml").write_text(
        "---\ndocument_baseline: fixture\n", encoding="utf-8"
    )
    return root


def pytest_configure(config: pytest.Config) -> None:
    """Point import-time workspace lookups at an empty folder, never the real one."""
    session_root = Path(tempfile.mkdtemp(prefix="lem-env-session-"))
    config.stash[_SESSION_ROOT] = session_root
    os.environ[WORKSPACE_ROOT_ENV] = str(session_root)
    os.environ["GIT_CEILING_DIRECTORIES"] = str(session_root.parent)


def pytest_unconfigure(config: pytest.Config) -> None:
    """Remove the session folder created in :func:`pytest_configure`."""
    session_root = config.stash.get(_SESSION_ROOT, None)
    if session_root is not None:
        shutil.rmtree(session_root)


@pytest.fixture(autouse=True)
def fake_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a fake workspace in ``tmp_path`` and make it the workspace root."""
    root = build_fake_workspace(tmp_path / "workspace")
    monkeypatch.setenv(WORKSPACE_ROOT_ENV, str(root))
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    return root
