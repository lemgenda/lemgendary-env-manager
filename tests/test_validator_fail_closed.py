"""A missing or crashing lint tool fails validation (REVIEW_REPORT D-16)."""

import sys
from pathlib import Path
from unittest.mock import patch

from env_manager.validator import TOOL_ERROR, run_eslint, run_markdownlint, run_yamllint

_NODE_RESOLVE = "env_manager.validation.gates.node_linters.resolve_tool_cmd"
_YAML_PYTHON = "env_manager.validation.gates.data_formats.get_env_manager_python"


def _exits(code: int, output: str = "") -> list[str]:
    """Return a command that prints ``output`` and exits with ``code``."""
    return [sys.executable, "-c", f"import sys; print({output!r}); sys.exit({code})"]


def test_markdownlint_crash_is_a_violation(tmp_path: Path) -> None:
    """A non-zero exit with no parsable finding is reported, not passed."""
    (tmp_path / "a.md").write_text("# A\n", encoding="utf-8")
    with patch(_NODE_RESOLVE, return_value=_exits(1, "boom")):
        found = run_markdownlint(tmp_path)
    assert [v.violation_type for v in found] == [TOOL_ERROR]
    assert "boom" in found[0].message


def test_markdownlint_findings_are_not_tool_errors(tmp_path: Path) -> None:
    """Exit 1 with parsed findings reports the findings only."""
    (tmp_path / "a.md").write_text("# A\n", encoding="utf-8")
    report = f"{tmp_path / 'a.md'}:1 MD041/first-line-heading First line"
    with patch(_NODE_RESOLVE, return_value=_exits(1, report)):
        found = run_markdownlint(tmp_path)
    assert [v.violation_type for v in found] == ["markdownlint/MD041/first-line-heading"]


def test_yamllint_without_interpreter_fails(tmp_path: Path) -> None:
    """YAML files with no interpreter to run yamllint are a tool failure."""
    (tmp_path / "a.yaml").write_text("a: 1\n", encoding="utf-8")
    with patch(_YAML_PYTHON, return_value=None):
        found = run_yamllint(tmp_path)
    assert [v.violation_type for v in found] == [TOOL_ERROR]


def test_yamllint_config_error_fails(tmp_path: Path) -> None:
    """yamllint exit code 2 (config or tool error) is a tool failure."""
    (tmp_path / "a.yaml").write_text("a: 1\n", encoding="utf-8")
    with patch(_YAML_PYTHON, return_value=sys.executable), \
            patch("subprocess.run") as run:
        run.return_value.returncode = 2
        run.return_value.stdout = ""
        run.return_value.stderr = "invalid config"
        found = run_yamllint(tmp_path)
    assert [v.violation_type for v in found] == [TOOL_ERROR]


def test_eslint_without_report_fails(tmp_path: Path) -> None:
    """ESLint that prints no JSON report is a tool failure."""
    (tmp_path / "eslint.config.js").write_text("export default [];\n", encoding="utf-8")
    with patch(_NODE_RESOLVE, return_value=_exits(2, "")):
        found = run_eslint(tmp_path)
    assert [v.violation_type for v in found] == [TOOL_ERROR]


def test_npx_fallback_never_downloads(tmp_path: Path) -> None:
    """The npx fallback uses ``--no``, so validation never installs packages."""
    (tmp_path / "a.md").write_text("# A\n", encoding="utf-8")
    with patch("shutil.which", return_value=None), \
            patch("subprocess.run") as run:
        run.return_value.returncode = 0
        run.return_value.stdout = ""
        run.return_value.stderr = ""
        run_markdownlint(tmp_path)
    assert run.call_args.args[0][1:3] == ["--no", "markdownlint-cli"]
