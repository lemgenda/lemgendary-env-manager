"""Synchronize AI Studio GUI Operator Manual Markdown, HTML, and manifest."""

import re
from datetime import date
from pathlib import Path

from env_manager.gui_docs.html_renderer import HtmlPageConfig
from env_manager.gui_docs.registry_sync import (
    _update_documents_manifest,
    normalize_doc_metadata,
    publish_doc_pair,
    read_gui_version,
)
from env_manager.gui_docs.specs import FIGURE_SPECS
from env_manager.workspace import workspace_root

_MANUAL_NAV: tuple[tuple[str, str], ...] = (
    ("Hub Index", "../index.html"),
    ("Manuals Hub", "manuals-hub.html"),
    ("AI Studio GUI Manual", "ai-studio-manual.html"),
    ("Master CLI Manual", "cli-manual.html"),
    ("Master API Manual", "api-manual.html"),
)

_IMG_REF_RE = re.compile(r"!\[[^\]]*\]\(\.\./assets/gui/([^)]+)\)")


def verify_manual_figures(docs_dir: Path, md_text: str) -> list[str]:
    """Verify all referenced GUI screenshots exist and have valid FIGURE_SPECS."""
    errors: list[str] = []
    for spec in FIGURE_SPECS:
        errors.extend(spec.validate_callouts())
    gui_assets = docs_dir / "assets" / "gui"
    referenced = sorted(set(_IMG_REF_RE.findall(md_text)))
    for filename in referenced:
        if not (gui_assets / filename).exists():
            errors.append(f"Manual references missing screenshot: assets/gui/{filename}")
    return errors


def sync_gui_manual(root: Path | None = None) -> dict[str, object]:
    """Sync AI Studio GUI Operator Manual MD, HTML, and manifest."""
    ws = root or workspace_root()
    gui_dir = ws / "lemgendary-ai-studio-gui"
    docs_dir = ws / "lemgendary-docs"
    md_path = docs_dir / "MD-Papers" / "MANUAL_AI_STUDIO_GUI.md"
    html_path = docs_dir / "papers" / "ai-studio-manual.html"

    gui_version = read_gui_version(gui_dir)
    today = date.today().isoformat()
    md_text = normalize_doc_metadata(md_path.read_text(encoding="utf-8"), gui_version, today)

    figure_errors = verify_manual_figures(docs_dir, md_text)
    if figure_errors:
        raise RuntimeError("Manual figure verification failed:\n" + "\n".join(figure_errors))

    cfg = HtmlPageConfig(
        title="LemGendary AI Studio GUI: Comprehensive Operator Manual",
        subtitle="Category 03 | LemGendary AI Documentation Hub | Master Operations Manual",
        canonical_url="https://docs.lemgenda.hr/papers/ai-studio-manual.html",
        source_md_rel="MD-Papers/MANUAL_AI_STUDIO_GUI.md",
        active_nav="AI Studio GUI Manual",
        nav_links=_MANUAL_NAV,
        companion_links=(("Architectural Whitepaper", "ai-studio-whitepaper.html"),),
    )
    final_md = publish_doc_pair(ws, html_path, cfg, md_text)
    _update_documents_manifest(docs_dir, "ai-studio-manual", gui_version, today)

    return {
        "gui_version": gui_version,
        "figure_specs_count": len(FIGURE_SPECS),
        "referenced_figures": len(set(_IMG_REF_RE.findall(final_md))),
        "md_path": str(md_path),
        "html_path": str(html_path),
    }
