"""Full-repository documentation audit service backing ``lem-env docs-audit``."""

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from env_manager.ecosystem import ecosystem
from env_manager.line_length import DEFAULT_MD_LIMIT, GuardOptions, check_markdown_file
from env_manager.validation.gates.absolute_paths import scan_file_for_absolute_paths
from env_manager.validation.gates.source_files import scan_file_for_emojis
from env_manager.validation.models import FileValidationViolation, ProjectValidationReport
from env_manager.validation.tooling import regex_violations, resolve_tool_cmd, run_tool
from env_manager.validator import validate_project
from env_manager.workspace import workspace_root

_MD_LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
_STALE_PORT_RE = re.compile(r"\bport\s+8100\b|:8100\b|\(8100\)")
_LEGACY_DATASET_PORT = 8100


@dataclass
class DocsAuditSummary:
    """Aggregated result of full-repository documentation audit."""

    docs_report: ProjectValidationReport | None
    extra_md_files: list[str] = field(default_factory=list)
    extra_violations: list[FileValidationViolation] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Return True when both project validation and extra checks pass."""
        docs_ok = True if self.docs_report is None else self.docs_report.passed
        return docs_ok and not self.extra_violations

    def to_dict(self) -> dict[str, object]:
        """Serialize audit summary for JSON output."""
        return {
            "passed": self.passed,
            "docs_hub_passed": None if self.docs_report is None else self.docs_report.passed,
            "docs_hub_violations": (
                0 if self.docs_report is None else len(self.docs_report.failing_violations())
            ),
            "extra_md_files": self.extra_md_files,
            "extra_violations": [
                {
                    "file": v.file_path,
                    "line": v.line_number,
                    "type": v.violation_type,
                    "message": v.message,
                }
                for v in self.extra_violations
            ],
        }


def _discover_extra_markdown(root: Path) -> list[Path]:
    docs_dir = root / "lemgendary-docs"
    if not docs_dir.is_dir():
        return []
    return [md for md in sorted(docs_dir.glob("*.md")) if md.is_file()]


def _check_relative_links(md_path: Path) -> list[FileValidationViolation]:
    violations: list[FileValidationViolation] = []
    text = md_path.read_text(encoding="utf-8", errors="replace")
    in_code = False
    for line_no, line in enumerate(text.splitlines(), start=1):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        for target in _MD_LINK_RE.findall(line):
            clean = target.split("#", 1)[0].split("?", 1)[0].strip()
            if not clean or clean.startswith(("http://", "https://", "mailto:", "data:")):
                continue
            resolved = (md_path.parent / clean).resolve()
            if not resolved.exists():
                violations.append(
                    FileValidationViolation(
                        file_path=str(md_path),
                        line_number=line_no,
                        violation_type="broken_relative_link",
                        message=f"Broken relative target '{clean}'",
                    )
                )
    return violations


def _check_ecosystem_ssot(md_path: Path) -> list[FileValidationViolation]:
    ds_port = ecosystem().require_service("dataset-compiler").port
    if ds_port == _LEGACY_DATASET_PORT:
        return []
    violations: list[FileValidationViolation] = []
    text = md_path.read_text(encoding="utf-8", errors="replace")
    for line_no, line in enumerate(text.splitlines(), start=1):
        if _STALE_PORT_RE.search(line):
            violations.append(
                FileValidationViolation(
                    file_path=str(md_path),
                    line_number=line_no,
                    violation_type="ecosystem_ssot_drift",
                    message=f"Stale dataset-compiler port 8100 (expected {ds_port})",
                )
            )
    return violations


def _run_markdownlint_files(files: list[Path], cwd: Path) -> list[FileValidationViolation]:
    if not files:
        return []
    cmd = [*resolve_tool_cmd("markdownlint", "markdownlint"), *(str(f) for f in files)]
    try:
        proc = run_tool(cmd, cwd=cwd, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    output = proc.stderr or proc.stdout or ""
    return regex_violations(
        output,
        r"^(?P<path>.+?):(?P<line>\d+)(?::\d+)? (?P<rule>MD\d+/\S+) (?P<msg>.+)$",
        "md_",
    )


def run_docs_audit(root: Path | None = None) -> DocsAuditSummary:
    """Run full-repository documentation audit on ``lemgendary-docs`` and top-level MD."""
    ws = root or workspace_root()
    docs_dir = ws / "lemgendary-docs"
    docs_report = validate_project(docs_dir, is_node_project=False) if docs_dir.is_dir() else None

    extra_files = _discover_extra_markdown(ws)
    extra_violations: list[FileValidationViolation] = []

    for md_path in extra_files:
        extra_violations.extend(scan_file_for_emojis(md_path))
        extra_violations.extend(scan_file_for_absolute_paths(md_path))
        extra_violations.extend(_check_relative_links(md_path))
        extra_violations.extend(_check_ecosystem_ssot(md_path))

    if extra_files:
        opts = GuardOptions(md_limit=DEFAULT_MD_LIMIT, py_limit=100)
        for md_path in extra_files:
            for finding in check_markdown_file(md_path, opts):
                extra_violations.append(
                    FileValidationViolation(
                        file_path=str(finding.file_path),
                        line_number=finding.line_number,
                        violation_type="line_length",
                        message=f"Line length {finding.length} > {finding.limit}",
                    )
                )
        extra_violations.extend(_run_markdownlint_files(extra_files, ws))

    return DocsAuditSummary(
        docs_report=docs_report,
        extra_md_files=[str(p) for p in extra_files],
        extra_violations=extra_violations,
    )
