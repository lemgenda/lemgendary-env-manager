"""Regression tests for lemgendary-env-manager under Python 3.14."""

import shutil
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient
import jsonschema
from pydantic import BaseModel, Field, ValidationError
import pytest

from env_manager.resolver.reverse_deps import check_reverse_deps
from env_manager.utils import run_command_simple

HTTP_OK = 200


class DummyManifest(BaseModel):
    """Synthetic manifest model for pydantic schema validation tests."""

    name: str
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")


def test_pydantic_manifest_validation() -> None:
    """Verify pydantic 2.14.4 manifest model validation."""
    m = DummyManifest(name="test-pkg", version="1.0.0")
    assert m.name == "test-pkg"
    assert m.version == "1.0.0"

    with pytest.raises(ValidationError):
        DummyManifest(name="bad", version="invalid-version")


def test_jsonschema_validation() -> None:
    """Verify jsonschema validation."""
    schema = {
        "type": "object",
        "properties": {"packages": {"type": "array", "items": {"type": "string"}}},
        "required": ["packages"],
    }
    data = {"packages": ["torch", "kornia"]}
    jsonschema.validate(instance=data, schema=schema)


def test_fastapi_and_httpx2_client() -> None:
    """Test FastAPI endpoint invocation with TestClient / httpx2."""
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok", "python": sys.version_info[:2]}

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == HTTP_OK
    assert response.json()["status"] == "ok"
    assert response.json()["python"] == [3, 14]


def test_slim_venv_uv_pip_operations(tmp_path) -> None:
    """Verify that unseeded slim virtual environments work with uv pip."""
    uv_path = shutil.which("uv")
    assert uv_path is not None, "uv binary must be present on PATH"

    venv_dir = tmp_path / ".test_slim_venv"
    # Create unseeded slim venv without pip
    proc = run_command_simple([uv_path, "venv", str(venv_dir)], timeout=30)
    assert proc.returncode == 0

    sub = "Scripts" if sys.platform == "win32" else "bin"
    exe_name = "python.exe" if sys.platform == "win32" else "python"
    python_exe = venv_dir / sub / exe_name
    assert python_exe.is_file()

    # Verify native uv pip check succeeds
    check_proc = run_command_simple(
        [uv_path, "pip", "check", "--python", str(python_exe)], timeout=30
    )
    assert check_proc.returncode == 0

    # Verify native uv pip list succeeds
    list_proc = run_command_simple(
        [uv_path, "pip", "list", "--python", str(python_exe)], timeout=30
    )
    assert list_proc.returncode == 0


def test_upstream_upper_bound_cap_handling() -> None:
    """Verify reverse dep analysis recognizes upstream upper-bound caps."""
    # Simulate sympy requiring mpmath<1.4,>=1.1.0
    reverse_deps = {"mpmath": [("sympy", "<1.4,>=1.1.0")]}

    # Candidate 1.4.0 violates the constraint
    violation = check_reverse_deps("mpmath", "1.4.0", reverse_deps)
    assert violation is not None
    assert "sympy requires mpmath<1.4,>=1.1.0" in violation

    # Installed candidate 1.3.0 satisfies the constraint
    satisfied = check_reverse_deps("mpmath", "1.3.0", reverse_deps)
    assert satisfied is None
