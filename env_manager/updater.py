"""Safe dependency upgrade module with CUDA-aware torch handling.

Implements a bottom-up, conflict-aware package upgrade strategy across all
managed Python virtual environments and the Node.js GUI workspace.

Safety model
------------
1. Candidates are vetted by :func:`classify_safe_upgrades`, which combines
   reverse-dependency analysis (via ``pip inspect``) with constraint-
   preserving pip dry-runs.
2. Every batch is snapshotted via ``pip freeze`` before being applied.
3. Each candidate is installed pinned to the vetted version
   (``name==latest_version``), so the apply phase cannot pick up a newer
   release than the one the dry-run checked.
4. After applying, ``pip check`` is run. If the install or the check fails,
   the batch is rolled back from the snapshot and reported as a failure.

CUDA handling
-------------
The PyTorch CUDA index URL returned by :func:`torch_upgrade_index` is the
single source of truth for CUDA availability; the audit, the plan and the
pipeline all use it, and :func:`apply_upgrade_plan` reuses the index stored
in the plan. When present, torch-family packages are always resolved
against it using ``--index-url`` (not ``--extra-index-url``). After
upgrade, ``torch.cuda.is_available()`` is verified.

Only torch-family packages that are *already installed* in a project are
considered for upgrade. This prevents the CUDA index dry-run from
silently adding packages the project never declared (e.g. torchaudio
into a project that only uses torch/torchvision).
"""

from env_manager.upgrade.apply import apply_batch_with_rollback, apply_upgrade_plan
from env_manager.upgrade.manifest_sync import sync_manifests_from_venvs
from env_manager.upgrade.models import (
    PROTECTED_FROM_SYNC,
    TORCH_FAMILY,
    EcosystemUpgradePlan,
    ProjectUpgradePlan,
    UpgradeEvent,
    pinned_specs,
    upgrade_order,
)
from env_manager.upgrade.npm import apply_npm_update, find_npm_outdated
from env_manager.upgrade.plan import build_upgrade_plan
from env_manager.upgrade.torch_cuda import check_torch_cuda_upgrades, torch_upgrade_index



__all__ = [
    "PROTECTED_FROM_SYNC",
    "TORCH_FAMILY",
    "EcosystemUpgradePlan",
    "ProjectUpgradePlan",
    "UpgradeEvent",
    "apply_batch_with_rollback",
    "apply_npm_update",
    "apply_upgrade_plan",
    "build_upgrade_plan",
    "check_torch_cuda_upgrades",
    "find_npm_outdated",
    "pinned_specs",
    "sync_manifests_from_venvs",
    "torch_upgrade_index",
    "upgrade_order",
]
