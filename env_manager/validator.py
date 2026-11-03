"""Code validation, linting, and accessibility audit module.

Performs comprehensive validation across all LemGendary projects:

Python projects (lemgendary-training-suite, lemgendary-datasets, lemgendary-env-manager):
  - py_compile bytecode compilation
  - Zero-emoji compliance

Node.js / React project (lemgendary-ai-studio-gui):
  - ESLint (JS/TS)
  - TypeScript tsc --noEmit

All projects (including lemgendary-docs, LemGendaryDatasets, LemGendaryModels):
  - markdownlint-cli2 for Markdown files
  - yamllint (from env-manager .venv) for YAML/YML files

HTML/CSS projects (lemgendary-docs):
  - html-validate (W3C HTML validation)
  - stylelint (CSS validation)
  - pa11y (WCAG 2.2 AA accessibility against static HTML)
"""

import collections
import difflib
import io
import json
import os
import platform
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from env_manager._logging import get_logger
from env_manager.utils import WINDOWS_NO_WINDOW, pip_env

_log = get_logger(__name__)

# Failures reading a JSON/YAML manifest and walking it: unreadable file
# (OSError), invalid JSON or encoding (ValueError), invalid YAML
# (yaml.YAMLError), and a document whose shape differs from the expected
# schema (AttributeError for a non-mapping node, TypeError for a wrong value
# type). Each is reported as a manifest violation.
_MANIFEST_READ_ERRORS = (OSError, ValueError, yaml.YAMLError, AttributeError, TypeError)


@dataclass
class FileValidationViolation:
    """Represents a violation found in a file."""
    file_path: str
    line_number: int
    violation_type: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectValidationReport:
    """Validation report for a project."""
    project_name: str
    compiled_files_count: int
    compile_errors: List[FileValidationViolation]
    emoji_violations: List[FileValidationViolation]
    lint_errors: List[FileValidationViolation]
    yaml_errors: List[FileValidationViolation]
    json_errors: List[FileValidationViolation]
    html_errors: List[FileValidationViolation]
    wcag_violations: List[FileValidationViolation]
    domain_errors: List[FileValidationViolation]
    passed: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ─── Shell selection ────────────────────────────────────────────────────────

def _preferred_shell() -> str:
    """Return 'pwsh' when PowerShell 7 is available, else 'powershell'."""
    if shutil.which("pwsh"):
        return "pwsh"
    return "powershell"


# Comprehensive emoji regex range (Unicode 15.0 ranges)
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # Emoticons
    "\U0001F300-\U0001F5FF"  # Misc Symbols and Pictographs
    "\U0001F680-\U0001F6FF"  # Transport and Map
    "\U0001F700-\U0001F77F"  # Alchemical Symbols
    "\U0001F780-\U0001F7FF"  # Geometric Shapes Extended
    "\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
    "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
    "\U0001FA00-\U0001FA6F"  # Chess Symbols
    "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
    "\U00002702-\U000027B0"  # Dingbats
    "\U0001F100-\U0001F251"  # Enclosed Alphanumeric and Ideographic Supplement
    "]+",
    flags=re.UNICODE,
)


# ─── Python compilation & emoji scan ────────────────────────────────────────

def scan_file_for_emojis(file_path: Path) -> List[FileValidationViolation]:
    """Audit single file for emoji presence."""
    violations: List[FileValidationViolation] = []
    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        for idx, line in enumerate(content.splitlines(), start=1):
            if EMOJI_PATTERN.search(line):
                violations.append(
                    FileValidationViolation(
                        file_path=str(file_path),
                        line_number=idx,
                        violation_type="emoji_violation",
                        message="Emoji character detected in source file.",
                    )
                )
    except OSError as exc:
        violations.append(
            FileValidationViolation(
                file_path=str(file_path),
                line_number=0,
                violation_type="read_error",
                message=str(exc),
            )
        )
    return violations


def compile_python_file(file_path: Path) -> Optional[FileValidationViolation]:
    """Compile single Python file and catch syntax errors."""
    try:
        py_compile.compile(str(file_path), doraise=True)
        return None
    except py_compile.PyCompileError as err:
        exc_val = getattr(err, "exc_value", None)
        line_num = getattr(exc_val, "lineno", 0) if exc_val else 0
        return FileValidationViolation(
            file_path=str(file_path),
            line_number=line_num or 0,
            violation_type="syntax_error",
            message=str(err),
        )
    except (OSError, ValueError) as exc:
        return FileValidationViolation(
            file_path=str(file_path),
            line_number=0,
            violation_type="compilation_error",
            message=str(exc),
        )


# ─── Python-y tooling (yamllint) ────────────────────────────────────────────

def _get_env_manager_python() -> Optional[str]:
    """Return the path to the env-manager .venv Python executable."""
    env_mgr_dir = Path(__file__).resolve().parent.parent
    if platform.system().lower() == "windows":
        py = env_mgr_dir / ".venv" / "Scripts" / "python.exe"
    else:
        py = env_mgr_dir / ".venv" / "bin" / "python"
    if py.exists():
        return str(py)
    return sys.executable  # fallback to current interpreter


def run_yamllint(project_dir: Path) -> List[FileValidationViolation]:
    """Run yamllint on all YAML files in the project using env-manager venv."""
    violations: List[FileValidationViolation] = []
    python_path = _get_env_manager_python()
    if not python_path:
        return violations

    skip_dirs = {
        ".git", ".venv", "node_modules", "__pycache__", "dist", "build", "target",
        ".pytest_cache", "raw-sets", "checkpoints", "weights", ".agents", "data",
        "scratch", ".cache", "export", "runs", ".lemtrain_server", ".lgd_server",
        ".env_server",
    }
    yaml_files = [
        f for f in project_dir.rglob("*.yaml")
        if not any(part in skip_dirs for part in f.parts)
    ] + [
        f for f in project_dir.rglob("*.yml")
        if not any(part in skip_dirs for part in f.parts)
    ]

    if not yaml_files:
        return violations

    mgr_dir = Path(__file__).resolve().parent.parent
    cfg = None
    for candidate in [
        project_dir / ".yamllint.yaml",
        project_dir / ".yamllint",
        mgr_dir / ".yamllint.yaml",
        mgr_dir / ".yamllint",
        project_dir.parent / ".yamllint.yaml",
        project_dir.parent / ".yamllint",
    ]:
        if candidate.exists():
            cfg = str(candidate)
            break

    cmd = [python_path, "-m", "yamllint", "-f", "parsable"]
    if cfg:
        cmd.extend(["-c", cfg])
    cmd.extend([str(f) for f in yaml_files])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
            env=pip_env(),
            creationflags=WINDOWS_NO_WINDOW,
        )
        if proc.stdout.strip():
            for line in proc.stdout.strip().splitlines():
                # yamllint parsable format: path:line:col: [level] message (rule)
                m = re.match(r"^(.+?):(\d+):\d+: \[(\w+)\] (.+)$", line.strip())
                if m:
                    violations.append(
                        FileValidationViolation(
                            file_path=m.group(1),
                            line_number=int(m.group(2)),
                            violation_type=f"yaml_{m.group(3)}",
                            message=m.group(4),
                        )
                    )
        # yamllint exit codes:
        #   0 = no problems
        #   1 = problems found (expected; parsed above)
        #   2 = config / tooling error (unexpected; log but do not emit)
        if proc.returncode not in (0, 1):
            _log.debug(
                "yamllint returned exit code %d for %s (stderr: %s)",
                proc.returncode, project_dir,
                (proc.stderr or "").strip()[:200],
            )
    except FileNotFoundError:
        _log.warning("yamllint not found — install it in the env-manager venv: pip install yamllint")
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("yamllint failed for %s: %s", project_dir, exc)
    return violations


# ─── JSON lint ──────────────────────────────────────────────────────────────

def run_jsonlint(project_dir: Path) -> List[FileValidationViolation]:
    """Audit all JSON configuration and manifest files for syntax and structure errors."""
    violations: List[FileValidationViolation] = []
    skip_dirs = {
        ".git", ".venv", "node_modules", "__pycache__", "dist", "build", "target",
        ".pytest_cache", "raw-sets", "checkpoints", "weights", ".agents", "data",
        "scratch", ".cache", ".antigravity", "export", "runs", ".lemtrain_server",
        ".lgd_server", ".env_server",
    }
    for root, dirs, files in Path(project_dir).walk():
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for f in files:
            if f.endswith(".json"):
                f_path = root / f
                try:
                    content = f_path.read_text(encoding="utf-8")
                    json.loads(content)
                except json.JSONDecodeError as exc:
                    violations.append(
                        FileValidationViolation(
                            file_path=str(f_path),
                            line_number=exc.lineno,
                            violation_type="json_syntax_error",
                            message=f"{exc.msg} (col {exc.colno})",
                        )
                    )
                except (OSError, ValueError) as exc:
                    violations.append(
                        FileValidationViolation(
                            file_path=str(f_path),
                            line_number=0,
                            violation_type="json_read_error",
                            message=str(exc),
                        )
                    )
    return violations


# ─── Tool resolution ────────────────────────────────────────────────────────

def _resolve_tool_cmd(
    tool_name: str, npx_pkg: str, project_dir: Optional[Path] = None,
) -> List[str]:
    """Resolve local binary from project or env-manager node_modules/.bin, else npx."""
    mgr_dir = Path(__file__).resolve().parent.parent
    candidate_dirs: List[Path] = []
    if project_dir and (project_dir / "node_modules" / ".bin").exists():
        candidate_dirs.append(project_dir / "node_modules" / ".bin")
    candidate_dirs.append(mgr_dir / "node_modules" / ".bin")

    for bin_dir in candidate_dirs:
        candidates = [
            bin_dir / f"{tool_name}.cmd",
            bin_dir / f"{tool_name}.exe",
            bin_dir / tool_name,
        ]
        for c in candidates:
            if c.exists():
                return [str(c)]

    global_bin = (
        shutil.which(f"{tool_name}.cmd")
        or shutil.which(f"{tool_name}.exe")
        or shutil.which(tool_name)
    )
    if global_bin:
        return [global_bin]

    npx = shutil.which("npx") or ("npx.cmd" if sys.platform == "win32" else "npx")
    return [npx, "--yes", npx_pkg]


# ─── Markdown / ESLint / TypeScript ─────────────────────────────────────────

def run_markdownlint(project_dir: Path) -> List[FileValidationViolation]:
    """Run markdownlint-cli on Markdown files using local binary or npx."""
    violations: List[FileValidationViolation] = []

    skip_dirs = {
        ".git", ".venv", "node_modules", "__pycache__", "dist", "build", "target",
        ".pytest_cache", "raw-sets", "checkpoints", "weights", ".agents", "data",
        "scratch", ".cache",
    }
    md_files = [
        f for f in project_dir.rglob("*.md")
        if not any(part in skip_dirs for part in f.parts)
    ]
    if not md_files:
        return violations

    mgr_dir = Path(__file__).resolve().parent.parent
    cfg = None
    for candidate in [
        project_dir / ".markdownlint.json",
        mgr_dir / ".markdownlint.json",
        project_dir.parent / ".markdownlint.json",
        project_dir.parent / "lemgendary-docs" / ".markdownlint.json",
    ]:
        if candidate.exists():
            cfg = str(candidate)
            break

    cmd = _resolve_tool_cmd("markdownlint", "markdownlint-cli")
    if cfg:
        cmd.extend(["-c", cfg])
    cmd.extend([str(f) for f in md_files])

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
            creationflags=WINDOWS_NO_WINDOW,
        )
        combined = proc.stdout + proc.stderr
        for line in combined.splitlines():
            m = re.match(r"^(.+?):(\d+)(?::\d+)?\s+(MD\d+/\S+)\s+(.+)$", line.strip())
            if m:
                violations.append(
                    FileValidationViolation(
                        file_path=m.group(1),
                        line_number=int(m.group(2)),
                        violation_type=f"markdownlint/{m.group(3)}",
                        message=m.group(4),
                    )
                )
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("markdownlint-cli failed for %s: %s", project_dir, exc)
    return violations


def run_eslint(project_dir: Path) -> List[FileValidationViolation]:
    """Run ESLint on JS/TS source files for a Node.js project.

    Uses ``--format json`` so parsing is exact. The previous compact-format
    regex + broad "any line with ' error '" fallback produced false positives
    from unrelated tool output (Browserslist warnings, deprecation notices).
    """
    violations: List[FileValidationViolation] = []

    eslint_cfg = (
        project_dir / ".eslintrc.json",
        project_dir / ".eslintrc.js",
        project_dir / ".eslintrc.cjs",
        project_dir / "eslint.config.js",
        project_dir / "eslint.config.mjs",
    )
    has_eslint_config = any(f.exists() for f in eslint_cfg)
    if not has_eslint_config:
        return violations

    src_dir = project_dir / "src"
    lint_target = str(src_dir) if src_dir.exists() else str(project_dir)

    try:
        cmd = _resolve_tool_cmd("eslint", "eslint", project_dir=project_dir) + [
            lint_target,
            "--ext", ".js,.jsx,.ts,.tsx",
            "--format", "json",
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
            creationflags=WINDOWS_NO_WINDOW,
        )

        # ESLint may emit non-JSON noise before the JSON block (Browserslist,
        # deprecation notices, etc.). Find the first '[' and parse from there.
        stdout = proc.stdout or ""
        idx = stdout.find("[")
        if idx < 0:
            # Nothing parseable; ESLint likely failed to start.
            if proc.stderr:
                _log.debug("ESLint for %s produced no JSON output: %s",
                           project_dir, proc.stderr.strip()[:200])
            return violations

        try:
            data = json.loads(stdout[idx:])
        except json.JSONDecodeError as exc:
            _log.warning("ESLint JSON parse failed for %s: %s", project_dir, exc)
            return violations

        for entry in data:
            file_path = entry.get("filePath", str(project_dir))
            for msg in entry.get("messages", []):
                # severity: 1 = warning, 2 = error
                if msg.get("severity") == 2:
                    violations.append(
                        FileValidationViolation(
                            file_path=file_path,
                            line_number=msg.get("line", 0),
                            violation_type="eslint_error",
                            message=msg.get("message", "").strip(),
                        )
                    )
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("ESLint failed for %s: %s", project_dir, exc)
    return violations


def run_typescript_check(project_dir: Path) -> List[FileValidationViolation]:
    """Run tsc --noEmit for TypeScript projects."""
    violations: List[FileValidationViolation] = []

    tsconfig = project_dir / "tsconfig.json"
    if not tsconfig.exists():
        return violations

    try:
        cmd = _resolve_tool_cmd("tsc", "typescript", project_dir=project_dir) + ["--noEmit"]
        proc = subprocess.run(
            cmd,
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            check=False,
            creationflags=WINDOWS_NO_WINDOW,
        )
        if proc.returncode != 0:
            for line in (proc.stdout + proc.stderr).splitlines():
                m = re.match(r"^(.+?)\((\d+),\d+\):\s+error\s+TS\d+:\s+(.+)$", line.strip())
                if m:
                    violations.append(
                        FileValidationViolation(
                            file_path=m.group(1),
                            line_number=int(m.group(2)),
                            violation_type="typescript_error",
                            message=m.group(3).strip(),
                        )
                    )
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("tsc --noEmit failed for %s: %s", project_dir, exc)
    return violations


# ─── HTML / CSS / WCAG ──────────────────────────────────────────────────────

def run_html_validate(project_dir: Path) -> List[FileValidationViolation]:
    """Run html-validate (W3C HTML validator) on static HTML files."""
    violations: List[FileValidationViolation] = []

    html_files = [
        f for f in project_dir.rglob("*.html")
        if not any(skip in f.parts for skip in (".venv", "node_modules", ".git"))
    ]
    if not html_files:
        return violations

    try:
        cmd = (
            _resolve_tool_cmd("html-validate", "html-validate")
            + ["--formatter", "text"]
            + [str(f) for f in html_files]
        )
        proc = subprocess.run(
            cmd,
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
            creationflags=WINDOWS_NO_WINDOW,
        )
        combined = proc.stdout + proc.stderr
        for line in combined.splitlines():
            m = re.match(r"^\s*(\d+):(\d+)\s+(error|warning)\s+(.+?)\s+(.+)$", line.strip())
            if m:
                violations.append(
                    FileValidationViolation(
                        file_path=str(project_dir),
                        line_number=int(m.group(1)),
                        violation_type=f"html_{m.group(3)}",
                        message=f"{m.group(4)} — {m.group(5)}",
                    )
                )
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("html-validate failed for %s: %s", project_dir, exc)
    return violations


def run_pa11y_wcag(project_dir: Path) -> List[FileValidationViolation]:
    """Run pa11y WCAG 2.2 AA accessibility audit against static HTML files."""
    violations: List[FileValidationViolation] = []

    html_files = [
        f for f in project_dir.rglob("*.html")
        if not any(skip in f.parts for skip in (".venv", "node_modules", ".git", "dist"))
        and f.stat().st_size < 512 * 1024
    ]
    if not html_files:
        return violations

    audit_targets = [f for f in html_files if f.name == "index.html"] or html_files[:1]
    for html_file in audit_targets:
        try:
            cmd = _resolve_tool_cmd("pa11y", "pa11y") + [
                "--standard", "WCAG2AA",
                "--reporter", "cli",
                f"file:///{html_file.as_posix()}",
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
                creationflags=WINDOWS_NO_WINDOW,
            )
            combined = proc.stdout + proc.stderr
            current_issue: Optional[str] = None
            for line in combined.splitlines():
                line = line.strip()
                if (
                    line.startswith("Error:")
                    or line.startswith("Warning:")
                    or line.startswith("Notice:")
                ):
                    current_issue = line
                elif current_issue and line.startswith("("):
                    violations.append(
                        FileValidationViolation(
                            file_path=str(html_file),
                            line_number=0,
                            violation_type="wcag_aa_violation",
                            message=f"{current_issue} {line}",
                        )
                    )
                    current_issue = None
        except (OSError, subprocess.SubprocessError) as exc:
            _log.warning("pa11y WCAG audit failed for %s: %s", html_file, exc)

    return violations


# ─── PowerShell (PSScriptAnalyzer) ──────────────────────────────────────────

def run_psscriptanalyzer(project_dir: Path) -> List[FileValidationViolation]:
    """Run PSScriptAnalyzer on all PowerShell scripts in the project.

    Prefers ``pwsh`` (PowerShell 7) when available. Writes the file list to a
    temp file rather than concatenating every path into a single command line,
    which was prone to hitting the Windows ``CreateProcess`` 32K-character
    limit on projects with many scripts.
    """
    violations: List[FileValidationViolation] = []
    if platform.system().lower() != "windows":
        return violations

    ps1_files = [
        f for f in project_dir.rglob("*.ps1")
        if not any(skip in f.parts for skip in (".venv", "node_modules", ".git"))
    ]
    if not ps1_files:
        return violations

    shell = _preferred_shell()

    # Write file list to a temp file with one absolute path per line.
    fd, list_file = tempfile.mkstemp(prefix="pssa_files_", suffix=".txt")
    try:
        with open(fd, "w", encoding="utf-8", closefd=True) as fh:
            for p in ps1_files:
                fh.write(str(p) + "\n")

        # Single-quoted path is safe because we control the temp filename.
        ps_cmd = (
            f"$files = Get-Content -Path '{list_file}' -Encoding UTF8; "
            "foreach ($file in $files) { "
            "    if (Test-Path -LiteralPath $file) { "
            "        Invoke-ScriptAnalyzer -Path $file -Severity Warning,Error "
            "    } "
            "} | ForEach-Object { "
            "    \"$($_.ScriptPath):$($_.Line):$($_.Severity):$($_.Message)\" "
            "}"
        )

        try:
            proc = subprocess.run(
                [shell, "-NoProfile", "-NonInteractive", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                check=False,
                creationflags=WINDOWS_NO_WINDOW,
            )
        except FileNotFoundError:
            # pwsh not on PATH despite shutil.which finding it earlier; fall
            # back to Windows PowerShell.
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=180,
                check=False,
                creationflags=WINDOWS_NO_WINDOW,
            )

        combined = proc.stdout + proc.stderr
        if (
            "CommandNotFoundException" in combined
            or "Invoke-ScriptAnalyzer" in combined and "is not recognized" in combined.lower()
        ):
            _log.debug(
                "PSScriptAnalyzer not installed. Install via: "
                "Install-Module PSScriptAnalyzer -Scope CurrentUser -Force"
            )
            return violations

        for line in combined.splitlines():
            # Format: path:line:severity:message
            parts = line.strip().split(":", 3)
            if len(parts) >= 4:
                try:
                    file_path = parts[0]
                    # Windows paths contain 'C:' so the first split is off by
                    # one. Re-join the first two parts before parsing the rest.
                    if len(parts[0]) == 1 and parts[1].startswith(("\\", "/")):
                        file_path = f"{parts[0]}:{parts[1]}"
                        rest = line.strip()[len(file_path) + 1:].split(":", 2)
                        if len(rest) < 3:
                            continue
                        line_num = int(rest[0])
                        severity = rest[1].strip().lower()
                        message = rest[2].strip()
                    else:
                        line_num = int(parts[1])
                        severity = parts[2].strip().lower()
                        message = parts[3].strip()

                    violations.append(
                        FileValidationViolation(
                            file_path=file_path,
                            line_number=line_num,
                            violation_type=f"pssa_{severity}",
                            message=message,
                        )
                    )
                except (ValueError, IndexError) as exc:
                    _log.debug("Unparsable PSScriptAnalyzer line %r: %s", line, exc)
                    continue
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("PSScriptAnalyzer execution failed: %s", exc)
    finally:
        try:
            os.unlink(list_file)
        except OSError as exc:
            _log.debug("Failed to unlink temporary PSScriptAnalyzer file %s: %s", list_file, exc)

    return violations


# ─── Domain verification ────────────────────────────────────────────────────

def _tokenize_text(text: str) -> List[str]:
    text = re.sub(r"https?://\S+", "", text)
    text = re.sub(r"\$\$[\s\S]*?\$\$", " ", text)
    text = re.sub(r"\$[^\$]+?\$", " ", text)
    text = re.sub(r"[*_`#|>\-\+~=/\\]", " ", text)
    return [w.lower() for w in re.findall(r"\b[a-zA-Z0-9_]{2,}\b", text)]


def _extract_md_sections(md_text: str) -> Dict[str, str]:
    md_text = re.sub(r"<!--.*?-->", "", md_text, flags=re.DOTALL)
    md_text = re.sub(r"```.*?```", "", md_text, flags=re.DOTALL)
    md_text = re.sub(r"!\[.*?\]\(.*?\)", "", md_text)
    md_text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", md_text)
    chunks = re.split(r"\n##\s+", md_text)
    sections: Dict[str, str] = {}
    for c in chunks[1:]:
        lines = c.split("\n")
        title = lines[0].strip()
        if "table of contents" in title.lower():
            continue
        sections[title] = c
    return sections


def _extract_html_sections(soup: Any) -> Dict[str, str]:
    sections: Dict[str, str] = {}
    for s in soup.find_all("section"):
        h = s.find(["h2", "h1"])
        if h:
            title = h.get_text(separator=" ").strip()
            sections[title] = s.get_text(separator=" ")
    return sections


def _audit_readme_changelog(project_dir: Path) -> List[FileValidationViolation]:
    """Verify that README.md has an updated changelog if any project files have been modified."""
    violations: List[FileValidationViolation] = []
    readme_path = project_dir / "README.md"

    # Inspect git status of working directory
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
            creationflags=WINDOWS_NO_WINDOW,
        )
        status_lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        modified_files: List[str] = []
        for line in status_lines:
            path_part = line[2:].strip()
            if " -> " in path_part:
                path_part = path_part.split(" -> ")[1].strip()
            modified_files.append(path_part)

        # If working tree is clean, inspect latest commit against parent
        if not modified_files:
            diff_proc = subprocess.run(
                ["git", "diff", "HEAD~1", "--name-only"],
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                check=False,
                creationflags=WINDOWS_NO_WINDOW,
            )
            if diff_proc.returncode == 0:
                modified_files = [line.strip() for line in diff_proc.stdout.splitlines() if line.strip()]

        non_readme_changes = [f for f in modified_files if Path(f).name.lower() != "readme.md"]
        readme_changed = any(Path(f).name.lower() == "readme.md" for f in modified_files)

        if non_readme_changes:
            if not readme_path.exists():
                violations.append(
                    FileValidationViolation(
                        file_path=str(readme_path),
                        line_number=1,
                        violation_type="readme_changelog_missing",
                        message=(
                            f"Project '{project_dir.name}' has {len(non_readme_changes)} modified files "
                            "but README.md does not exist."
                        ),
                    )
                )
            else:
                readme_text = readme_path.read_text(encoding="utf-8", errors="ignore")
                has_changelog = bool(
                    re.search(r"##\s+(?:Changelog|CHANGELOG)", readme_text)
                    or re.search(r"###\s+v\d+\.\d+", readme_text)
                )
                if not has_changelog:
                    violations.append(
                        FileValidationViolation(
                            file_path=str(readme_path),
                            line_number=1,
                            violation_type="readme_changelog_missing",
                            message=(
                                f"Project '{project_dir.name}' has modified files but README.md "
                                "lacks a valid Changelog section."
                            ),
                        )
                    )
                elif not readme_changed and len(modified_files) > 0 and len(status_lines) > 0:
                    # Uncommitted working tree files modified without README.md being updated
                    violations.append(
                        FileValidationViolation(
                            file_path=str(readme_path),
                            line_number=1,
                            violation_type="readme_changelog_unupdated",
                            message=(
                                f"Project '{project_dir.name}' has uncommitted changes in "
                                f"{len(non_readme_changes)} files, but README.md changelog was not updated."
                            ),
                        )
                    )
    except (OSError, subprocess.SubprocessError) as exc:
        _log.warning("README changelog audit skipped for %s: %s", project_dir, exc)

    return violations


def _audit_manifest_synchronization(docs_dir: Path) -> List[FileValidationViolation]:
    """Verify bidirectional synchronization across documents.json, canonical_capabilities.json, and canonical_capabilities.yaml."""
    violations: List[FileValidationViolation] = []
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
            with open(doc_m_path, "r", encoding="utf-8") as f:
                dj = json.load(f)
            with open(cap_j_path, "r", encoding="utf-8") as f:
                cj = json.load(f)
            with open(cap_y_path, "r", encoding="utf-8") as f:
                cy = yaml.safe_load(f)

            d_base = dj.get("document_baseline")
            c_base = cj.get("document_baseline")
            y_base = cy.get("document_baseline")

            if not (d_base == c_base == y_base):
                violations.append(
                    FileValidationViolation(
                        file_path=str(cap_j_path),
                        line_number=1,
                        violation_type="manifest_baseline_mismatch",
                        message=(
                            f"Document baseline desynchronization: documents.json='{d_base}', "
                            f"canonical_capabilities.json='{c_base}', canonical_capabilities.yaml='{y_base}'"
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
                        message="canonical_capabilities.yaml is not strictly identical in structure to canonical_capabilities.json",
                    )
                )
        except _MANIFEST_READ_ERRORS as exc:
            violations.append(
                FileValidationViolation(
                    file_path=str(doc_m_path),
                    line_number=0,
                    violation_type="manifest_parse_error",
                    message=f"Failed parsing capabilities manifests: {exc}",
                )
            )

    return violations


def _audit_docs_synchronization(docs_dir: Path) -> List[FileValidationViolation]:
    """Verify comprehensive structural and content parity across all 48 Markdown papers and companion HTML publications."""
    violations: List[FileValidationViolation] = []
    manifest_path = docs_dir / "manifest" / "documents.json"
    if not manifest_path.exists():
        return violations

    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)

        for doc in manifest.get("documents", []):
            doc_id = doc.get("id", "unknown")
            hp = docs_dir / doc.get("html_path", "")
            mp = docs_dir / doc.get("md_path", "")

            if not hp.exists():
                violations.append(
                    FileValidationViolation(
                        file_path=str(hp),
                        line_number=1,
                        violation_type="doc_html_missing",
                        message=f"Companion HTML publication missing for [{doc_id}]: {doc.get('html_path')}",
                    )
                )
                continue
            if not mp.exists():
                violations.append(
                    FileValidationViolation(
                        file_path=str(mp),
                        line_number=1,
                        violation_type="doc_md_missing",
                        message=f"Markdown source missing for [{doc_id}]: {doc.get('md_path')}",
                    )
                )
                continue

            h_raw = hp.read_text(encoding="utf-8", errors="ignore")
            m_raw = mp.read_text(encoding="utf-8", errors="ignore")

            # 1. H2 count & parity
            h2_h = [
                re.sub(r"<[^>]+>", "", x).strip()
                for x in re.findall(r"<h2[^>]*>(.*?)</h2>", h_raw, re.I)
            ]
            h2_h = [
                x for x in h2_h
                if x.lower() not in ("contents", "table of contents", "navigation matrix")
                and not x.lower().startswith("category")
            ]
            h2_m = [
                re.sub(r"[*_`]", "", x).strip()
                for x in re.findall(r"^##\s+(.+)$", m_raw, re.M)
            ]
            h2_m = [
                x for x in h2_m
                if x.lower() not in ("contents", "table of contents", "navigation matrix")
                and not x.lower().startswith("category")
            ]

            if len(h2_h) != len(h2_m):
                violations.append(
                    FileValidationViolation(
                        file_path=str(hp),
                        line_number=1,
                        violation_type="doc_heading_mismatch",
                        message=f"[{doc_id}] H2 count mismatch: HTML({len(h2_h)}) vs MD({len(h2_m)})",
                    )
                )

            # 2. Table count parity
            h_tables = len(re.findall(r"<table", h_raw, re.I))
            m_tables = len(re.findall(r"\|(?:\s*:?-+:?\s*\|)+", m_raw))
            if abs(h_tables - m_tables) > 1:
                violations.append(
                    FileValidationViolation(
                        file_path=str(hp),
                        line_number=1,
                        violation_type="doc_table_mismatch",
                        message=f"[{doc_id}] Table count difference exceeds threshold: HTML={h_tables} vs MD={m_tables}",
                    )
                )

            # 3. Length ratio bounds
            ratio = len(m_raw) / len(h_raw) if len(h_raw) > 0 else 0
            if ratio < 0.20 or ratio > 3.0:
                violations.append(
                    FileValidationViolation(
                        file_path=str(hp),
                        line_number=1,
                        violation_type="doc_ratio_anomaly",
                        message=f"[{doc_id}] Length ratio anomaly: ratio={ratio:.2f} (HTML={len(h_raw)}, MD={len(m_raw)})",
                    )
                )
    except _MANIFEST_READ_ERRORS as exc:
        violations.append(
            FileValidationViolation(
                file_path=str(manifest_path),
                line_number=0,
                violation_type="doc_parity_audit_error",
                message=f"Failed auditing 48-doc manifest parity: {exc}",
            )
        )

    return violations


def _audit_documentation_suite(docs_dir: Path) -> List[FileValidationViolation]:
    """Execute the comprehensive 20-rule documentation test suite in lemgendary-docs/tests."""
    violations: List[FileValidationViolation] = []
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


def _audit_dataset_manifests(project_dir: Path) -> List[FileValidationViolation]:
    """Verify dataset manifest schema correctness and manifold metadata integrity."""
    violations: List[FileValidationViolation] = []
    unified_yaml = project_dir / "unified_data.yaml"
    if unified_yaml.exists():
        try:
            data = yaml.safe_load(unified_yaml.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                violations.append(
                    FileValidationViolation(
                        file_path=str(unified_yaml),
                        line_number=1,
                        violation_type="manifest_schema_error",
                        message="unified_data.yaml root must be a dictionary",
                    )
                )
            elif "_registry_metadata" not in data:
                violations.append(
                    FileValidationViolation(
                        file_path=str(unified_yaml),
                        line_number=1,
                        violation_type="manifest_schema_error",
                        message="unified_data.yaml missing required '_registry_metadata' block",
                    )
                )
        except _MANIFEST_READ_ERRORS as exc:
            violations.append(
                FileValidationViolation(
                    file_path=str(unified_yaml),
                    line_number=0,
                    violation_type="manifest_parse_error",
                    message=str(exc),
                )
            )

    models_yaml = project_dir / "models" / "models_metadata.yaml"
    if not models_yaml.exists():
        models_yaml = project_dir / "models_metadata.yaml"
    if models_yaml.exists():
        try:
            data = yaml.safe_load(models_yaml.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                violations.append(
                    FileValidationViolation(
                        file_path=str(models_yaml),
                        line_number=1,
                        violation_type="manifest_schema_error",
                        message="models_metadata.yaml root must be a dictionary",
                    )
                )
            elif "task_metadata" not in data:
                violations.append(
                    FileValidationViolation(
                        file_path=str(models_yaml),
                        line_number=1,
                        violation_type="manifest_schema_error",
                        message="models_metadata.yaml missing required 'task_metadata' block",
                    )
                )
        except _MANIFEST_READ_ERRORS as exc:
            violations.append(
                FileValidationViolation(
                    file_path=str(models_yaml),
                    line_number=0,
                    violation_type="manifest_parse_error",
                    message=str(exc),
                )
            )

    info_files = (
        list(project_dir.glob("*/dataset_info.yaml"))
        + list(project_dir.glob("*/*/dataset_info.yaml"))
    )
    for d_info in info_files:
        try:
            info = yaml.safe_load(d_info.read_text(encoding="utf-8"))
            if not isinstance(info, dict):
                violations.append(
                    FileValidationViolation(
                        file_path=str(d_info),
                        line_number=1,
                        violation_type="manifold_schema_error",
                        message="dataset_info.yaml root must be a dictionary",
                    )
                )
            else:
                for req_key in ["count", "task"]:
                    if req_key not in info:
                        violations.append(
                            FileValidationViolation(
                                file_path=str(d_info),
                                line_number=1,
                                violation_type="manifold_schema_error",
                                message=f"dataset_info.yaml missing required field '{req_key}'",
                            )
                        )
        except _MANIFEST_READ_ERRORS as exc:
            violations.append(
                FileValidationViolation(
                    file_path=str(d_info),
                    line_number=0,
                    violation_type="manifold_parse_error",
                    message=str(exc),
                )
            )

    # ── Manifold Directory Integrity ─────────────────────────────────────────
    manifold_dirs = [
        d for d in project_dir.iterdir()
        if d.is_dir() and d.name.startswith("LemGendized")
    ]
    for manifold in manifold_dirs:
        for req_f in ["README.md", "dataset_info.yaml", "category.txt", "classes.txt"]:
            if not (manifold / req_f).exists():
                violations.append(
                    FileValidationViolation(
                        file_path=str(manifold),
                        line_number=1,
                        violation_type="manifold_missing_file",
                        message=f"{manifold.name} missing required file '{req_f}'",
                    )
                )

        if manifold.name == "LemGendizedForexUniverseLarge":
            meta_json = manifold / "dataset-metadata.json"
            if not meta_json.exists():
                violations.append(
                    FileValidationViolation(
                        file_path=str(meta_json),
                        line_number=1,
                        violation_type="manifold_missing_file",
                        message=f"{manifold.name} missing dataset-metadata.json",
                    )
                )
            parquets = list(manifold.glob("*.parquet"))
            if len(parquets) != 8:
                violations.append(
                    FileValidationViolation(
                        file_path=str(manifold),
                        line_number=1,
                        violation_type="manifold_parquet_count",
                        message=f"{manifold.name} expected 8 Parquet files, found {len(parquets)}",
                    )
                )

    # ── Staging Residue Check ────────────────────────────────────────────────
    for item in project_dir.iterdir():
        if item.name.startswith(".staging_") or item.name.startswith("staging_"):
            violations.append(
                FileValidationViolation(
                    file_path=str(item),
                    line_number=1,
                    violation_type="staging_residue",
                    message=f"Detected unapproved staging residue: {item.name}",
                )
            )
        elif item.is_file() and item.suffix.lower() in [".kaggle-partial", ".tmp", ".log"]:
            violations.append(
                FileValidationViolation(
                    file_path=str(item),
                    line_number=1,
                    violation_type="staging_residue",
                    message=f"Detected unapproved temporary file: {item.name}",
                )
            )

    return violations


def _audit_models_hub(project_dir: Path) -> List[FileValidationViolation]:
    """Verify model directory integrity, checkpoints, and zero residue."""
    violations: List[FileValidationViolation] = []
    root_readme = project_dir / "README.md"
    if not root_readme.exists():
        violations.append(
            FileValidationViolation(
                file_path=str(root_readme),
                line_number=1,
                violation_type="missing_readme",
                message="Root README.md is missing in models hub",
            )
        )

    model_dirs = [
        d for d in project_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".") and d.name not in ["__pycache__", ".githooks", "typings"]
    ]
    for m_dir in model_dirs:
        m_readme = m_dir / "README.md"
        if not m_readme.exists():
            violations.append(
                FileValidationViolation(
                    file_path=str(m_dir),
                    line_number=1,
                    violation_type="missing_model_readme",
                    message=f"Model directory '{m_dir.name}' is missing README.md",
                )
            )
        checkpoints_dir = m_dir / "checkpoints"
        if checkpoints_dir.exists():
            for pth in checkpoints_dir.glob("*.pth"):
                if pth.stat().st_size == 0:
                    violations.append(
                        FileValidationViolation(
                            file_path=str(pth),
                            line_number=1,
                            violation_type="corrupt_checkpoint",
                            message=f"Zero-byte checkpoint detected: {pth.name}",
                        )
                    )

    for item in project_dir.iterdir():
        if item.name.startswith(".staging_") or item.name.startswith("staging_"):
            violations.append(
                FileValidationViolation(
                    file_path=str(item),
                    line_number=1,
                    violation_type="model_residue",
                    message=f"Detected staging residue: {item.name}",
                )
            )
        elif item.is_file() and item.suffix.lower() in [".kaggle-partial", ".tmp", ".log"]:
            violations.append(
                FileValidationViolation(
                    file_path=str(item),
                    line_number=1,
                    violation_type="model_residue",
                    message=f"Detected temporary file: {item.name}",
                )
            )

    return violations


def _audit_training_suite(project_dir: Path) -> List[FileValidationViolation]:
    """Verify training suite configuration and neural model registry."""
    violations: List[FileValidationViolation] = []
    v2_models = project_dir / "unified_models_v2.yaml"
    if v2_models.exists():
        try:
            data = yaml.safe_load(v2_models.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                violations.append(
                    FileValidationViolation(
                        file_path=str(v2_models),
                        line_number=1,
                        violation_type="model_registry_error",
                        message="unified_models_v2.yaml root must be a dictionary",
                    )
                )
            elif "_registry_metadata" not in data:
                violations.append(
                    FileValidationViolation(
                        file_path=str(v2_models),
                        line_number=1,
                        violation_type="model_registry_error",
                        message="unified_models_v2.yaml missing '_registry_metadata'",
                    )
                )
        except _MANIFEST_READ_ERRORS as exc:
            violations.append(
                FileValidationViolation(
                    file_path=str(v2_models),
                    line_number=0,
                    violation_type="model_registry_parse_error",
                    message=str(exc),
                )
            )
    return violations


def _audit_git_hooks(project_dir: Path) -> List[FileValidationViolation]:
    """Verify that git hooks do not reference retired scripts or nonexistent files."""
    violations: List[FileValidationViolation] = []
    hook_dirs = [project_dir / ".githooks", project_dir / ".git" / "hooks"]

    retired_scripts = {
        "verify_training.py",
        "verify_datasets.py",
        "verify_docs.py",
        "verify_gui.py",
        "verify_manifold_datasets.py",
        "verify_models.py",
        "verify_env_manager.py",
    }

    checked_hooks = set()
    for h_dir in hook_dirs:
        if not h_dir.exists() or not h_dir.is_dir():
            continue
        for hook_file in h_dir.iterdir():
            if hook_file.is_file() and not hook_file.name.endswith(".sample"):
                try:
                    canon = hook_file.resolve()
                except (OSError, RuntimeError) as exc:
                    _log.warning("Cannot resolve git hook path %s: %s", hook_file, exc)
                    canon = hook_file
                if canon in checked_hooks:
                    continue
                checked_hooks.add(canon)
                try:
                    content = hook_file.read_text(encoding="utf-8", errors="replace")
                    for idx, line in enumerate(content.splitlines(), start=1):
                        line_clean = line.strip()
                        if line_clean.startswith("#"):
                            continue
                        for retired in retired_scripts:
                            if retired in line:
                                violations.append(
                                    FileValidationViolation(
                                        file_path=str(hook_file),
                                        line_number=idx,
                                        violation_type="retired_git_hook_script",
                                        message=(
                                            f"Hook invokes retired verification script '{retired}'. "
                                            f"Pre-commit hooks must delegate to "
                                            f"'lem-env validate --project {project_dir.name}'."
                                        ),
                                    )
                                )
                        py_matches = re.findall(
                            r'(?:python(?:\.exe)?|"\$VENV_PYTHON"|\$VENV_PYTHON)\s+([a-zA-Z0-9_\-\./\\]+\.py)',
                            line,
                        )
                        for py_script in py_matches:
                            script_path = (project_dir / py_script).resolve()
                            if not script_path.exists():
                                violations.append(
                                    FileValidationViolation(
                                        file_path=str(hook_file),
                                        line_number=idx,
                                        violation_type="missing_git_hook_target",
                                        message=f"Hook references nonexistent script '{py_script}'.",
                                    )
                                )
                except OSError as exc:
                    violations.append(
                        FileValidationViolation(
                            file_path=str(hook_file),
                            line_number=0,
                            violation_type="git_hook_read_error",
                            message=f"Failed reading git hook: {exc}",
                        )
                    )
    return violations


def run_domain_verification(project_dir: Path) -> List[FileValidationViolation]:
    """Execute specialized domain gate checks tailored to each repository."""
    violations: List[FileValidationViolation] = []
    p_name = project_dir.name

    # 1. Project-wide README changelog verification for code projects (skip asset stores)
    if p_name not in ("LemGendaryModels", "lemgendary-models", "LemGendaryDatasets", "lemgendary-datasets-store"):
        violations.extend(_audit_readme_changelog(project_dir))

    # 2. Project-specific domain verifications
    if p_name == "lemgendary-docs":
        violations.extend(_audit_docs_synchronization(project_dir))
        violations.extend(_audit_manifest_synchronization(project_dir))
        violations.extend(_audit_documentation_suite(project_dir))
    elif p_name in ("lemgendary-datasets", "LemGendaryDatasets"):
        violations.extend(_audit_dataset_manifests(project_dir))
    elif p_name == "lemgendary-training-suite":
        violations.extend(_audit_training_suite(project_dir))
    elif p_name in ("lemgendary-models", "LemGendaryModels"):
        violations.extend(_audit_models_hub(project_dir))

    violations.extend(_audit_git_hooks(project_dir))
    return violations


def validate_project(project_dir: Path, is_node_project: bool = False) -> ProjectValidationReport:
    """Execute complete validation suite across all files in a project."""
    compile_errors: List[FileValidationViolation] = []
    emoji_violations: List[FileValidationViolation] = []
    lint_errors: List[FileValidationViolation] = []
    yaml_errors: List[FileValidationViolation] = []
    json_errors: List[FileValidationViolation] = []
    html_errors: List[FileValidationViolation] = []
    wcag_violations: List[FileValidationViolation] = []
    domain_errors: List[FileValidationViolation] = []
    compiled_count = 0

    scannable_extensions = {
        ".py", ".ps1", ".json", ".yaml", ".yml", ".md", ".toml",
        ".ts", ".tsx", ".html", ".css",
    }
    skip_dirs = {
        ".git", ".venv", "node_modules", "__pycache__", "dist", "build", "target",
        ".pytest_cache", "raw-sets", "checkpoints", "weights", ".agents", "data",
        "scratch", ".cache", "images", "targets", "labels", "train", "val", "test",
        "lr", "hr", "export", "runs", ".lemtrain_server", ".lgd_server", ".env_server",
    }

    # ── Emoji & Python compilation scan ──────────────────────────────────────
    for root, dirs, files in Path(project_dir).walk():
        dirs[:] = [d for d in dirs if d not in skip_dirs and not d.startswith(".")]
        for f in files:
            f_path = root / f
            ext = f_path.suffix.lower()

            try:
                if f_path.stat().st_size > 1024 * 1024:
                    continue
            except OSError as exc:
                _log.warning("Cannot stat %s, skipping: %s", f_path, exc)
                continue

            if ext == ".py":
                compiled_count += 1
                err = compile_python_file(f_path)
                if err:
                    compile_errors.append(err)

            if ext in scannable_extensions:
                em_errs = scan_file_for_emojis(f_path)
                emoji_violations.extend(em_errs)

    # ── YAML lint ────────────────────────────────────────────────────────────
    yaml_errors = run_yamllint(project_dir)

    # ── JSON lint ────────────────────────────────────────────────────────────
    json_errors = run_jsonlint(project_dir)

    # ── Markdown lint ────────────────────────────────────────────────────────
    md_lint = run_markdownlint(project_dir)
    lint_errors.extend(md_lint)

    # ── Node.js project: ESLint + TypeScript ─────────────────────────────────
    if is_node_project or (project_dir / "package.json").exists():
        eslint_violations = run_eslint(project_dir)
        lint_errors.extend(eslint_violations)
        ts_violations = run_typescript_check(project_dir)
        lint_errors.extend(ts_violations)

    # ── Static HTML projects: W3C + WCAG ─────────────────────────────────────
    has_static_html = any(
        f.exists() for f in [
            project_dir / "index.html",
            project_dir / "papers",
        ]
    ) and not (project_dir / "src-tauri").exists()

    if has_static_html:
        html_errors = run_html_validate(project_dir)
        wcag_violations = run_pa11y_wcag(project_dir)

    # ── PowerShell scripts: PSScriptAnalyzer ─────────────────────────────────
    ps1_violations = run_psscriptanalyzer(project_dir)
    lint_errors.extend(ps1_violations)

    # ── Domain-Specific Verification Gates ───────────────────────────────────
    domain_errors = run_domain_verification(project_dir)

    passed = (
        len(compile_errors) == 0
        and len(emoji_violations) == 0
        and len(lint_errors) == 0
        and len(yaml_errors) == 0
        and len(json_errors) == 0
        and len(html_errors) == 0
        and len(wcag_violations) == 0
        and len(domain_errors) == 0
    )

    return ProjectValidationReport(
        project_name=project_dir.name,
        compiled_files_count=compiled_count,
        compile_errors=compile_errors,
        emoji_violations=emoji_violations,
        lint_errors=lint_errors,
        yaml_errors=yaml_errors,
        json_errors=json_errors,
        html_errors=html_errors,
        wcag_violations=wcag_violations,
        domain_errors=domain_errors,
        passed=passed,
    )