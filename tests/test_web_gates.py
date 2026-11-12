"""HTML gates name the file, audit every page and include the GUI shell (audit 4.2.3)."""

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from env_manager.validation.gate import ProjectContext
from env_manager.validation.gates import web
from env_manager.validation.models import TOOL_ERROR
from env_manager.validation.registry import has_static_html

PAGE = "<!DOCTYPE html><html lang=\"en\"><head><title>x</title></head><body></body></html>\n"


def _site(root: Path) -> list[Path]:
    pages = [root / "index.html", root / "papers" / "a.html", root / "papers" / "b.html"]
    for page in pages:
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(PAGE, encoding="utf-8")
    (root / "dist").mkdir()
    (root / "dist" / "index.html").write_text(PAGE, encoding="utf-8")
    return pages


def test_html_files_skip_build_output(tmp_path: Path) -> None:
    """Build output is not validated; every source page is."""
    pages = _site(tmp_path)
    assert web.html_files(tmp_path) == sorted(pages)


def test_html_validate_reports_the_file(tmp_path: Path) -> None:
    """Each finding names the page it came from."""
    pages = _site(tmp_path)
    report = [
        {"filePath": str(pages[1]), "messages": [
            {"ruleId": "no-dup-id", "severity": 2, "message": "dup", "line": 7},
            {"ruleId": "long-title", "severity": 1, "message": "long", "line": 2},
        ]},
        {"filePath": "papers/b.html", "messages": []},
    ]
    done = subprocess.CompletedProcess([], 1, stdout=json.dumps(report), stderr="")
    with patch.object(web, "resolve_tool_cmd", return_value=["html-validate"]), \
            patch.object(web, "run_tool", return_value=done) as run:
        found = web.run_html_validate(tmp_path)
    assert [(v.file_path, v.line_number, v.violation_type) for v in found] == [
        (str(pages[1]), 7, "html_error"), (str(pages[1]), 2, "html_warning"),
    ]
    assert run.call_args.args[0][1:3] == ["--formatter", "json"]


def test_html_validate_without_report_fails_closed(tmp_path: Path) -> None:
    """No JSON report means the tool failed."""
    _site(tmp_path)
    crashed = subprocess.CompletedProcess([], 1, stdout="", stderr="boom")
    with patch.object(web, "resolve_tool_cmd", return_value=["html-validate"]), \
            patch.object(web, "run_tool", return_value=crashed):
        found = web.run_html_validate(tmp_path)
    assert [v.violation_type for v in found] == [TOOL_ERROR]


def test_pa11y_audits_every_page(tmp_path: Path) -> None:
    """pa11y runs once per page, not only on ``index.html``."""
    pages = _site(tmp_path)
    issue = [{"code": "WCAG2AA.H37", "message": "Img missing alt", "selector": "img"}]
    clean = subprocess.CompletedProcess([], 0, stdout="[]", stderr="")
    dirty = subprocess.CompletedProcess([], 2, stdout=json.dumps(issue), stderr="")
    with patch.object(web, "resolve_tool_cmd", return_value=["pa11y"]), \
            patch.object(web, "run_tool", side_effect=[clean, dirty, clean]) as run:
        found = web.run_pa11y_wcag(tmp_path)
    audited = [call.args[0][-1] for call in run.call_args_list]
    assert audited == [p.resolve().as_uri() for p in sorted(pages)]
    assert [(v.file_path, v.violation_type) for v in found] == [
        (str(sorted(pages)[1]), "wcag_aa_violation"),
    ]


def test_gui_index_is_not_skipped(tmp_path: Path) -> None:
    """A Tauri app's ``index.html`` is validated like any other page."""
    (tmp_path / "src-tauri").mkdir()
    (tmp_path / "index.html").write_text(PAGE, encoding="utf-8")
    assert has_static_html(ProjectContext(tmp_path))
