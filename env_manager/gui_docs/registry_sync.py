"""Synchronize GUI Control Registry Markdown, HTML, and manifest."""

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from env_manager.ecosystem import ecosystem
from env_manager.gui_docs.html_renderer import HtmlPageConfig, render_markdown_to_html
from env_manager.line_length import DEFAULT_MD_LIMIT, GuardOptions, run_fix
from env_manager.workspace import workspace_root

_STATIC_ID_RE = re.compile(r'\bid="([a-zA-Z0-9_-]+)"')
_TEMPLATE_ID_RE = re.compile(r"\bid=\{`([^`]+)`\}")
_LEGACY_DATASET_PORT = 8100

_REGISTRY_NAV: tuple[tuple[str, str], ...] = (
    ("Hub Index", "../index.html"),
    ("Architecture Hub", "ecosystem-architecture.html"),
    ("Current State", "current-state.html"),
    ("Env Manager", "env_manager.html"),
    ("Dataset Compiler", "dataset-compiler.html"),
    ("Degradation Engine", "degradation-engine.html"),
    ("Training Suite", "training-suite-master.html"),
    ("Desktop GUI", "ai-studio-whitepaper.html"),
    ("GUI Registry", "gui-control-registry.html"),
    ("Versioning Policy", "versioning-policy.html"),
)


def scan_gui_control_ids(gui_src_dir: Path) -> dict[str, list[str]]:
    """Inspect ``src/**/*.{ts,tsx}`` for static and template DOM IDs."""
    static_ids: set[str] = set()
    template_ids: set[str] = set()
    for path in sorted(gui_src_dir.rglob("*.ts*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        static_ids.update(_STATIC_ID_RE.findall(text))
        template_ids.update(_TEMPLATE_ID_RE.findall(text))
    return {
        "static_ids": sorted(static_ids),
        "template_ids": sorted(template_ids),
    }


def read_gui_version(gui_dir: Path) -> str:
    """Return ``vX.Y.Z`` from ``lemgendary-ai-studio-gui/package.json``."""
    pkg = json.loads((gui_dir / "package.json").read_text(encoding="utf-8"))
    raw = str(pkg.get("version", "0.0.0")).lstrip("v")
    return f"v{raw}"


def _update_documents_manifest(
    docs_dir: Path, doc_id: str, version: str, audit_date: str
) -> None:
    manifest_path = docs_dir / "manifest" / "documents.json"
    if not manifest_path.exists():
        return
    data: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in data.get("documents", []):
        if entry.get("id") == doc_id:
            entry["baseline_version"] = version
            entry["last_audited"] = audit_date
            break
    manifest_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def normalize_doc_metadata(md_text: str, gui_version: str, audit_date: str) -> str:
    """Update Version, Last audited, and sidecar port references in Markdown."""
    updated = re.sub(
        r"\*\*Version\*\*:\s*`v[^`]+`",
        f"**Version**: `{gui_version}`",
        md_text,
        count=1,
    )
    updated = re.sub(
        r"\*\*Last audited\*\*:\s*\d{4}-\d{2}-\d{2}",
        f"**Last audited**: {audit_date}",
        updated,
        count=1,
    )
    ds_port = ecosystem().service("dataset-compiler").port
    if ds_port != _LEGACY_DATASET_PORT:
        updated = re.sub(r"\b([Pp])ort 8100\b", rf"\g<1>ort {ds_port}", updated)
        updated = updated.replace("(8100)", f"({ds_port})")
    return updated


def publish_doc_pair(
    ws: Path,
    html_path: Path,
    cfg: HtmlPageConfig,
    md_text: str,
    page_title: str = "",
) -> str:
    """Write normalized Markdown, wrap to 80 cols, and render HTML publication."""
    md_path = ws / "lemgendary-docs" / cfg.source_md_rel
    md_path.write_text(md_text, encoding="utf-8")
    run_fix(
        [md_path],
        GuardOptions(md_limit=DEFAULT_MD_LIMIT, py_limit=100, fix=True),
        ws,
    )
    final_md = md_path.read_text(encoding="utf-8")
    html_path.write_text(
        render_markdown_to_html(final_md, cfg, page_title=page_title), encoding="utf-8"
    )
    return final_md


def sync_gui_registry(
    root: Path | None = None,
    audit_json: Path | None = None,
) -> dict[str, object]:
    """Sync GUI Control Registry MD, HTML, and manifest against GUI source."""
    ws = root or workspace_root()
    gui_dir = ws / "lemgendary-ai-studio-gui"
    docs_dir = ws / "lemgendary-docs"
    md_path = docs_dir / "MD-Papers" / "GUI_CONTROL_REGISTRY.md"
    html_path = docs_dir / "papers" / "gui-control-registry.html"

    gui_version = read_gui_version(gui_dir)
    today = date.today().isoformat()
    scanned = scan_gui_control_ids(gui_dir / "src")
    audit_payload: dict[str, Any] | None = None
    if audit_json is not None and audit_json.exists():
        audit_payload = json.loads(audit_json.read_text(encoding="utf-8"))

    md_text = normalize_doc_metadata(md_path.read_text(encoding="utf-8"), gui_version, today)
    cfg = HtmlPageConfig(
        title="LemGendary AI Studio GUI: Control ID Registry & Operational Boundaries",
        subtitle="Category 03.4 CONTROLS | LemGendary AI Documentation Hub",
        canonical_url="https://docs.lemgenda.hr/papers/gui-control-registry.html",
        source_md_rel="MD-Papers/GUI_CONTROL_REGISTRY.md",
        active_nav="GUI Registry",
        nav_links=_REGISTRY_NAV,
        companion_links=(("Operator Manual", "ai-studio-manual.html"),),
    )
    publish_doc_pair(
        ws,
        html_path,
        cfg,
        md_text,
        page_title="LemGendary AI Studio GUI: Control ID Registry",
    )
    _update_documents_manifest(docs_dir, "gui-control-registry", gui_version, today)

    return {
        "gui_version": gui_version,
        "static_ids": len(scanned["static_ids"]),
        "template_ids": len(scanned["template_ids"]),
        "live_audit_used": audit_payload is not None,
        "md_path": str(md_path),
        "html_path": str(html_path),
    }
