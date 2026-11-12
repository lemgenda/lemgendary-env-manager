"""CUDA torch-family candidates are vetted as wheel-cuda (audit 4.1.4)."""

from pathlib import Path
from unittest.mock import patch

from env_manager.dependency_resolver import CUDA_WHEEL_TYPE, OutdatedPackage, classify_safe_upgrades
from env_manager.upgrade import plan as plan_module
from env_manager.venv_manager import ProjectVenvInfo

CLASSIFY = "env_manager.resolver.classify"
CUDA_INDEX = "https://download.pytorch.org/whl/cu126"
CURRENT = {"torch": "2.14.0+cu126", "torchvision": "0.28.0+cu126", "xformers": "0.1.0"}


def _cuda(name: str, old: str, new: str) -> OutdatedPackage:
    return OutdatedPackage(name, old, new, CUDA_WHEEL_TYPE)


def _classify(candidates, reverse_deps, dry_run):
    with patch(f"{CLASSIFY}.is_venv_valid", return_value=True), \
            patch(f"{CLASSIFY}.installed_package_versions", return_value=CURRENT), \
            patch(f"{CLASSIFY}.get_reverse_dependencies", return_value=reverse_deps), \
            patch(f"{CLASSIFY}.constraint_preserving_dry_run", side_effect=dry_run) as run:
        safe, blocked = classify_safe_upgrades(Path("p"), candidates, cuda_index_url=CUDA_INDEX)
    return safe, blocked, run


def test_torch_family_is_dry_run_together_on_the_cuda_index() -> None:
    """Torch family is dry run together on the cuda index."""
    candidates = [_cuda("torch", "2.14.0", "2.15.0"), _cuda("torchvision", "0.28.0", "0.29.0")]
    changed = {"torch": "2.15.0", "torchvision": "0.29.0"}
    safe, blocked, run = _classify(candidates, {}, lambda *a, **k: (True, changed, "ok"))
    assert [p.name for p in safe] == ["torch", "torchvision"]
    assert not blocked
    run.assert_called_once()
    assert run.call_args.args[1] == ["torch", "torchvision"]
    assert run.call_args.kwargs["index_url"] == CUDA_INDEX


def test_outside_reverse_dependency_blocks_a_cuda_wheel() -> None:
    """Outside reverse dependency blocks a cuda wheel."""
    candidates = [_cuda("torch", "2.14.0", "2.15.0")]
    reverse = {"torch": [("xformers", "<2.15"), ("torchvision", "==2.14.0")]}
    safe, blocked, run = _classify(candidates, reverse, lambda *a, **k: (True, {}, "ok"))
    assert not safe
    assert blocked[0][1] == "xformers requires torch<2.15"
    run.assert_not_called()


def test_plan_routes_cuda_candidates_through_classification(tmp_path: Path) -> None:
    """plan_python_project no longer trusts the CUDA dry-run alone."""
    project = ProjectVenvInfo(
        name="lemgendary-datasets", project_dir=str(tmp_path), venv_dir=str(tmp_path / ".venv"),
        python_path=None, is_valid=True, python_version=None,
    )
    found = [_cuda("torch", "2.14.0", "2.15.0")]
    blocked_torch = [(found[0], "xformers requires torch<2.15")]

    def classify(_p_dir, candidates, cuda_index_url=None):
        assert cuda_index_url == CUDA_INDEX
        return ([], blocked_torch) if candidates == found else ([], [])

    with patch.object(plan_module, "find_outdated_packages", return_value=[]), \
            patch.object(plan_module, "torch_installed", return_value=True), \
            patch.object(plan_module, "venv_package_map", return_value=CURRENT), \
            patch.object(plan_module, "check_torch_cuda_upgrades", return_value=(found, "ok")), \
            patch.object(plan_module, "classify_safe_upgrades", side_effect=classify):
        result = plan_module.plan_python_project(project, CUDA_INDEX)
    assert not result.safe_packages
    assert not result.torch_cuda_packages
    assert result.blocked_packages == blocked_torch
