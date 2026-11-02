"""pip stdout/stderr separation and fail-closed classification (review finding D-07)."""

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from env_manager import dependency_resolver as dr
from env_manager import utils

_WARNING = "WARNING: Ignoring invalid distribution ~orch (c:\\venv\\lib\\site-packages)"
_PIP_LIST = [{"name": "numpy", "version": "2.5.4"}, {"name": "pip", "version": "26.0"}]


def _completed(stdout: str, stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["pip"], returncode=returncode, stdout=stdout, stderr=stderr,
    )


class TestPipStreamsAreSeparated(unittest.TestCase):
    """Runs a real child process that writes JSON to stdout and warnings to stderr."""

    def test_stderr_lines_do_not_reach_stdout(self) -> None:
        script = (
            "import sys, json\n"
            f"sys.stderr.write({_WARNING!r} + '\\n'); sys.stderr.flush()\n"
            f"sys.stdout.write(json.dumps({_PIP_LIST!r}, indent=2) + '\\n')\n"
            f"sys.stderr.write({_WARNING!r} + '\\n')\n"
        )
        real_popen = subprocess.Popen

        def fake_popen(cmd, **kwargs):
            return real_popen([sys.executable, "-c", script], **kwargs)

        streamed = []
        with patch.object(utils.subprocess, "Popen", side_effect=fake_popen):
            proc = utils.run_pip_with_recovery(Path("python"), ["list", "--format=json"],
                                               on_line=streamed.append)

        self.assertEqual(proc.returncode, 0)
        self.assertEqual(json.loads(proc.stdout), _PIP_LIST)
        self.assertEqual(proc.stderr.splitlines(), [_WARNING, _WARNING])
        self.assertIn(_WARNING, streamed)


class _VenvPatched(unittest.TestCase):
    def setUp(self) -> None:
        self.project = Path("fake-project")
        for target, value in (("is_venv_valid", True), ("get_venv_python_path", Path("python"))):
            p = patch.object(dr, target, return_value=value)
            p.start()
            self.addCleanup(p.stop)


class TestInstalledStateParsing(_VenvPatched):
    def test_stderr_warning_is_logged_and_json_still_parsed(self) -> None:
        with patch.object(dr, "run_pip_with_recovery",
                          return_value=_completed(json.dumps(_PIP_LIST), _WARNING)), \
                self.assertLogs(dr._log, level="WARNING") as logs:
            versions = dr._installed_package_versions(self.project)

        self.assertEqual(versions, {"numpy": "2.5.4", "pip": "26.0"})
        self.assertTrue(any("Ignoring invalid distribution" in line for line in logs.output))

    def test_invalid_json_raises(self) -> None:
        with patch.object(dr, "run_pip_with_recovery",
                          return_value=_completed(_WARNING + "\n" + json.dumps(_PIP_LIST))):
            with self.assertRaises(dr.PipJsonError):
                dr._installed_package_versions(self.project)

    def test_empty_package_list_raises(self) -> None:
        with patch.object(dr, "run_pip_with_recovery", return_value=_completed("[]")):
            with self.assertRaises(dr.PipJsonError):
                dr._installed_package_versions(self.project)


class TestClassifierFailsClosed(_VenvPatched):
    def setUp(self) -> None:
        super().setUp()
        self.candidates = [
            dr.OutdatedPackage("numpy", "2.5.4", "2.6.0", "wheel"),
            dr.OutdatedPackage("torch", "2.15.0", "2.16.0", "wheel-cuda"),
        ]

    def test_unparseable_pip_list_blocks_every_candidate(self) -> None:
        with patch.object(dr, "run_pip_with_recovery", return_value=_completed("not json")), \
                self.assertLogs(dr._log, level="ERROR"):
            safe, blocked = dr.classify_safe_upgrades(self.project, self.candidates)

        self.assertEqual(safe, [])
        self.assertEqual([pkg.name for pkg, _ in blocked], ["numpy", "torch"])
        self.assertTrue(all("unavailable" in reason for _, reason in blocked))

    def test_failed_pip_inspect_blocks_every_candidate(self) -> None:
        responses = [_completed(json.dumps(_PIP_LIST)), _completed("", "ERROR: boom", returncode=1)]
        with patch.object(dr, "run_pip_with_recovery", side_effect=responses), \
                self.assertLogs(dr._log, level="ERROR"):
            safe, blocked = dr.classify_safe_upgrades(self.project, self.candidates)

        self.assertEqual(safe, [])
        self.assertEqual(len(blocked), 2)

    def test_unparseable_dry_run_report_is_a_failure(self) -> None:
        with patch.object(dr, "run_pip_with_recovery",
                          return_value=_completed("Would install numpy-2.6.0", _WARNING)), \
                self.assertLogs(dr._log, level="ERROR"):
            ok, changed, msg = dr.constraint_preserving_dry_run(
                self.project, ["numpy"], current_state={"numpy": "2.5.4", "pip": "26.0"},
            )

        self.assertFalse(ok)
        self.assertEqual(changed, {})
        self.assertIn("could not be parsed", msg)

    def test_empty_installed_state_aborts_dry_run(self) -> None:
        with patch.object(dr, "run_pip_with_recovery") as runner, \
                self.assertLogs(dr._log, level="ERROR"):
            ok, _, _ = dr.constraint_preserving_dry_run(self.project, ["numpy"], current_state={})

        self.assertFalse(ok)
        runner.assert_not_called()

    def test_dry_run_report_with_stderr_warning_is_parsed(self) -> None:
        report = {"install": [{"metadata": {"name": "numpy", "version": "2.6.0"}}]}
        with patch.object(dr, "run_pip_with_recovery",
                          return_value=_completed(json.dumps(report), _WARNING)):
            ok, changed, _ = dr.constraint_preserving_dry_run(
                self.project, ["numpy"], current_state={"numpy": "2.5.4", "pip": "26.0"},
            )

        self.assertTrue(ok)
        self.assertEqual(changed, {"numpy": "2.6.0"})


if __name__ == "__main__":
    unittest.main()
