"""The Python version contract has one value (REVIEW_REPORT D-17)."""

import tomllib
from pathlib import Path

from env_manager.bootstrap import MIN_PYTHON

ROOT = Path(__file__).resolve().parent.parent


def test_requires_python_matches_bootstrap_minimum() -> None:
    """``requires-python`` and the bootstrap check agree, and both require 3.12."""
    with open(ROOT / "pyproject.toml", "rb") as handle:
        project = tomllib.load(handle)["project"]
    assert project["requires-python"] == f">={MIN_PYTHON[0]}.{MIN_PYTHON[1]}"
    assert MIN_PYTHON >= (3, 12)
    ruff_target = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert ruff_target["tool"]["ruff"]["target-version"] == f"py{MIN_PYTHON[0]}{MIN_PYTHON[1]}"
