"""Pylint is pinned and falls back to the env-manager config (audit 4.2.1)."""

import subprocess
from pathlib import Path
from unittest.mock import patch

from env_manager.validation.gates import pylint_gate
from env_manager.validation.gates.pylint_gate import DEFAULT_PYLINTRC, rcfile_args

REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements"
PYTHON_MANIFESTS = (
    "requirements-env-manager.txt",
    "requirements-datasets.txt",
    "requirements-training.txt",
    "requirements-documentation.txt",
)


def test_every_python_manifest_pins_pylint() -> None:
    """Every python manifest pins pylint."""
    for name in PYTHON_MANIFESTS:
        lines = (REQUIREMENTS / name).read_text(encoding="utf-8").splitlines()
        assert any(line.startswith("pylint==") for line in lines), name


def test_project_without_config_uses_env_manager_pylintrc(tmp_path: Path) -> None:
    """Project without config uses env manager pylintrc."""
    assert DEFAULT_PYLINTRC.is_file()
    assert rcfile_args(tmp_path) == [f"--rcfile={DEFAULT_PYLINTRC}"]


def test_project_pylintrc_is_respected(tmp_path: Path) -> None:
    """Project pylintrc is respected."""
    (tmp_path / ".pylintrc").write_text("[MASTER]\n", encoding="utf-8")
    assert not rcfile_args(tmp_path)


def test_pyproject_counts_only_with_a_pylint_section(tmp_path: Path) -> None:
    """Pyproject counts only with a pylint section."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("[tool.ruff]\nline-length = 100\n", encoding="utf-8")
    assert rcfile_args(tmp_path) == [f"--rcfile={DEFAULT_PYLINTRC}"]
    pyproject.write_text("[tool.pylint.main]\njobs = 1\n", encoding="utf-8")
    assert not rcfile_args(tmp_path)


def test_rcfile_reaches_the_pylint_command(tmp_path: Path) -> None:
    """Rcfile reaches the pylint command."""
    (tmp_path / "a.py").write_text("X = 1\n", encoding="utf-8")
    done = subprocess.CompletedProcess([], 0, stdout="[]", stderr="")
    with patch.object(pylint_gate, "run_tool", return_value=done) as run:
        assert not pylint_gate.run_pylint(tmp_path)
    assert f"--rcfile={DEFAULT_PYLINTRC}" in run.call_args.args[0]
