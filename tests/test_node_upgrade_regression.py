"""Regression suite verifying Node/NPM toolchain upgrades and integrity.

Ensures that packages and tooling across lemgendary-env-manager and
lemgendary-ai-studio-gui satisfy major-version boundaries, syntax/engine
compatibility, and zero-diagnostic gates upon dependency upgrades.
"""

from __future__ import annotations

import json
from pathlib import Path

from env_manager.validation.gates.node_linters import eslint_config_args


def _workspace_root() -> Path:
    """Return the model-training workspace root path."""
    return Path(__file__).resolve().parent.parent.parent


def test_env_manager_package_json_manifest_sync() -> None:
    """Verify package.json matches requirements/lemgendary-env-manager.package.json."""
    root = _workspace_root()
    env_pkg = root / "lemgendary-env-manager" / "package.json"
    central_pkg = (
        root / "lemgendary-env-manager" / "requirements" / "lemgendary-env-manager.package.json"
    )
    assert env_pkg.is_file(), "Missing lemgendary-env-manager/package.json"
    assert central_pkg.is_file(), "Missing central env-manager package.json manifest"

    env_data = json.loads(env_pkg.read_text(encoding="utf-8"))
    central_data = json.loads(central_pkg.read_text(encoding="utf-8"))
    assert env_data["devDependencies"] == central_data["devDependencies"], (
        "DevDependencies drift between package.json and requirements manifest."
    )


def test_gui_package_json_manifest_sync() -> None:
    """Verify GUI package.json matches requirements/lemgendary-ai-studio-gui.package.json."""
    root = _workspace_root()
    gui_pkg = root / "lemgendary-ai-studio-gui" / "package.json"
    central_pkg = (
        root / "lemgendary-env-manager" / "requirements" / "lemgendary-ai-studio-gui.package.json"
    )
    assert gui_pkg.is_file(), "Missing lemgendary-ai-studio-gui/package.json"
    assert central_pkg.is_file(), "Missing central gui package.json manifest"

    gui_data = json.loads(gui_pkg.read_text(encoding="utf-8"))
    central_data = json.loads(central_pkg.read_text(encoding="utf-8"))
    assert gui_data["dependencies"] == central_data["dependencies"], (
        "Dependencies drift between GUI package.json and requirements manifest."
    )
    assert gui_data["devDependencies"] == central_data["devDependencies"], (
        "DevDependencies drift between GUI package.json and requirements manifest."
    )


def test_eslint_flat_config_support_on_upgrade() -> None:
    """Verify that modern ESLint flat config is recognized with no deprecated flags."""
    root = _workspace_root()
    gui_dir = root / "lemgendary-ai-studio-gui"
    if (gui_dir / "eslint.config.js").is_file():
        args = eslint_config_args(gui_dir)
        assert args == [], "Flat config should produce empty argument list without --ext"


def test_no_peer_dependency_downgrade_violations() -> None:
    """Verify GUI peer dependencies do not violate critical typescript/react constraints."""
    root = _workspace_root()
    gui_pkg = json.loads(
        (root / "lemgendary-ai-studio-gui" / "package.json").read_text(encoding="utf-8")
    )
    ts_spec = gui_pkg["devDependencies"].get("typescript", "")
    assert ts_spec.startswith("^5."), (
        f"typescript in GUI must remain on v5.x to support typescript-eslint v8, got: {ts_spec}"
    )

    react_spec = gui_pkg["dependencies"].get("react", "")
    assert react_spec.startswith("^19."), (
        f"react in GUI must remain on v19.x, got: {react_spec}"
    )


def test_tauri_cargo_manifest_parity_and_lock() -> None:
    """Verify that Tauri JS/TS API dependencies align with Cargo.toml crates."""
    root = _workspace_root()
    gui_dir = root / "lemgendary-ai-studio-gui"
    cargo_toml = gui_dir / "src-tauri" / "Cargo.toml"
    assert cargo_toml.is_file(), "Missing src-tauri/Cargo.toml"

    gui_pkg = json.loads((gui_dir / "package.json").read_text(encoding="utf-8"))
    tauri_api = gui_pkg["dependencies"].get("@tauri-apps/api", "")
    tauri_shell = gui_pkg["dependencies"].get("@tauri-apps/plugin-shell", "")

    # Both frontend packages must match the Tauri v2 family
    assert "2." in tauri_api, f"Expected @tauri-apps/api v2.x, got {tauri_api}"
    assert "2." in tauri_shell, f"Expected @tauri-apps/plugin-shell v2.x, got {tauri_shell}"

    cargo_text = cargo_toml.read_text(encoding="utf-8")
    assert 'tauri = { version = "2.' in cargo_text, "Cargo.toml must declare tauri = 2.x"
    assert (
        'tauri-plugin-shell = "2.' in cargo_text
    ), "Cargo.toml must declare tauri-plugin-shell = 2.x"

    # Enforce exact pinning for ABI-sensitive native screen recording crates
    for pinned_crate in ("openh264", "mp4", "bytes", "windows-capture"):
        assert f'{pinned_crate} = "=' in cargo_text, (
            f"Native recording crate {pinned_crate} must be strictly pinned with '=x.y.z'"
        )
