"""Regression tests for lemgendary-env-manager under Python 3.14."""

import sys
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field, ValidationError
import jsonschema

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
        "properties": {
            "packages": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["packages"]
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
