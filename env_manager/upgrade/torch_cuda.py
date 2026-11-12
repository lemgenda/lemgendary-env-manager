"""GPU torch index selection, CUDA runtime verification and the torch-family CUDA dry-run."""

import subprocess
from pathlib import Path

from env_manager._logging import get_logger
from env_manager.dependency_resolver import (
    CUDA_WHEEL_TYPE,
    OutdatedPackage,
    constraint_preserving_dry_run,
)
from env_manager.system_probe import HardwareProfile, probe_hardware
from env_manager.upgrade.models import TORCH_FAMILY, normalize, venv_package_map
from env_manager.utils import run_command_simple
from env_manager.venv_manager import (
    get_venv_python_path,
    is_venv_valid,
)

_log = get_logger(__name__)


def torch_upgrade_index(hw: HardwareProfile) -> str | None:
    """Return the GPU torch index to vet and apply upgrades against, or None.

    None means the host has no CUDA or ROCm backend, so torch-family
    packages are vetted like any other PyPI package. ``recommended_torch_index``
    is always set (it falls back to the CPU index), so it must not be used to
    decide whether a GPU upgrade path exists.
    """
    backend = (hw.primary_backend or "").lower()
    if backend in ("cuda", "rocm") and hw.recommended_torch_index:
        return str(hw.recommended_torch_index)
    return None


def detect_cuda() -> tuple[str | None, str]:
    """Return (torch_index_url_or_None, human_description)."""
    hw = probe_hardware()
    index_url = torch_upgrade_index(hw)
    accel = ", ".join(a.name for a in hw.accelerators) or "none"
    if index_url:
        return index_url, f"{(hw.primary_backend or '').upper()} via {accel}"
    return None, f"no CUDA backend ({accel})"


def torch_installed(project_dir: Path) -> bool:
    """Return True when torch is installed in the project's venv."""
    return "torch" in venv_package_map(project_dir)


def verify_torch_cuda_runtime(project_dir: Path) -> tuple[bool, str]:
    """Import torch in the venv and confirm ``torch.cuda.is_available()``."""
    if not is_venv_valid(project_dir):
        return False, "invalid venv"
    python_path = get_venv_python_path(project_dir)
    try:
        proc = run_command_simple(
            [str(python_path), "-c",
             "import torch; print(torch.__version__); print(int(torch.cuda.is_available()))"],
            timeout=60,
        )
        if proc.returncode != 0:
            return False, (proc.stderr or "torch import failed").strip()[:200]
        lines = (proc.stdout or "").strip().splitlines()
        *_, version, cuda_flag = ["?", "?", *lines]
        if cuda_flag == "1":
            return True, f"torch {version} CUDA runtime verified"
        return False, f"torch {version} reports cuda.is_available() == False"
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("torch CUDA runtime check failed for %s: %s", project_dir, exc)
        return False, str(exc)


def check_torch_cuda_upgrades(
    project_dir: Path,
    cuda_index_url: str,
    current: dict[str, str],
) -> tuple[list[OutdatedPackage], str]:
    """Return (candidates, reason) for torch-family CUDA upgrades.

    Only considers torch-family packages that are *already installed* in the
    project. Running the CUDA-index dry-run against all three names would
    cause pip to install packages the project never asked for (e.g.
    torchaudio into a project that only uses torch/torchvision).

    The returned candidates only include packages whose version would
    actually change. Packages already at the newest CUDA build are omitted.
    """
    installed_torch = [
        name for name in TORCH_FAMILY if normalize(name) in current
    ]
    if not installed_torch:
        return [], "no torch-family packages installed"

    ok, changed, msg = constraint_preserving_dry_run(
        project_dir,
        installed_torch,
        current_state=current,
        index_url=cuda_index_url,
    )
    if not ok:
        return [], f"CUDA dry-run failed: {msg}"

    candidates: list[OutdatedPackage] = []
    for pkg_name in installed_torch:
        key = normalize(pkg_name)
        new_ver = changed.get(key)
        if new_ver is None:
            continue
        old_ver = current.get(key, "")
        if not old_ver or old_ver == new_ver:
            # Not an upgrade: either not previously installed, or already
            # at the newest available CUDA build. Skip.
            continue
        candidates.append(OutdatedPackage(
            name=pkg_name,
            current_version=old_ver,
            latest_version=new_ver,
            package_type=CUDA_WHEEL_TYPE,
        ))

    reason = f"CUDA index checked ({len(installed_torch)} installed)"
    return candidates, reason
