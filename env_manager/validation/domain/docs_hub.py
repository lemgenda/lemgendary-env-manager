"""Domain gates for the LemGendary AI Documentation Hub: paper sync, manifests and suite tests."""

import io
import json
import re
import unittest
from pathlib import Path
from typing import Any

import yaml

from env_manager.validation.models import MANIFEST_READ_ERRORS, FileValidationViolation

DOC_LENGTH_RATIO_MIN = 0.20


DOC_LENGTH_RATIO_MAX = 3.0
"""Allowed Markdown-to-HTML length ratio for a paper and its HTML companion."""


def _tokenize_text(text: str) -> list[str]:
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\$\$[\s\S]*?\$\$", " ", text)
    text = re.sub(r"\$[^\$]+?\$", " ", text)
    text = re.sub(r"[*_`#|>\-\+~=/\\]", " ", text)
    return [w.lower() for w in re.findall(r"\b[a-zA-Z0-9_]{2,}\b", text)]


def _extract_md_sections(md_text: str) -> dict[str, str]:
    md_text = re.sub(r"<!--.*?-->", "", md_text, flags=re.DOTALL)
    md_text = re.sub(r"```.*?```", "", md_text, flags=re.DOTALL)
    md_text = re.sub(r"!\[.*?\]\(.*?\)", "", md_text)
    md_text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", md_text)
    chunks = re.split(r"\n##\s+", md_text)
    sections: dict[str, str] = {}
    for c in chunks[1:]:
        lines = c.split("\n")
        title = lines[0].strip()
        if "table of contents" in title.lower():
            continue
        sections[title] = c
    return sections


def _extract_html_sections(soup: Any) -> dict[str, str]:
    sections: dict[str, str] = {}
    for s in soup.find_all("section"):
        h = s.find(["h2", "h1"])
        if h:
            title = h.get_text(separator=" ").strip()
            sections[title] = s.get_text(separator=" ")
    return sections


def audit_manifest_synchronization(docs_dir: Path) -> list[FileValidationViolation]:
    """Check that documents.json and both canonical_capabilities files agree."""
    violations: list[FileValidationViolation] = []
    doc_m_path = docs_dir / "manifest" / "documents.json"
    cap_j_path = docs_dir / "canonical_capabilities.json"
    cap_y_path = docs_dir / "canonical_capabilities.yaml"

    if not doc_m_path.exists():
        violations.append(
            FileValidationViolation(
                file_path=str(doc_m_path),
                line_number=1,
                violation_type="manifest_missing",
                message="documents.json manifest missing from lemgendary-docs/manifest/",
            )
        )
    if not cap_j_path.exists():
        violations.append(
            FileValidationViolation(
                file_path=str(cap_j_path),
                line_number=1,
                violation_type="manifest_missing",
                message="canonical_capabilities.json missing from lemgendary-docs/",
            )
        )
    if not cap_y_path.exists():
        violations.append(
            FileValidationViolation(
                file_path=str(cap_y_path),
                line_number=1,
                violation_type="manifest_missing",
                message="canonical_capabilities.yaml missing from lemgendary-docs/",
            )
        )

    if doc_m_path.exists() and cap_j_path.exists() and cap_y_path.exists():
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
                    FileValidationViolation(
                        file_path=str(cap_j_path),
                        line_number=1,
                        violation_type="manifest_baseline_mismatch",
                        message=(
                            f"Document baseline desynchronization: documents.json='{d_base}', "
                            f"canonical_capabilities.json='{c_base}', "
                            f"canonical_capabilities.yaml='{y_base}'"
                        ),
                    )
                )

            total_docs = len(dj.get("documents", []))
            if dj.get("total_publications") != total_docs:
                violations.append(
                    FileValidationViolation(
                        file_path=str(doc_m_path),
                        line_number=1,
                        violation_type="manifest_count_mismatch",
                        message=(
                            f"Total publications count mismatch in documents.json: "
                            f"header={dj.get('total_publications')} vs item_count={total_docs}"
                        ),
                    )
                )

            # JSON and YAML capabilities bijective parity
            if cj != cy:
                violations.append(
                    FileValidationViolation(
                        file_path=str(cap_y_path),
                        line_number=1,
                        violation_type="manifest_content_drift",
                        message=(
                            "canonical_capabilities.yaml is not strictly identical in structure "
                            "to canonical_capabilities.json"
                        ),
                    )
                )
        except MANIFEST_READ_ERRORS as exc:
            violations.append(
                FileValidationViolation(
                    file_path=str(doc_m_path),
                    line_number=0,
                    violation_type="manifest_parse_error",
                    message=f"Failed parsing capabilities manifests: {exc}",
                )
            )

    return violations


_SKIPPED_H2_TITLES = ("contents", "table of contents", "navigation matrix")


def _content_h2_titles(raw_titles: list[str]) -> list[str]:
    """Drop navigation headings (contents, category indexes) from a list of H2 titles."""
    return [
        x for x in raw_titles
        if x.lower() not in _SKIPPED_H2_TITLES and not x.lower().startswith("category")
    ]


def _doc_violation(path: Path, kind: str, message: str) -> FileValidationViolation:
    return FileValidationViolation(
        file_path=str(path), line_number=1, violation_type=kind, message=message,
    )


def _paper_parity_violations(
    doc_id: str, hp: Path, h_raw: str, m_raw: str,
) -> list[FileValidationViolation]:
    """Compare H2 count, table count and length ratio of one HTML/Markdown paper pair."""
    violations: list[FileValidationViolation] = []
    h2_h = _content_h2_titles([
        re.sub(r"<[^>]+>", "", x).strip()
        for x in re.findall(r"<h2[^>]*>(.*?)</h2>", h_raw, re.I)
    ])
    h2_m = _content_h2_titles([
        re.sub(r"[*_`]", "", x).strip()
        for x in re.findall(r"^##\s+(.+)$", m_raw, re.M)
    ])
    if len(h2_h) != len(h2_m):
        violations.append(_doc_violation(
            hp, "doc_heading_mismatch",
            f"[{doc_id}] H2 count mismatch: HTML({len(h2_h)}) vs MD({len(h2_m)})",
        ))

    h_tables = len(re.findall(r"<table", h_raw, re.I))
    m_tables = len(re.findall(r"\|(?:\s*:?-+:?\s*\|)+", m_raw))
    if abs(h_tables - m_tables) > 1:
        violations.append(_doc_violation(
            hp, "doc_table_mismatch",
            f"[{doc_id}] Table count difference exceeds threshold: "
            f"HTML={h_tables} vs MD={m_tables}",
        ))

    ratio = len(m_raw) / len(h_raw) if len(h_raw) > 0 else 0
    if not DOC_LENGTH_RATIO_MIN <= ratio <= DOC_LENGTH_RATIO_MAX:
        violations.append(_doc_violation(
            hp, "doc_ratio_anomaly",
            f"[{doc_id}] Length ratio anomaly: ratio={ratio:.2f} "
            f"(HTML={len(h_raw)}, MD={len(m_raw)})",
        ))
    return violations


def _manifest_doc_violations(docs_dir: Path, doc: dict[str, Any]) -> list[FileValidationViolation]:
    """Check that one manifest entry has both papers and that they stay in parity."""
    doc_id = doc.get("id", "unknown")
    hp = docs_dir / doc.get("html_path", "")
    mp = docs_dir / doc.get("md_path", "")
    if not hp.exists():
        return [_doc_violation(
            hp, "doc_html_missing",
            f"Companion HTML publication missing for [{doc_id}]: {doc.get('html_path')}",
        )]
    if not mp.exists():
        return [_doc_violation(
            mp, "doc_md_missing", f"Markdown source missing for [{doc_id}]: {doc.get('md_path')}",
        )]
    return _paper_parity_violations(
        doc_id,
        hp,
        hp.read_text(encoding="utf-8", errors="ignore"),
        mp.read_text(encoding="utf-8", errors="ignore"),
    )


def audit_docs_synchronization(docs_dir: Path) -> list[FileValidationViolation]:
    """Check heading, table and length parity between each Markdown paper and its HTML twin."""
    violations: list[FileValidationViolation] = []
    manifest_path = docs_dir / "manifest" / "documents.json"
    if not manifest_path.exists():
        return violations

    try:
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
        for doc in manifest.get("documents", []):
            violations.extend(_manifest_doc_violations(docs_dir, doc))
    except MANIFEST_READ_ERRORS as exc:
        violations.append(
            FileValidationViolation(
                file_path=str(manifest_path),
                line_number=0,
                violation_type="doc_parity_audit_error",
                message=f"Failed auditing 48-doc manifest parity: {exc}",
            )
        )

    return violations


def audit_documentation_suite(docs_dir: Path) -> list[FileValidationViolation]:
    """Execute the comprehensive 20-rule documentation test suite in lemgendary-docs/tests."""
    violations: list[FileValidationViolation] = []
    tests_dir = docs_dir / "tests"
    if not tests_dir.exists():
        return violations

    try:
        loader = unittest.TestLoader()
        suite = loader.discover(start_dir=str(tests_dir), pattern="test_*.py")
        runner = unittest.TextTestRunner(stream=io.StringIO(), verbosity=1)
        result = runner.run(suite)

        for test, trace in result.failures + result.errors:
            t_id = test.id() if hasattr(test, "id") else str(test)
            err_line = trace.strip().splitlines()[-1] if trace.strip() else "Test failed"
            violations.append(
                FileValidationViolation(
                    file_path=str(tests_dir / "test_documentation.py"),
                    line_number=1,
                    violation_type="doc_integrity_failure",
                    message=f"[{t_id}] {err_line}",
                )
            )
    except (ImportError, OSError) as exc:
        violations.append(
            FileValidationViolation(
                file_path=str(tests_dir),
                line_number=0,
                violation_type="doc_test_execution_error",
                message=f"Failed executing documentation test suite: {exc}",
            )
        )
    return violations
