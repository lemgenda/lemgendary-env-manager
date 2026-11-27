"""Unit tests for ``env_manager.gui_docs`` and ``docs_audit_runner``."""

import json
import struct
import zlib
from pathlib import Path

from env_manager.ecosystem import ecosystem
from env_manager.gui_docs.html_renderer import HtmlPageConfig, render_markdown_to_html
from env_manager.gui_docs.manual_sync import sync_gui_manual
from env_manager.gui_docs.registry_sync import scan_gui_control_ids, sync_gui_registry
from env_manager.gui_docs.screenshots import read_png_dimensions, validate_captured_screenshots
from env_manager.gui_docs.specs import FIGURE_SPECS, CalloutSpec, FigureSpec
from env_manager.validation.docs_audit_runner import run_docs_audit


def _write_minimal_png(path: Path, width: int = 32, height: int = 24) -> None:
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    ihdr = struct.pack(">I", len(ihdr_data)) + b"IHDR" + ihdr_data
    ihdr += struct.pack(">I", zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF)
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND") & 0xFFFFFFFF)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(sig + ihdr + iend)


_MIN_FIGURE_COUNT = 30


def test_figure_specs_have_sequential_callouts() -> None:
    """Every canonical FigureSpec must have sequential 1..K callouts."""
    assert len(FIGURE_SPECS) >= _MIN_FIGURE_COUNT
    for spec in FIGURE_SPECS:
        assert not spec.validate_callouts()


def test_html_renderer_preserves_math_and_alerts() -> None:
    """HTML renderer outputs TOC, sections, callout divs, and verbatim math."""
    md = "\n".join([
        "# Sample Paper",
        "",
        "## Category 03 | Hub",
        "",
        "**Version**: `v2.10.82`",
        "",
        "---",
        "",
        "## Table of Contents",
        "",
        "- [1. Overview](#1-overview)",
        "",
        "---",
        "",
        "## 1. Overview",
        "",
        "> [!NOTE] Sidecar port is active.",
        "",
        "Inline math $x + y$ and display:",
        "",
        "$$a^2 + b^2 = c^2$$",
        "",
    ])
    cfg = HtmlPageConfig(
        title="Sample Paper",
        subtitle="Category 03 | Hub",
        canonical_url="https://docs.lemgenda.hr/papers/sample.html",
        source_md_rel="MD-Papers/SAMPLE.md",
        active_nav="Sample",
        nav_links=(("Sample", "sample.html"),),
    )
    rendered = render_markdown_to_html(md, cfg)
    assert '<nav class="toc-sidebar" aria-label="Table of Contents">' in rendered
    assert '<section id="sec-1-overview">' in rendered
    assert '<div class="callout callout-note">' in rendered
    assert "$x + y$" in rendered
    assert "$$a^2 + b^2 = c^2$$" in rendered
    assert 'class="skip-link"' in rendered


def test_png_dimension_reader_and_validator(tmp_path: Path) -> None:
    """Valid PNGs pass dimension check and sequential callout check."""
    png_path = tmp_path / "demo.png"
    _write_minimal_png(png_path, 64, 48)
    assert read_png_dimensions(png_path) == (64, 48)
    spec = FigureSpec(
        filename="demo.png",
        title="Demo",
        tab_id="tab-dashboard",
        callouts=(CalloutSpec(1, "#tab-dashboard", "Dash", "Dashboard"),),
    )
    assert not validate_captured_screenshots(tmp_path, (spec,))


def test_registry_and_manual_sync_roundtrip(tmp_path: Path) -> None:
    """Registry and manual sync update MD, HTML, and documents.json cleanly."""
    gui_src = tmp_path / "lemgendary-ai-studio-gui" / "src"
    gui_src.mkdir(parents=True)
    (gui_src / "App.tsx").write_text(
        '<button id="tab-dashboard">Dash</button><button id="pipeline-btn-run-custom" />',
        encoding="utf-8",
    )
    (tmp_path / "lemgendary-ai-studio-gui" / "package.json").write_text(
        json.dumps({"version": "2.10.82"}), encoding="utf-8"
    )

    docs = tmp_path / "lemgendary-docs"
    (docs / "MD-Papers").mkdir(parents=True)
    (docs / "papers").mkdir(parents=True)
    (docs / "manifest").mkdir(parents=True)
    (docs / "manifest" / "documents.json").write_text(
        json.dumps({
            "document_baseline": "v2.10.82",
            "total_publications": 2,
            "documents": [
                {
                    "id": "gui-control-registry",
                    "baseline_version": "v2.10.70",
                    "last_audited": "2026-01-01",
                },
                {
                    "id": "ai-studio-manual",
                    "baseline_version": "v2.10.70",
                    "last_audited": "2026-01-01",
                },
            ],
        }),
        encoding="utf-8",
    )
    (docs / "MD-Papers" / "GUI_CONTROL_REGISTRY.md").write_text(
        "# Registry\n\n**Version**: `v2.10.70` · **Last audited**: 2026-01-01\n\n---\n\n"
        "## 1. Abstract\n\nPort 8100.\n",
        encoding="utf-8",
    )
    _write_minimal_png(docs / "assets" / "gui" / "gui_shell_header_sidebar.png")
    (docs / "MD-Papers" / "MANUAL_AI_STUDIO_GUI.md").write_text(
        "# Manual\n\n**Version**: `v2.10.70` · **Last audited**: 2026-01-01\n\n---\n\n"
        "## 1. Abstract\n\n![Shell](../assets/gui/gui_shell_header_sidebar.png)\n",
        encoding="utf-8",
    )

    scanned = scan_gui_control_ids(gui_src)
    assert "tab-dashboard" in scanned["static_ids"]

    reg_res = sync_gui_registry(root=tmp_path)
    assert reg_res["gui_version"] == "v2.10.82"
    ds_port = ecosystem().require_service("dataset-compiler").port
    assert f"Port {ds_port}" in (docs / "MD-Papers" / "GUI_CONTROL_REGISTRY.md").read_text(
        encoding="utf-8"
    )
    assert (docs / "papers" / "gui-control-registry.html").exists()

    man_res = sync_gui_manual(root=tmp_path)
    assert man_res["referenced_figures"] == 1
    assert (docs / "papers" / "ai-studio-manual.html").exists()


def test_docs_audit_detects_broken_link_and_stale_port(tmp_path: Path) -> None:
    """docs-audit flags broken relative links and stale port 8100 in docs MD."""
    docs_dir = tmp_path / "lemgendary-docs"
    docs_dir.mkdir(parents=True, exist_ok=True)
    (docs_dir / "README.md").write_text(
        "# Docs Hub\n\nSee [Missing](./missing.md) on port 8100.\n",
        encoding="utf-8",
    )
    summary = run_docs_audit(root=tmp_path)
    assert not summary.passed
    types = {v.violation_type for v in summary.extra_violations}
    assert "broken_relative_link" in types
    assert "ecosystem_ssot_drift" in types
