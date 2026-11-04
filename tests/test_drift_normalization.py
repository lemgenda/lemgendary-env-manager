"""The drift matrix matches pip's raw names to PEP 503 names (REVIEW_REPORT D-11)."""

from pathlib import Path

from env_manager.health_checker import ProjectHealth, compute_version_drift


def _project(root: Path, name: str, installed: dict[str, str]) -> ProjectHealth:
    project_dir = root / name
    project_dir.mkdir()
    (project_dir / "requirements.txt").write_text("pydantic-core==2.33.2\n", encoding="utf-8")
    return ProjectHealth(
        name=name, project_dir=str(project_dir), venv_exists=True, python_version="3.12",
        total_required=1, total_installed=1, missing_packages=[],
        installed_packages=installed, is_healthy=True,
    )


def test_underscore_names_report_their_installed_version(tmp_path: Path) -> None:
    """``pydantic_core`` as pip reports it fills the ``pydantic-core`` row."""
    projects = [
        _project(tmp_path, "a", {"pydantic_core": "2.33.2"}),
        _project(tmp_path, "b", {"Pydantic.Core": "2.33.1"}),
    ]
    entries = compute_version_drift(projects, include_safety=False)
    row = next(e for e in entries if e.package_name == "pydantic-core")
    assert row.versions == {"a": "2.33.2", "b": "2.33.1"}
