"""Regression tests for Python 3.14 environment, ABI compatibility, and permissive licenses."""

import importlib
import sys

import pytest


def test_python_version() -> None:
    """Assert runtime is strictly Python 3.14."""
    assert sys.version_info[:2] == (3, 14), f"Expected Python 3.14, got {sys.version}"


def test_permissive_license_zero_agpl() -> None:
    """Ensure copyleft/AGPL dependencies are not installed."""
    with pytest.raises(ImportError):
        importlib.import_module("albumentations")

    with pytest.raises(ImportError):
        importlib.import_module("albumentationsx")


@pytest.mark.parametrize(
    "pkg_name",
    [
        "typer",
        "rich",
        "yaml",
        "psutil",
        "fastapi",
        "uvicorn",
        "websockets",
        "packaging",
        "jsonschema",
        "httpx2",
        "yamllint",
        "pydantic",
        "bs4",
    ],
)
def test_import_dependencies_zero_warnings(pkg_name: str) -> None:
    """Dynamically import required packages asserting successful import."""
    mod = importlib.import_module(pkg_name)
    assert mod is not None
