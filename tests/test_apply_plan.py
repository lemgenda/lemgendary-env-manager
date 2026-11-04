"""The apply phase installs exactly the vetted plan (REVIEW_REPORT D-10)."""

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from env_manager.dependency_resolver import OutdatedPackage
from env_manager.system_probe import HardwareProfile
from env_manager.updater import (
    EcosystemUpgradePlan,
    ProjectUpgradePlan,
    apply_batch_with_rollback,
    apply_upgrade_plan,
    torch_upgrade_index,
)

APPLY = "env_manager.upgrade.apply"
CUDA_INDEX = "https://download.pytorch.org/whl/cu126"


def _plan(safe: list[OutdatedPackage], cuda_index_url: str | None) -> EcosystemUpgradePlan:
    proj = ProjectUpgradePlan(
        name="lemgendary-datasets", project_dir=str(Path("ws") / "lemgendary-datasets"),
        is_node_project=False, outdated_packages=list(safe), safe_packages=list(safe),
        has_updates=bool(safe), has_safe_updates=bool(safe),
        cuda_available=cuda_index_url is not None, cuda_index_url=cuda_index_url,
    )
    return EcosystemUpgradePlan(
        projects=[proj], total_outdated=len(safe), total_safe=len(safe), total_blocked=0,
        has_any_updates=bool(safe), has_any_safe_updates=bool(safe),
        cuda_detected=cuda_index_url is not None, cuda_index_url=cuda_index_url,
    )


class TestApplyUpgradePlan(unittest.TestCase):
    """``apply_upgrade_plan`` honours the vetted plan."""

    def setUp(self) -> None:
        self.ws = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.ws)
        self.batch = patch(
            f"{APPLY}.apply_batch_with_rollback", return_value=(True, "ok", False),
        ).start()
        self.torch = patch(
            f"{APPLY}._apply_torch_cuda_upgrade", return_value=(True, "ok"),
        ).start()
        patch(f"{APPLY}._snapshot_freeze", return_value="torch==2.14.0\n").start()
        patch(f"{APPLY}._pip_check", return_value=(True, "ok")).start()
        patch(f"{APPLY}.torch_installed", return_value=True).start()
        patch(f"{APPLY}.verify_torch_cuda_runtime", return_value=(True, "ok")).start()

    def tearDown(self) -> None:
        patch.stopall()

    def test_candidates_are_pinned_to_the_vetted_version(self) -> None:
        """Each safe package is installed as ``name==latest_version``."""
        plan = _plan([OutdatedPackage("rich", "13.0.0", "13.1.0", "pip")], None)
        list(apply_upgrade_plan(plan, self.ws))
        self.assertEqual(self.batch.call_args.args[1], ["rich==13.1.0"])

    def test_no_torch_reinstall_without_torch_candidates(self) -> None:
        """A GPU host with no torch candidates in the plan does not reinstall torch."""
        plan = _plan([OutdatedPackage("rich", "13.0.0", "13.1.0", "pip")], CUDA_INDEX)
        list(apply_upgrade_plan(plan, self.ws))
        self.torch.assert_not_called()

    def test_torch_candidates_use_the_plan_index_and_pins(self) -> None:
        """Torch candidates install pinned from the CUDA index stored in the plan."""
        plan = _plan([OutdatedPackage("torch", "2.14.0", "2.15.0+cu126", "wheel-cuda")],
                     CUDA_INDEX)
        events = list(apply_upgrade_plan(plan, self.ws))
        self.torch.assert_called_once()
        self.assertEqual(self.torch.call_args.args[1:], (CUDA_INDEX, ["torch==2.15.0+cu126"]))
        self.assertIn("upgraded", [e.status for e in events])

    def test_cpu_host_applies_torch_in_the_regular_batch(self) -> None:
        """Without a GPU index, a vetted torch candidate goes in the pinned pip batch."""
        plan = _plan([OutdatedPackage("torch", "2.14.0", "2.15.0", "pip")], None)
        list(apply_upgrade_plan(plan, self.ws))
        self.torch.assert_not_called()
        self.assertEqual(self.batch.call_args.args[1], ["torch==2.15.0"])

    def test_failed_torch_install_rolls_back(self) -> None:
        """A failed CUDA install restores the snapshot."""
        self.torch.return_value = (False, "boom")
        plan = _plan([OutdatedPackage("torch", "2.14.0", "2.15.0+cu126", "wheel-cuda")],
                     CUDA_INDEX)
        with patch(f"{APPLY}._restore_freeze", return_value=(True, "ok")) as restore:
            events = list(apply_upgrade_plan(plan, self.ws))
        restore.assert_called_once()
        failed = [e for e in events if e.package == "torch-family"]
        self.assertIn("rolled back", failed[0].message)


class TestBatchRollback(unittest.TestCase):
    """``apply_batch_with_rollback`` restores the snapshot on any failure."""

    def test_install_failure_rolls_back(self) -> None:
        """A pip install that fails part-way is rolled back."""
        ws = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, ws)
        with patch(f"{APPLY}._snapshot_freeze", return_value="rich==13.0.0\n"), \
                patch(f"{APPLY}._apply_pip_upgrade", return_value=(False, "boom")), \
                patch(f"{APPLY}._restore_freeze",
                      return_value=(True, "ok")) as restore:
            ok, msg, rolled_back = apply_batch_with_rollback(ws, ["rich==13.1.0"])
        self.assertFalse(ok)
        self.assertTrue(rolled_back)
        self.assertIn("pip install failed", msg)
        restore.assert_called_once()


class TestTorchUpgradeIndex(unittest.TestCase):
    """One definition of the GPU torch index for audit, plan and apply."""

    def test_cpu_host_has_no_gpu_index(self) -> None:
        """The CPU fallback index is not a GPU upgrade path."""
        hw = SimpleNamespace(primary_backend="cpu",
                             recommended_torch_index="https://download.pytorch.org/whl/cpu")
        self.assertIsNone(torch_upgrade_index(cast(HardwareProfile, hw)))

    def test_cuda_host_uses_the_recommended_index(self) -> None:
        """A CUDA host returns its recommended index."""
        hw = SimpleNamespace(primary_backend="cuda", recommended_torch_index=CUDA_INDEX)
        self.assertEqual(torch_upgrade_index(cast(HardwareProfile, hw)), CUDA_INDEX)


if __name__ == "__main__":
    unittest.main()
