"""New validation gates (REVIEW_REPORT D-16): suppressions, absolute paths, Pylint."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from env_manager.validation.gate import Bucket, Gate, ProjectContext
from env_manager.validation.gates.absolute_paths import ABSOLUTE_PATH, run_absolute_path_scan
from env_manager.validation.gates.pylint_gate import parse_pylint_json, run_pylint
from env_manager.validation.gates.suppressions import SUPPRESSION, run_suppression_scan
from env_manager.validation.models import TOOL_ERROR, FileValidationViolation
from env_manager.validator import run_gates

# Directive text is assembled at runtime so this file holds no directive itself.
_HASH = "#"
_PY_DIRECTIVE = f"x = 1  {_HASH} type: ignore\n"
_TS_DIRECTIVE = "// @ts-" + "ignore\nconst a = 1;\n"
_RUST_ALLOW = _HASH + "[allow(dead_code)]\nfn main() {}\n"
_DRIVE_PATH = "C" + ":" + "/data/x.csv"


def _types(found: list[FileValidationViolation]) -> list[str]:
    return [v.violation_type for v in found]


def test_python_suppression_comment_is_flagged(tmp_path: Path) -> None:
    """A real comment directive is a suppression violation."""
    (tmp_path / "a.py").write_text(_PY_DIRECTIVE, encoding="utf-8")
    found = run_suppression_scan(tmp_path)
    assert _types(found) == [SUPPRESSION]
    assert found[0].line_number == 1


def test_directive_inside_string_is_not_flagged(tmp_path: Path) -> None:
    """Only comments count; a string that mentions a directive passes."""
    (tmp_path / "a.py").write_text(f"TEXT = {_PY_DIRECTIVE.strip()!r}\n", encoding="utf-8")
    assert not run_suppression_scan(tmp_path)


def test_warning_ignore_filter_is_flagged(tmp_path: Path) -> None:
    """``warnings.filterwarnings("ignore")`` is a programmatic suppression."""
    call = "warnings." + "simplefilter(action='ignore')"
    (tmp_path / "a.py").write_text(f"import warnings\n{call}\n", encoding="utf-8")
    found = run_suppression_scan(tmp_path)
    assert (_types(found), found[0].line_number) == ([SUPPRESSION], 2)


def test_typescript_and_rust_directives_are_flagged(tmp_path: Path) -> None:
    """TS/JS comment directives and Rust ``allow`` attributes are flagged."""
    (tmp_path / "a.ts").write_text(_TS_DIRECTIVE, encoding="utf-8")
    (tmp_path / "b.rs").write_text(_RUST_ALLOW, encoding="utf-8")
    assert _types(run_suppression_scan(tmp_path)) == [SUPPRESSION, SUPPRESSION]


def test_absolute_path_is_flagged(tmp_path: Path) -> None:
    """A Windows drive path in a text file is flagged; a relative one is not."""
    (tmp_path / "a.md").write_text(f"see {_DRIVE_PATH}\n", encoding="utf-8")
    (tmp_path / "b.py").write_text('DATA = "../data/x.csv"\nURL = "http://x/y"\n', encoding="utf-8")
    found = run_absolute_path_scan(tmp_path)
    assert (_types(found), Path(found[0].file_path).name) == ([ABSOLUTE_PATH], "a.md")


def test_escape_sequence_is_not_a_drive_path(tmp_path: Path) -> None:
    """Notebook JSON such as ``if x in f:\n`` is not a drive path."""
    notebook = '{"src": ["if x in f:' + chr(92) + 'n", "y"]}'
    (tmp_path / "a.ipynb").write_text(notebook, encoding="utf-8")
    assert not run_absolute_path_scan(tmp_path)


def test_parse_pylint_json() -> None:
    """Each Pylint message becomes a ``pylint/<symbol>`` violation."""
    out = '[{"path": "a.py", "line": 3, "symbol": "unused-import", "message": "Unused os"}]'
    (v,) = parse_pylint_json(out)
    assert (v.violation_type, v.line_number, v.file_path) == ("pylint/unused-import", 3, "a.py")


def test_pylint_skips_projects_without_python(tmp_path: Path) -> None:
    """No Python files, no Pylint run."""
    (tmp_path / "a.md").write_text("# A\n", encoding="utf-8")
    assert not run_pylint(tmp_path)


def test_pylint_missing_fails_closed(tmp_path: Path) -> None:
    """Pylint not installed (exit 1, no JSON) is a tool failure."""
    (tmp_path / "a.py").write_text("X = 1\n", encoding="utf-8")
    proc = subprocess.CompletedProcess([], 1, stdout="", stderr="No module named pylint")
    with patch("env_manager.validation.gates.pylint_gate.run_tool", return_value=proc):
        found = run_pylint(tmp_path)
    assert _types(found) == [TOOL_ERROR]


def test_pylint_reports_findings(tmp_path: Path) -> None:
    """Pylint runs for real and its findings fail the project."""
    (tmp_path / "a.py").write_text("import os\n", encoding="utf-8")
    with patch("env_manager.validation.gates.pylint_gate.get_env_manager_python",
               return_value=sys.executable):
        found = run_pylint(tmp_path)
    assert "pylint/unused-import" in _types(found)


def test_registered_gate_runs_without_runner_changes(tmp_path: Path) -> None:
    """A new gate plugs in through the registry list (OCP)."""
    marker = FileValidationViolation(str(tmp_path), 0, "custom", "found")
    gate = Gate("custom", Bucket.DOMAIN, lambda _p: [marker])
    skipped = Gate("never", Bucket.LINT, lambda _p: [marker], applies=lambda _c: False)
    found = run_gates(ProjectContext(tmp_path), [gate, skipped])
    assert (found[Bucket.DOMAIN], found[Bucket.LINT]) == ([marker], [])
