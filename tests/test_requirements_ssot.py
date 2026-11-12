"""Central requirement manifests leave the torch index to the probe (audit 4.1.2)."""

from pathlib import Path

import pytest
import yaml
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

from env_manager.requirements_manager import parse_requirements_file
from env_manager.workspace import ENV_MANAGER_DIR

REQUIREMENTS_DIR = ENV_MANAGER_DIR / "requirements"
CENTRAL_MANIFESTS = sorted(REQUIREMENTS_DIR.glob("requirements-*.txt"))
INDEX_OPTIONS = ("--extra-index-url", "--index-url", "-i ", "-f ", "--find-links")


@pytest.mark.parametrize("manifest", CENTRAL_MANIFESTS, ids=lambda p: p.name)
def test_manifest_declares_no_package_index(manifest: Path) -> None:
    """The installer injects the hardware-probed index; a fixed one would override it."""
    lines = [line.strip() for line in manifest.read_text(encoding="utf-8").splitlines()]
    assert not [line for line in lines if line.startswith(INDEX_OPTIONS)]


def _pins(manifest: Path) -> dict[str, Requirement]:
    return {
        e.name.lower(): Requirement(e.raw_line)
        for e in parse_requirements_file(manifest)
        if e.name and not e.is_index_url and not e.is_editable
    }


def test_bitsandbytes_is_excluded_on_macos() -> None:
    """bitsandbytes ships no macOS wheels, so it carries a darwin marker."""
    requirement = _pins(REQUIREMENTS_DIR / "requirements-training.txt")["bitsandbytes"]
    assert requirement.marker is not None
    assert not requirement.marker.evaluate({"sys_platform": "darwin"})
    assert requirement.marker.evaluate({"sys_platform": "linux"})


def test_runtime_env_container_specifiers_match_training_pins() -> None:
    """Every container library pinned in training satisfies runtime_env.yaml."""
    contract = yaml.safe_load((REQUIREMENTS_DIR / "runtime_env.yaml").read_text(encoding="utf-8"))
    pins = _pins(REQUIREMENTS_DIR / "requirements-training.txt")
    checked = 0
    for name, entry in contract["container_runtime_dependencies"].items():
        pinned = pins.get(name)
        if pinned is None:
            continue
        version = str(next(iter(pinned.specifier))).lstrip("=<>!~")
        assert SpecifierSet(entry["specifier"]).contains(version), name
        checked += 1
    assert checked > 0
