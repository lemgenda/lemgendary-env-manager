"""Domain gates for the LemGendary AI Documentation Hub: paper sync, manifests, suite tests,
and Prompts 35-40 audits (TOC, LaTeX formula syntax, assets, frontend, SEO, and WCAG compliance).
"""

import json
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

from env_manager.validation.models import (
    MANIFEST_READ_ERRORS,
    FileValidationViolation,
    tool_failure,
    unexplained_exit,
)
from env_manager.validation.tooling import get_env_manager_python, run_tool
from env_manager.venv_manager import get_venv_python_path

DOCS_HUB_PROJECTS = ("lemgendary-docs",)
"""Project folders the documentation hub gates apply to."""

DOC_LENGTH_RATIO_MIN = 0.20
DOC_LENGTH_RATIO_MAX = 3.0
"""Allowed Markdown-to-HTML length ratio for a paper and its HTML companion."""

EMOJI_PATTERN = re.compile(
    "[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\u2300-\u23ff]|[\u2b50-\u2b55]"
)


def _doc_violation(
    path: Path, kind: str, message: str, line: int = 1
) -> FileValidationViolation:
    return FileValidationViolation(
        file_path=str(path),
        line_number=line,
        violation_type=kind,
        message=message,
    )


def _check_manifest_files_exist(
    doc_m: Path, cap_j: Path, cap_y: Path
) -> list[FileValidationViolation]:
    violations: list[FileValidationViolation] = []
    if not doc_m.exists():
        violations.append(
            _doc_violation(
                doc_m, "manifest_missing", "documents.json missing from manifest/"
            )
        )
    if not cap_j.exists():
        violations.append(
            _doc_violation(
                cap_j, "manifest_missing", "canonical_capabilities.json missing"
            )
        )
    if not cap_y.exists():
        violations.append(
            _doc_violation(
                cap_y, "manifest_missing", "canonical_capabilities.yaml missing"
            )
        )
    return violations


def audit_manifest_synchronization(
    docs_dir: Path,
) -> list[FileValidationViolation]:
    """Check that documents.json and both canonical_capabilities files agree."""
    doc_m_path = docs_dir / "manifest" / "documents.json"
    cap_j_path = docs_dir / "canonical_capabilities.json"
    cap_y_path = docs_dir / "canonical_capabilities.yaml"

    violations = _check_manifest_files_exist(
        doc_m_path, cap_j_path, cap_y_path
    )
    if violations or not (
        doc_m_path.exists() and cap_j_path.exists() and cap_y_path.exists()
    ):
        return violations

    try:
        with open(doc_m_path, encoding="utf-8") as f:
            dj = json.load(f)
        with open(cap_j_path, encoding="utf-8") as f:
            cj = json.load(f)
        with open(cap_y_path, encoding="utf-8") as f:
            cy = yaml.safe_load(f)

        d_base = dj.get("document_baseline")
        c_base = cj.get("document_baseline")
        y_base = cy.get("document_baseline")

        if not d_base == c_base == y_base:
            violations.append(
                _doc_violation(
                    cap_j_path,
                    "manifest_baseline_mismatch",
                    (
                        f"Document baseline desync: documents.json='{d_base}', "
                        f"canonical_capabilities.json='{c_base}', "
                        f"canonical_capabilities.yaml='{y_base}'"
                    ),
                )
            )

        total_docs = len(dj.get("documents", []))
        if dj.get("total_publications") != total_docs:
            violations.append(
                _doc_violation(
                    doc_m_path,
                    "manifest_count_mismatch",
                    (
                        f"Publications count mismatch in documents.json: "
                        f"header={dj.get('total_publications')} vs count={total_docs}"
                    ),
                )
            )

        if cj != cy:
            violations.append(
                _doc_violation(
                    cap_y_path,
                    "manifest_content_drift",
                    "canonical_capabilities.yaml structure is not identical to JSON",
                )
            )
    except MANIFEST_READ_ERRORS as exc:
        violations.append(
            _doc_violation(
                doc_m_path,
                "manifest_parse_error",
                f"Failed parsing manifests: {exc}",
                line=0,
            )
        )

    return violations


_SKIPPED_H2_TITLES = ("contents", "table of contents", "navigation matrix")


def _content_h2_titles(raw_titles: list[str]) -> list[str]:
    return [
        x
        for x in raw_titles
        if x.lower() not in _SKIPPED_H2_TITLES
        and not x.lower().startswith("category")
    ]


def github_slug(s: str) -> str:
    """Generate GitHub-style anchor slug from heading text."""
    s = s.replace("`", "").replace("*", "")
    s = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", s)
    s = re.sub(
        r"<(?:a|span|code|div|p|img|br|hr)[^>]*>", "", s, flags=re.IGNORECASE
    )
    s = re.sub(r"[^\w\s\-]", "", s)
    s = s.strip().lower()
    s = re.sub(r" ", "-", s)
    return s.strip("-")


def _extract_md_headings(m_raw: str) -> list[str]:
    headings: list[str] = []
    in_code = False
    for line in m_raw.splitlines():
        st = line.strip()
        if st.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        h_match = re.match(r"^(#{1,4})\s+", st)
        if h_match is not None:
            lvl = len(h_match.group(1))
            t = re.sub(r"^#{1,4}\s+", "", st).strip()
            if (
                lvl == 1
                or t.lower().startswith("category ")
                or t.lower() == "table of contents"
            ):
                continue
            headings.append(t)
    return headings


def _build_valid_anchors(headings: list[str]) -> set[str]:
    slug_counts: dict[str, int] = {}
    valid_anchors = set()
    for h in headings:
        base = github_slug(h)
        if base not in slug_counts:
            slug_counts[base] = 0
            valid_anchors.add(base)
        else:
            slug_counts[base] += 1
            valid_anchors.add(f"{base}-{slug_counts[base]}")
    return valid_anchors


def _check_md_toc(
    doc_id: str, mp: Path, m_raw: str
) -> list[FileValidationViolation]:
    violations: list[FileValidationViolation] = []
    pos_toc = m_raw.find("## Table of Contents")
    if pos_toc == -1:
        pos_toc = m_raw.find("# Table of Contents")
    if pos_toc == -1:
        violations.append(
            _doc_violation(
                mp, "doc_toc_missing", f"[{doc_id}] MD missing TOC"
            )
        )
        return violations

    pos_end = m_raw.find("---", pos_toc)
    toc_text = (
        m_raw[pos_toc:pos_end]
        if pos_end != -1
        else m_raw[pos_toc : pos_toc + 2000]
    )

    headings = _extract_md_headings(m_raw)
    valid_anchors = _build_valid_anchors(headings)

    links = re.findall(r"\[([\s\S]*?)\]\(#([^\)]+)\)", toc_text)
    if not links:
        violations.append(
            _doc_violation(mp, "doc_toc_empty", f"[{doc_id}] MD TOC is empty")
        )
    for label, href in links:
        if href not in valid_anchors:
            clean_lbl = label.strip()
            violations.append(
                _doc_violation(
                    mp,
                    "doc_toc_broken_anchor",
                    f"[{doc_id}] Broken MD TOC link: [{clean_lbl}] -> #{href}",
                )
            )
    return violations


def _check_html_toc(
    doc_id: str, hp: Path, h_raw: str
) -> list[FileValidationViolation]:
    violations: list[FileValidationViolation] = []
    nav_match = re.search(
        r"<nav[^>]*aria-label=[\"\']Table of Contents[\"\'][^>]*>([\s\S]*?)</nav>",
        h_raw,
        flags=re.IGNORECASE,
    )
    if not nav_match:
        nav_match = re.search(
            r"<nav[^>]*class=[\"\'][^\"\']*toc[^\"]*[\"\'][^>]*>([\s\S]*?)</nav>",
            h_raw,
            flags=re.IGNORECASE,
        )

    if not nav_match:
        violations.append(
            _doc_violation(
                hp, "doc_html_toc_missing", f"[{doc_id}] HTML missing <nav> TOC"
            )
        )
        return violations

    html_ids = set(
        re.findall(r"id=[\"\']([^\"\']+)[\"\']", h_raw, flags=re.IGNORECASE)
    )
    html_links = re.findall(r"href=[\"\']#([^\"\']+)[\"\']", nav_match.group(1))
    if not html_links:
        violations.append(
            _doc_violation(
                hp, "doc_html_toc_empty", f"[{doc_id}] HTML TOC has 0 links"
            )
        )
    for href in html_links:
        if href not in html_ids:
            violations.append(
                _doc_violation(
                    hp,
                    "doc_html_toc_broken_anchor",
                    f"[{doc_id}] Broken HTML TOC link: #{href}",
                )
            )
    return violations


def _check_toc_integrity(
    doc_id: str, mp: Path, hp: Path, m_raw: str, h_raw: str
) -> list[FileValidationViolation]:
    """Prompt 37: Verify TOC coverage and anchor resolution."""
    v = _check_md_toc(doc_id, mp, m_raw)
    v.extend(_check_html_toc(doc_id, hp, h_raw))
    return v


def _check_display_math(
    doc_id: str, mp: Path, m_raw: str
) -> list[FileValidationViolation]:
    violations: list[FileValidationViolation] = []
    for m in re.finditer(r"\$\$([\s\S]*?)\$\$", m_raw):
        formula = m.group(1).strip()
        for open_b, close_b in [("{", "}"), ("[", "]")]:
            if formula.count(open_b) != formula.count(close_b):
                short_f = formula[:35]
                violations.append(
                    _doc_violation(
                        mp,
                        "doc_latex_unbalanced",
                        f"[{doc_id}] Unbalanced {open_b}{close_b}: {short_f}...",
                    )
                )
        begins = re.findall(r"\\begin\{([a-zA-Z0-9*]+)\}", formula)
        ends = re.findall(r"\\end\{([a-zA-Z0-9*]+)\}", formula)
        if begins != ends:
            violations.append(
                _doc_violation(
                    mp,
                    "doc_latex_mismatched_env",
                    f"[{doc_id}] Mismatched environments: {begins} vs {ends}",
                )
            )
    return violations


def _check_latex_and_math(
    doc_id: str, mp: Path, hp: Path, m_raw: str, h_raw: str
) -> list[FileValidationViolation]:
    """Prompts 38 & 39: Verify LaTeX syntax and MathJax setup."""
    violations = _check_display_math(doc_id, mp, m_raw)

    clean_md = re.sub(r"\$\$[\s\S]*?\$\$", "", m_raw)
    for m in re.finditer(r"(?<!\$)\$(?!\$)([^\n\$]+?)\$(?!\$)", clean_md):
        formula = m.group(1).strip()
        if formula.count("{") != formula.count("}"):
            violations.append(
                _doc_violation(
                    mp,
                    "doc_latex_unbalanced_inline",
                    f"[{doc_id}] Unbalanced curly braces in: ${formula}$",
                )
            )

    if "mathjax" not in h_raw.lower() and "katex" not in h_raw.lower():
        violations.append(
            _doc_violation(
                hp,
                "doc_html_missing_mathjax",
                f"[{doc_id}] HTML companion missing MathJax/KaTeX",
            )
        )
    return violations


def _check_seo_and_a11y_meta(
    doc_id: str, hp: Path, h_raw: str
) -> list[FileValidationViolation]:
    violations: list[FileValidationViolation] = []
    if not re.search(r"<title>([^<]+)</title>", h_raw, re.IGNORECASE):
        violations.append(
            _doc_violation(hp, "doc_seo_missing_title", f"[{doc_id}] Missing <title>")
        )

    desc_pat = r'<meta[^>]*name=["\']description["\'][^>]*content=["\'][^"\']+["\']'
    if not re.search(desc_pat, h_raw, re.IGNORECASE):
        violations.append(
            _doc_violation(
                hp, "doc_seo_missing_description", f"[{doc_id}] Missing meta description"
            )
        )

    canon_pat = r'<link[^>]*rel=["\']canonical["\'][^>]*href=["\'][^"\']+["\']'
    if not re.search(canon_pat, h_raw, re.IGNORECASE):
        violations.append(
            _doc_violation(
                hp, "doc_seo_missing_canonical", f"[{doc_id}] Missing canonical link"
            )
        )

    og_pat = r'<meta[^>]*property=["\']og:title["\']'
    if not re.search(og_pat, h_raw, re.IGNORECASE):
        violations.append(
            _doc_violation(
                hp, "doc_seo_missing_og_title", f"[{doc_id}] Missing og:title"
            )
        )
    return violations


def _check_html_landmarks_and_skip(
    doc_id: str, hp: Path, h_raw: str
) -> list[FileValidationViolation]:
    violations: list[FileValidationViolation] = []
    skip_pat = (
        r'<a[^>]*href=["\']#(main-content|main|content)["\']'
        r'[^>]*class=["\'][^"\']*skip-link[^"\']*["\']'
    )
    if not re.search(skip_pat, h_raw, re.IGNORECASE):
        violations.append(
            _doc_violation(
                hp,
                "doc_wcag_missing_skip_link",
                f"[{doc_id}] Missing keyboard skip link (<a class='skip-link'>)",
            )
        )
    if "<main" not in h_raw:
        violations.append(
            _doc_violation(
                hp, "doc_wcag_missing_main", f"[{doc_id}] Missing <main> landmark"
            )
        )
    if "<nav" not in h_raw:
        violations.append(
            _doc_violation(
                hp, "doc_wcag_missing_nav", f"[{doc_id}] Missing <nav> landmark"
            )
        )
    return violations


def _check_html_images(
    doc_id: str, hp: Path, h_raw: str
) -> list[FileValidationViolation]:
    violations: list[FileValidationViolation] = []
    for m in re.finditer(r"<img\s+([^>]+)>", h_raw, re.IGNORECASE):
        attrs = m.group(1)
        src_m = re.search(r'src=["\']([^"\']+)["\']', attrs, re.IGNORECASE)
        alt_m = re.search(r'alt=["\']([^"\']*)["\']', attrs, re.IGNORECASE)
        if not src_m:
            violations.append(
                _doc_violation(
                    hp, "doc_img_missing_src", f"[{doc_id}] <img> missing src"
                )
            )
            continue
        src = src_m.group(1)
        if alt_m is None:
            violations.append(
                _doc_violation(
                    hp,
                    "doc_img_missing_alt",
                    f"[{doc_id}] <img> missing alt attribute: {src}",
                )
            )
        if not src.startswith(("http://", "https://", "data:")):
            resolved = (hp.parent / src).resolve()
            if not resolved.exists():
                violations.append(
                    _doc_violation(
                        hp,
                        "doc_broken_asset",
                        f"[{doc_id}] Broken image link: {src}",
                    )
                )
    return violations


def _check_html_frontend_seo_a11y(
    doc_id: str, hp: Path, h_raw: str
) -> list[FileValidationViolation]:
    """Prompts 39 & 40: Audit HTML structure, assets, SEO, and accessibility."""
    violations: list[FileValidationViolation] = []

    emoji_m = EMOJI_PATTERN.search(h_raw)
    if emoji_m:
        violations.append(
            _doc_violation(
                hp,
                "doc_emoji_prohibited",
                f"[{doc_id}] Forbidden emoji detected: '{emoji_m.group(0)}'",
            )
        )

    lang_pat = r'<html[^>]*lang=["\'][a-zA-Z\-]+["\']'
    if not re.search(lang_pat, h_raw, re.IGNORECASE):
        violations.append(
            _doc_violation(
                hp,
                "doc_wcag_missing_lang",
                f"[{doc_id}] Missing or invalid <html lang='...'>",
            )
        )

    violations.extend(_check_seo_and_a11y_meta(doc_id, hp, h_raw))
    violations.extend(_check_html_landmarks_and_skip(doc_id, hp, h_raw))
    violations.extend(_check_html_images(doc_id, hp, h_raw))
    return violations


def _paper_parity_violations(
    doc_id: str,
    mp: Path,
    hp: Path,
    m_raw: str,
    h_raw: str,
) -> list[FileValidationViolation]:
    """Compare H2 count, table count, length ratio, TOC, LaTeX, and frontend standards."""
    violations: list[FileValidationViolation] = []

    h2_h = _content_h2_titles(
        [
            re.sub(r"<[^>]+>", "", x).strip()
            for x in re.findall(r"<h2[^>]*>(.*?)</h2>", h_raw, re.I)
        ]
    )
    h2_m = _content_h2_titles(
        [
            re.sub(r"[*_`]", "", x).strip()
            for x in re.findall(r"^##\s+(.+)$", m_raw, re.M)
        ]
    )
    if len(h2_h) != len(h2_m):
        violations.append(
            _doc_violation(
                hp,
                "doc_heading_mismatch",
                f"[{doc_id}] H2 mismatch: HTML({len(h2_h)}) vs MD({len(h2_m)})",
            )
        )

    h_tables = len(re.findall(r"<table", h_raw, re.I))
    m_tables = len(re.findall(r"\|(?:\s*:?-+:?\s*\|)+", m_raw))
    if abs(h_tables - m_tables) > 1:
        violations.append(
            _doc_violation(
                hp,
                "doc_table_mismatch",
                f"[{doc_id}] Table count diff: HTML={h_tables} vs MD={m_tables}",
            )
        )

    ratio = len(m_raw) / len(h_raw) if len(h_raw) > 0 else 0
    if not DOC_LENGTH_RATIO_MIN <= ratio <= DOC_LENGTH_RATIO_MAX:
        violations.append(
            _doc_violation(
                hp,
                "doc_ratio_anomaly",
                f"[{doc_id}] Ratio anomaly: ratio={ratio:.2f}",
            )
        )

    violations.extend(_check_toc_integrity(doc_id, mp, hp, m_raw, h_raw))
    violations.extend(_check_latex_and_math(doc_id, mp, hp, m_raw, h_raw))
    violations.extend(_check_html_frontend_seo_a11y(doc_id, hp, h_raw))

    return violations


def _manifest_doc_violations(
    docs_dir: Path, doc: dict[str, Any]
) -> list[FileValidationViolation]:
    """Check that one manifest entry has both papers and that they stay in parity."""
    doc_id = doc.get("id", "unknown")
    hp = docs_dir / doc.get("html_path", "")
    mp = docs_dir / doc.get("md_path", "")
    if not hp.exists():
        return [
            _doc_violation(
                hp,
                "doc_html_missing",
                f"Companion HTML missing for [{doc_id}]: {doc.get('html_path')}",
            )
        ]
    if not mp.exists():
        return [
            _doc_violation(
                mp,
                "doc_md_missing",
                f"Markdown missing for [{doc_id}]: {doc.get('md_path')}",
            )
        ]
    return _paper_parity_violations(
        doc_id,
        mp,
        hp,
        mp.read_text(encoding="utf-8", errors="ignore"),
        hp.read_text(encoding="utf-8", errors="ignore"),
    )


def audit_docs_synchronization(docs_dir: Path) -> list[FileValidationViolation]:
    """Check heading, table, length parity, TOC, LaTeX, and frontend standards."""
    violations: list[FileValidationViolation] = []
    manifest_path = docs_dir / "manifest" / "documents.json"
    if not manifest_path.exists():
        return violations

    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        for doc in manifest.get("documents", []):
            violations.extend(_manifest_doc_violations(docs_dir, doc))

        index_html = docs_dir / "index.html"
        if index_html.exists():
            violations.extend(
                _check_html_frontend_seo_a11y(
                    "index",
                    index_html,
                    index_html.read_text(encoding="utf-8", errors="ignore"),
                )
            )
    except MANIFEST_READ_ERRORS as exc:
        violations.append(
            _doc_violation(
                manifest_path,
                "doc_parity_audit_error",
                f"Failed auditing manifest: {exc}",
                line=0,
            )
        )

    return violations


DOCS_SUITE_TIMEOUT_S = 600
"""Upper bound for one run of the docs test suite."""

_UNITTEST_PROBLEM = re.compile(
    r"^(?P<kind>FAIL|ERROR): (?P<test>.+)$", re.MULTILINE
)


def docs_suite_command(docs_dir: Path) -> list[str] | None:
    """``unittest discover`` over ``tests/`` with docs venv, else env-manager's."""
    venv_python = get_venv_python_path(docs_dir)
    python = (
        str(venv_python) if venv_python.exists() else get_env_manager_python()
    )
    if not python:
        return None
    return [
        python,
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-p",
        "test_*.py",
    ]


def parse_unittest_output(
    output: str, tests_dir: Path
) -> list[FileValidationViolation]:
    """One violation per ``FAIL:`` or ``ERROR:`` header in unittest output."""
    return [
        FileValidationViolation(
            file_path=str(tests_dir),
            line_number=0,
            violation_type="doc_integrity_failure",
            message=f"[{m.group('kind')}] {m.group('test').strip()}",
        )
        for m in _UNITTEST_PROBLEM.finditer(output)
    ]


def audit_documentation_suite(docs_dir: Path) -> list[FileValidationViolation]:
    """Run the docs test suite in ``lemgendary-docs/tests`` as a subprocess."""
    tests_dir = docs_dir / "tests"
    if not tests_dir.exists():
        return []
    cmd = docs_suite_command(docs_dir)
    if cmd is None:
        return [
            tool_failure(
                "docs-suite", tests_dir, "no Python interpreter found"
            )
        ]
    try:
        proc = run_tool(cmd, timeout=DOCS_SUITE_TIMEOUT_S, cwd=docs_dir)
    except (OSError, subprocess.SubprocessError) as exc:
        return [tool_failure("docs-suite", tests_dir, str(exc))]
    found = parse_unittest_output(proc.stdout + proc.stderr, tests_dir)
    return found + unexplained_exit("docs-suite", tests_dir, proc, len(found))
