"""Regression tests for upgrade routing and scope (D-08, D-09).

D-08: pipeline Step 5 must give every Python project the pip safe-upgrade
path and every project with a ``package.json`` the npm path; a dual-stack
project gets both.

D-09: ``lem-env update --project X`` must plan and apply only project X.

All upgrade functions are mocked; no real pip or npm command runs.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from env_manager.cli import app
from env_manager.ecosystem import ecosystem
from env_manager.dependency_resolver import OutdatedPackage
from env_manager.orchestrator import PipelineEvent, PipelineOrchestrator
from env_manager.pipeline.events import EventPublisher
from env_manager.updater import (
    EcosystemUpgradePlan,
    ProjectUpgradePlan,
    build_upgrade_plan,
)
from env_manager.venv_manager import ProjectVenvInfo, discover_projects


_emit = EventPublisher(total_steps=7).step(5, "Dependency Upgrades")


def _project(name: str, root: Path, *, is_node: bool, has_npm: bool) -> ProjectVenvInfo:
    return ProjectVenvInfo(
        name=name, project_dir=str(root / name), venv_dir=str(root / name / ".venv"),
        python_path=None, is_valid=True, python_version=None,
        is_node_project=is_node, has_npm_manifest=has_npm,
    )


class TestDiscoveryMetadata(unittest.TestCase):
    """Discovery records which projects carry an npm manifest."""

    def test_has_npm_manifest_flag(self) -> None:
        """Discovery sets ``has_npm_manifest`` and ``is_node_project`` per project."""
        managed = ecosystem().managed_projects()
        dual = ecosystem().self_folder()
        plain = next(p.folder for p in managed if not p.is_node and p.folder != dual)
        node = next(p.folder for p in managed if p.is_node)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in (dual, plain, node):
                (root / name).mkdir()
            (root / dual / "package.json").write_text("{}")
            (root / node / "package.json").write_text("{}")

            found = {p.name: p for p in discover_projects(root)}

        self.assertTrue(found[dual].has_npm_manifest)
        self.assertFalse(found[dual].is_node_project)
        self.assertFalse(found[plain].has_npm_manifest)
        self.assertTrue(found[node].has_npm_manifest)
        self.assertTrue(found[node].is_node_project)


class TestStep5Routing(unittest.TestCase):
    """Step 5 dispatches pip and npm upgrades from project metadata."""

    def setUp(self) -> None:
        self._tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._tmp_dir)
        self.root = Path(self._tmp_dir)
        self.pip_pkg = OutdatedPackage("rich", "13.0.0", "13.1.0", "pip")
        self.npm_pkg = OutdatedPackage("eslint", "9.0.0", "9.1.0", "npm")
        self.npm_outdated = patch(
            "env_manager.pipeline.upgrades.find_npm_outdated", return_value=[self.npm_pkg]
        ).start()
        self.npm_update = patch(
            "env_manager.pipeline.upgrades.apply_npm_update", return_value=(True, "ok")
        ).start()
        self.pkg_count = patch(
            "env_manager.pipeline.upgrades.venv_package_count", return_value=5
        ).start()
        self.pip_outdated = patch(
            "env_manager.pipeline.upgrades.find_outdated_packages", return_value=[self.pip_pkg]
        ).start()
        patch(
            "env_manager.pipeline.upgrades.classify_safe_upgrades",
            return_value=([self.pip_pkg], []),
        ).start()
        self.pip_apply = patch(
            "env_manager.pipeline.upgrades.apply_batch_with_rollback",
            return_value=(True, "ok", False),
        ).start()
        self.sync = patch("env_manager.pipeline.upgrades.sync_manifests_from_venvs").start()

    def tearDown(self) -> None:
        patch.stopall()

    def _run(self, project: ProjectVenvInfo) -> list[PipelineEvent]:
        orchestrator = PipelineOrchestrator(base_dir=self.root)
        hw = MagicMock(recommended_torch_index=None)
        return list(orchestrator.execute_safe_upgrades([project], hw, _emit))

    def test_dual_stack_project_gets_pip_and_npm(self) -> None:
        """A project with requirements and package.json gets pip and npm upgrades."""
        proj = _project("lemgendary-env-manager", self.root, is_node=False, has_npm=True)
        self._run(proj)
        p_dir = Path(proj.project_dir)
        self.npm_outdated.assert_called_once_with(p_dir)
        self.npm_update.assert_called_once_with(p_dir)
        self.pip_outdated.assert_called_once_with(p_dir)
        self.pip_apply.assert_called_once_with(p_dir, ["rich==13.1.0"], extra_index_url=None)
        self.sync.assert_called_once_with(self.root)

    def test_python_only_project_gets_pip_only(self) -> None:
        """A Python-only project gets pip upgrades only."""
        proj = _project("lemgendary-datasets", self.root, is_node=False, has_npm=False)
        self._run(proj)
        self.npm_outdated.assert_not_called()
        self.npm_update.assert_not_called()
        self.pip_apply.assert_called_once_with(
            Path(proj.project_dir), ["rich==13.1.0"], extra_index_url=None
        )

    def test_npm_only_project_gets_npm_only(self) -> None:
        """A Node-only project gets npm upgrades only."""
        proj = _project("lemgendary-ai-studio-gui", self.root, is_node=True, has_npm=True)
        events = self._run(proj)
        self.npm_outdated.assert_called_once_with(Path(proj.project_dir))
        self.npm_update.assert_called_once_with(Path(proj.project_dir))
        self.pkg_count.assert_not_called()
        self.pip_outdated.assert_not_called()
        self.pip_apply.assert_not_called()
        self.assertTrue(any("(npm) - updating 1 package" in e.message for e in events))

    def test_npm_up_to_date_skips_update(self) -> None:
        """An up-to-date npm project is not updated."""
        self.npm_outdated.return_value = []
        proj = _project("lemgendary-ai-studio-gui", self.root, is_node=True, has_npm=True)
        self._run(proj)
        self.npm_update.assert_not_called()


class TestUpdateCommandScope(unittest.TestCase):
    """``lem-env update --project X`` plans and applies only X."""

    def setUp(self) -> None:
        self.runner = CliRunner()

    @staticmethod
    def _plan(name: str) -> EcosystemUpgradePlan:
        pkg = OutdatedPackage("rich", "13.0.0", "13.1.0", "pip")
        proj = ProjectUpgradePlan(
            name=name, project_dir=name, is_node_project=False,
            outdated_packages=[pkg], safe_packages=[pkg], blocked_packages=[],
            has_updates=True, has_safe_updates=True,
        )
        return EcosystemUpgradePlan(
            projects=[proj], total_outdated=1, total_safe=1, total_blocked=0,
            has_any_updates=True, has_any_safe_updates=True,
        )

    def test_project_option_scopes_plan_and_apply(self) -> None:
        """``update --project X`` plans and applies only X."""
        plan = self._plan("lemgendary-datasets")
        with patch("env_manager.cli.build_upgrade_plan", return_value=plan) as build, \
                patch("env_manager.cli.apply_upgrade_plan", return_value=iter([])) as apply_, \
                patch("env_manager.upgrade.verify.validate_project",
                      return_value=MagicMock(passed=True)):
            result = self.runner.invoke(app, ["update", "--project", "lemgendary-datasets"])

        self.assertEqual(result.exit_code, 0, result.output)
        build.assert_called_once()
        self.assertEqual(build.call_args.kwargs["project"], "lemgendary-datasets")
        apply_.assert_called_once()
        applied_plan = apply_.call_args.args[0]
        self.assertEqual([p.name for p in applied_plan.projects], ["lemgendary-datasets"])

    def test_unknown_project_exits_non_zero(self) -> None:
        """An unknown project exits non-zero and applies nothing."""
        with patch("env_manager.cli.build_upgrade_plan",
                   side_effect=ValueError("Project not found: nope")) as build, \
                patch("env_manager.cli.apply_upgrade_plan") as apply_:
            result = self.runner.invoke(app, ["update", "--project", "nope"])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Project not found: nope", result.output)
        self.assertEqual(build.call_args.kwargs["project"], "nope")
        apply_.assert_not_called()

    def test_unknown_project_rejected_by_plan_builder(self) -> None:
        """The plan builder raises ValueError for an unknown project."""
        with tempfile.TemporaryDirectory() as tmp, \
                patch("env_manager.upgrade.plan.detect_cuda", return_value=(None, "none")):
            with self.assertRaises(ValueError):
                build_upgrade_plan(Path(tmp), project="nope")


if __name__ == "__main__":
    unittest.main()
