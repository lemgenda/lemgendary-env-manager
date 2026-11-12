"""Emoji, suppression and compile scanners cover the former blind spots (audit 4.2.4)."""

import json
from pathlib import Path

from env_manager.validation.files import is_text_source, iter_project_files
from env_manager.validation.gates.config_disables import scan_config_disables
from env_manager.validation.gates.source_files import compile_python_file, scan_sources
from env_manager.validation.gates.suppressions import SUPPRESSION, run_suppression_scan

HOURGLASS, WARNING_SIGN, STAR = chr(0x231B), chr(0x26A0), chr(0x2B50)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _lines(found: list) -> list[tuple[str, int]]:
    return [(Path(v.file_path).name, v.line_number) for v in found]


def test_every_text_type_and_hook_is_scanned(tmp_path: Path) -> None:
    """Rust, JS, shell, notebooks and extension-less hooks are text sources."""
    for name in ("a.rs", "a.js", "a.mjs", "a.sh", "a.psm1", "a.ipynb", ".githooks/pre-commit"):
        assert is_text_source(tmp_path / name), name
    assert not is_text_source(tmp_path / "a.png")


def test_code_carrying_hidden_folders_are_walked(tmp_path: Path) -> None:
    """``.githooks``, ``.github`` and ``.agents`` are walked; other hidden folders are not."""
    project = tmp_path / "project"
    for folder in (".githooks", ".github", ".agents", ".cache", ".idea"):
        _write(project / folder / "f.md", "x\n")
    walked = {p.parent.name for p in iter_project_files(project)}
    assert walked == {".githooks", ".github", ".agents"}


def test_wider_emoji_ranges_are_flagged(tmp_path: Path) -> None:
    """Misc Technical, Misc Symbols and Symbols-and-Arrows emoji are caught, in a .js file too."""
    _write(tmp_path / "a.js", f"// {HOURGLASS}\n// {WARNING_SIGN}\n// {STAR}\n// plain\n")
    _write(tmp_path / ".githooks" / "pre-commit", f"echo {STAR}\n")
    found = scan_sources(tmp_path).emoji_violations
    assert sorted(_lines(found)) == [("a.js", 1), ("a.js", 2), ("a.js", 3), ("pre-commit", 1)]


def test_silent_python_except_is_a_suppression(tmp_path: Path) -> None:
    """``except: pass`` and ``except: ...`` are flagged; a logged handler is not."""
    source = (
        "try:\n    x = 1\nexcept ValueError:\n    pass\n"
        "try:\n    x = 2\nexcept KeyError:\n    ...\n"
        "try:\n    x = 3\nexcept OSError as exc:\n    print(exc)\n"
    )
    _write(tmp_path / "a.py", source)
    assert _lines(run_suppression_scan(tmp_path)) == [("a.py", 3), ("a.py", 7)]


def test_bare_imported_warning_filter_is_flagged(tmp_path: Path) -> None:
    """``from warnings import simplefilter`` does not hide the call."""
    _write(tmp_path / "a.py", "from warnings import simplefilter\nsimplefilter('ignore')\n")
    assert _lines(run_suppression_scan(tmp_path)) == [("a.py", 2)]


def test_empty_js_catch_is_flagged(tmp_path: Path) -> None:
    """Empty and comment-only catch blocks are flagged; a handled one is not."""
    source = (
        "try { a(); } catch (e) {}\n"
        "try { b(); } catch {\n  // ignore\n}\n"
        "try { c(); } catch (e) { report(e); }\n"
    )
    _write(tmp_path / "a.ts", source)
    assert _lines(run_suppression_scan(tmp_path)) == [("a.ts", 1), ("a.ts", 2)]


def test_powershell_silent_error_action_is_flagged(tmp_path: Path) -> None:
    """``SilentlyContinue``/``Ignore`` error actions are flagged; comments are not."""
    silent = "Silently" + "Continue"
    source = (
        f"Get-Item x -ErrorAction {silent}\n"
        "Get-Item y -EA Ignore\n"
        f"$ErrorActionPreference = '{silent}'\n"
        f"# never use -ErrorAction {silent}\n"
        "Get-Item z -ErrorAction Stop\n"
    )
    _write(tmp_path / "a.ps1", source)
    assert _lines(run_suppression_scan(tmp_path)) == [("a.ps1", 1), ("a.ps1", 2), ("a.ps1", 3)]


def test_config_rule_disables_are_flagged(tmp_path: Path) -> None:
    """Ruff, Pylint, Pyright, markdownlint and ESLint config disables are found."""
    pyproject = _write(tmp_path / "pyproject.toml", (
        "[tool.ruff.lint]\nselect = ['ALL']\nignore = ['E501']\n"
        "[tool.pylint.messages_control]\ndisable = ['C0114']\n"
        "[tool.pyright]\nreportMissingImports = false\n"
    ))
    pylintrc = _write(tmp_path / ".pylintrc", "[MESSAGES CONTROL]\ndisable=C0116\n")
    markdownlint = _write(tmp_path / ".markdownlint.json",
                          json.dumps({"default": True, "MD013": False, "MD024": {"x": False}}))
    eslint = _write(tmp_path / "eslint.config.js",
                    "export default [{ rules: { 'no-console': 'off', eqeqeq: 'error' } }];\n")
    counts = [len(scan_config_disables(p)) for p in (pyproject, pylintrc, markdownlint, eslint)]
    assert counts == [3, 1, 1, 1]
    assert {v.violation_type for v in run_suppression_scan(tmp_path)} == {SUPPRESSION}


def test_clean_configs_pass(tmp_path: Path) -> None:
    """Configs that only tune options disable nothing."""
    pyproject = _write(tmp_path / "pyproject.toml", "[tool.ruff]\nline-length = 100\n")
    markdownlint = _write(tmp_path / ".markdownlint.json",
                          json.dumps({"default": True, "MD013": {"headings": False}}))
    assert not scan_config_disables(pyproject)
    assert not scan_config_disables(markdownlint)


def test_compile_check_writes_no_bytecode(tmp_path: Path) -> None:
    """Validation compiles in memory and leaves no ``__pycache__`` behind."""
    good = _write(tmp_path / "good.py", "X = 1\n")
    bad = _write(tmp_path / "bad.py", "def broken(:\n")
    assert compile_python_file(good) is None
    error = compile_python_file(bad)
    assert error is not None
    assert (error.violation_type, error.line_number) == ("syntax_error", 1)
    assert not (tmp_path / "__pycache__").exists()
