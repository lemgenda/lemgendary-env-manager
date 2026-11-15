"""Unit and regression tests for line length guard and reflow engine."""

from __future__ import annotations

from pathlib import Path

from env_manager.line_length import (
    DEFAULT_MD_LIMIT,
    DEFAULT_PY_LIMIT,
    GuardOptions,
    audit_file,
    check_markdown_file,
    check_python_file,
    discover_files,
    is_heading,
    is_list_item,
    is_math_fence,
    is_table_row,
    is_url_or_link_def,
    process_file,
    reflow_markdown,
    reflow_python,
    run_check,
    run_fix,
)


def test_markdown_predicates() -> None:
    """Verify Markdown syntactic structure recognition predicates."""
    assert DEFAULT_MD_LIMIT == 80
    assert DEFAULT_PY_LIMIT == 100
    assert is_table_row("| Col1 | Col2 |") is True
    assert is_table_row("|---|---|") is True
    assert is_table_row("Normal text") is False

    assert is_math_fence("$$") is True
    assert is_math_fence("$$x = y$$") is True
    assert is_math_fence("$x = y$") is False

    assert is_heading("# Title") is True
    assert is_heading("### Subtitle") is True
    assert is_heading("Not a heading") is False

    assert is_url_or_link_def("https://example.com/test") is True
    assert is_url_or_link_def("[docs]: https://example.com") is True
    assert is_url_or_link_def("See [docs](https://example.com)") is False

    is_l, prefix, content = is_list_item("- First item")
    assert is_l is True
    assert prefix == "- "
    assert content == "First item"

    is_l2, prefix2, content2 = is_list_item("  1. Numbered item")
    assert is_l2 is True
    assert prefix2 == "  1. "
    assert content2 == "Numbered item"


def test_markdown_audit_clean_and_defects(tmp_path: Path) -> None:
    """Verify Markdown auditing detects lines > 80 and respects exemptions."""
    md_file = tmp_path / "test.md"
    content = (
        "# Short Heading\n\n"
        "This is a compliant paragraph that fits comfortably within eighty columns.\n\n"
        "| Very Long Column Header 1 | Very Long Column Header 2 | Very Long Column Header 3 |\n"
        "|---|---|---|\n\n"
        "```python\n"
        "code_exceeding_eighty_chars_inside_code_block_is_skipped = 123456789\n"
        "```\n\n"
        "$$"
        "\\int_{0}^{\\infty} e^{-x^2} dx = \\frac{\\sqrt{\\pi}}{2} "
        "\\quad \\text{very long math equation block}\n"
        "$$\n\n"
        "https://example.com/very/long/url/that/exceeds/the/eighty/character/limit/completely\n\n"
        "This line is deliberately crafted to be far too long for the eighty character limit and "
        "must trigger a formal defect in the validator.\n"
    )
    md_file.write_text(content, encoding="utf-8")

    opts = GuardOptions(md_limit=80)
    violations = check_markdown_file(md_file, opts)
    assert len(violations) == 1
    assert violations[0].line_number == 17
    assert violations[0].length > 80

    # Also test audit_file dispatcher
    via_audit = audit_file(md_file, opts)
    assert len(via_audit) == 1


def test_markdown_heading_exemption(tmp_path: Path) -> None:
    """Verify ignore_headings option exempts long header lines."""
    md_file = tmp_path / "headings.md"
    md_file.write_text(
        "### v16.9.113 - Manifold Verification Pipeline, Visual Invariant QA & Shard Sidecars\n",
        encoding="utf-8",
    )

    opts_strict = GuardOptions(md_limit=80, ignore_headings=False)
    violations = check_markdown_file(md_file, opts_strict)
    assert len(violations) == 1

    opts_lenient = GuardOptions(md_limit=80, ignore_headings=True)
    violations_lenient = check_markdown_file(md_file, opts_lenient)
    assert len(violations_lenient) == 0


def test_python_audit_clean_and_defects(tmp_path: Path) -> None:
    """Verify Python audit detects lines exceeding 100 characters."""
    py_file = tmp_path / "test.py"
    clean_line = "x = 42\n"
    long_line = "# " + "a" * 120 + "\n"
    py_file.write_text(clean_line + long_line, encoding="utf-8")

    violations = check_python_file(py_file, limit=100)
    assert len(violations) == 1
    assert violations[0].line_number == 2
    assert violations[0].length > 100


def test_reflow_markdown_paragraphs_and_lists() -> None:
    """Verify automatic reflowing wraps prose and bullet items to <= 80 columns."""
    long_prose = (
        "The verification pipeline evaluates output manifolds against formal mathematical "
        "and structural contracts across vision, financial time-series, and instruction datasets."
    )
    reflowed = reflow_markdown(long_prose, width=80)
    for line in reflowed.splitlines():
        assert len(line) <= 80

    long_list = (
        "- **Schema Conformance**: Every sample within .tar or .tar.zst shards must contain "
        "an unbroken contiguous sequence of expected member extensions without orphan keys."
    )
    reflowed_list = reflow_markdown(long_list, width=80)
    lines = reflowed_list.splitlines()
    assert len(lines) > 1
    assert lines[0].startswith("- **Schema Conformance**:")
    assert lines[1].startswith("  ")  # Preserves sub-indentation
    for line in lines:
        assert len(line) <= 80


def test_reflow_python_comments_and_docstrings() -> None:
    """Verify automatic reflowing wraps long Python comments and docstrings."""
    long_comment = "# " + "word " * 30
    reflowed_c = reflow_python(long_comment, width=80)
    for line in reflowed_c.splitlines():
        assert len(line) <= 80
        assert line.startswith("# ")

    docstring = '"""\n' + ("long descriptive sentence that must be wrapped. " * 5) + '\n"""'
    reflowed_doc = reflow_python(docstring, width=80)
    for line in reflowed_doc.splitlines():
        assert len(line) <= 80


def test_process_file_fix_in_place(tmp_path: Path) -> None:
    """Verify process_file applies reflowing in-place when fix=True."""
    target = tmp_path / "needs_fix.md"
    target.write_text(
        "A very long line that exceeds the standard eighty column boundary and requires "
        "automatic in-place remediation by the line fixer utility.\n",
        encoding="utf-8",
    )

    opts_dry = GuardOptions(md_limit=80, fix=False)
    assert process_file(target, opts_dry) is True
    # Still long in dry run
    assert len(target.read_text(encoding="utf-8").splitlines()[0]) > 80

    opts_fix = GuardOptions(md_limit=80, fix=True)
    assert process_file(target, opts_fix) is True
    # Modified in-place
    fixed_lines = target.read_text(encoding="utf-8").splitlines()
    for l in fixed_lines:
        assert len(l) <= 80


def test_cli_runners_and_discovery(tmp_path: Path) -> None:
    """Verify file discovery and CLI check/fix runner exit codes."""
    sub = tmp_path / "sub"
    sub.mkdir()
    f1 = sub / "clean.md"
    f1.write_text("Short line.\n", encoding="utf-8")
    f2 = sub / "clean.py"
    f2.write_text("y = 1\n", encoding="utf-8")

    discovered = discover_files([tmp_path])
    assert f1 in discovered
    assert f2 in discovered

    opts = GuardOptions(md_limit=80, py_limit=100)
    assert run_check([f1, f2], opts, tmp_path) == 0
    assert run_fix([f1, f2], opts, tmp_path) == 0

    bad = sub / "bad.md"
    bad.write_text("x" * 90 + "\n", encoding="utf-8")
    assert run_check([bad], opts, tmp_path) == 1
