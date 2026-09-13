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
import json
import py_compile
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from env_manager._logging import get_logger

_log = get_logger(__name__)


@dataclass
class FileValidationViolation:
    """Represents a violation found in a file."""
    file_path: str
    line_number: int
    violation_type: str
    message: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert violation to dictionary."""
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
        """Convert report to dictionary."""
        return asdict(self)



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
    except Exception as exc:
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
    except Exception as exc:
        return FileValidationViolation(
            file_path=str(file_path),
            line_number=0,
            violation_type="compilation_error",
            message=str(exc),
        )


def _get_env_manager_python() -> Optional[str]:
    """Return the path to the env-manager .venv Python executable."""
    import platform
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

    skip_dirs = {".git", ".venv", "node_modules", "__pycache__", "dist", "build", "target", ".pytest_cache", "raw-sets", "checkpoints", "weights", ".agents", "data", "scratch", ".cache"}
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
            timeout=60,
            check=False,
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
        if proc.returncode not in (0, 1):
            _log.warning("yamllint returned unexpected exit code %d for %s", proc.returncode, project_dir)
    except FileNotFoundError:
        _log.warning("yamllint not found — install it in the env-manager venv: pip install yamllint")
    except Exception as exc:
        _log.warning("yamllint failed for %s: %s", project_dir, exc)
    return violations


def run_jsonlint(project_dir: Path) -> List[FileValidationViolation]:
    """Validate RFC 8259 syntax across all JSON files in the project."""
    violations: List[FileValidationViolation] = []
    skip_dirs = {
        ".git", ".venv", "node_modules", "__pycache__", "dist", "build", "target",
        ".pytest_cache", "raw-sets", "checkpoints", "weights", ".agents", "data", "scratch", ".cache",
        "images", "targets", "labels", "train", "val", "test", "lr", "hr"
    }
    json_files = [
        f for f in project_dir.rglob("*.json")
        if not any(part in skip_dirs for part in f.parts)
    ]

    for jf in json_files:
        try:
            with open(jf, "r", encoding="utf-8") as f:
                json.load(f)
        except json.JSONDecodeError as exc:
            violations.append(
                FileValidationViolation(
                    file_path=str(jf),
                    line_number=exc.lineno,
                    violation_type="json_syntax_error",
                    message=f"JSON syntax error: {exc.msg} (col {exc.colno})",
                )
            )
        except Exception as exc:
            violations.append(
                FileValidationViolation(
                    file_path=str(jf),
                    line_number=0,
                    violation_type="json_read_error",
                    message=str(exc),
                )
            )

    return violations


def _resolve_tool_cmd(tool_name: str, npx_pkg: str, project_dir: Optional[Path] = None) -> List[str]:
    """Resolve local binary from project or lemgendary-env-manager node_modules/.bin or fallback to npx."""
    mgr_dir = Path(__file__).resolve().parent.parent
    candidate_dirs = []
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

    # Check global PATH (e.g., globally installed npm binaries)
    global_bin = (
        shutil.which(f"{tool_name}.cmd")
        or shutil.which(f"{tool_name}.exe")
        or shutil.which(tool_name)
    )
    if global_bin:
        return [global_bin]

    npx = shutil.which("npx") or ("npx.cmd" if sys.platform == "win32" else "npx")
    return [npx, "--yes", npx_pkg]


def run_markdownlint(project_dir: Path) -> List[FileValidationViolation]:
    """Run markdownlint-cli on Markdown files using local binary or npx."""
    violations: List[FileValidationViolation] = []

    skip_dirs = {".git", ".venv", "node_modules", "__pycache__", "dist", "build", "target", ".pytest_cache", "raw-sets", "checkpoints", "weights", ".agents", "data", "scratch", ".cache"}
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
            timeout=60,
            check=False,
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
    except Exception as exc:
        _log.warning("markdownlint-cli failed for %s: %s", project_dir, exc)
    return violations


def run_eslint(project_dir: Path) -> List[FileValidationViolation]:
    """Run ESLint on JS/TS source files for a Node.js project."""
    violations: List[FileValidationViolation] = []
    npx = shutil.which("npx")
    if not npx:
        return violations

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
            lint_target, "--ext", ".js,.jsx,.ts,.tsx",
            "--format", "compact", "--max-warnings", "0"
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        combined = proc.stdout + proc.stderr
        for line in combined.splitlines():
            # ESLint compact format: path: line col: severity message  (rule)
            m = re.match(r"^(.+?):\s+line (\d+),.*?Error\s+(.+)$", line.strip())
            if m:
                violations.append(
                    FileValidationViolation(
                        file_path=m.group(1),
                        line_number=int(m.group(2)),
                        violation_type="eslint_error",
                        message=m.group(3).strip(),
                    )
                )
            else:
                # Fallback: any line with " error " is worth capturing
                if " error " in line.lower() and line.strip() and not line.startswith("Browserslist"):
                    violations.append(
                        FileValidationViolation(
                            file_path=str(project_dir),
                            line_number=0,
                            violation_type="eslint_error",
                            message=line.strip(),
                        )
                    )
    except Exception as exc:
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
            timeout=120,
            check=False,
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
    except Exception as exc:
        _log.warning("tsc --noEmit failed for %s: %s", project_dir, exc)
    return violations


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
        cmd = _resolve_tool_cmd("html-validate", "html-validate") + ["--formatter", "text"] + [str(f) for f in html_files]
        proc = subprocess.run(
            cmd,
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
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
    except Exception as exc:
        _log.warning("html-validate failed for %s: %s", project_dir, exc)
    return violations



def run_pa11y_wcag(project_dir: Path) -> List[FileValidationViolation]:
    """Run pa11y WCAG 2.2 AA accessibility audit against static HTML files.

    For the lemgendary-docs project this runs directly against the static HTML.
    For the React GUI (lemgendary-ai-studio-gui) WCAG requires a running server
    and is therefore skipped here — report notes this limitation.
    """
    violations: List[FileValidationViolation] = []

    html_files = [
        f for f in project_dir.rglob("*.html")
        if not any(skip in f.parts for skip in (".venv", "node_modules", ".git", "dist"))
        and f.stat().st_size < 512 * 1024  # Skip very large generated HTML
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
                timeout=30,
                check=False,
            )
            combined = proc.stdout + proc.stderr
            current_issue: Optional[str] = None
            for line in combined.splitlines():
                line = line.strip()
                if line.startswith("Error:") or line.startswith("Warning:") or line.startswith("Notice:"):
                    current_issue = line
                elif current_issue and line.startswith("("):
                    # Line with WCAG code e.g. (WCAG2AA.Principle1.Guideline1_1.1_1_1.H37)
                    violations.append(
                        FileValidationViolation(
                            file_path=str(html_file),
                            line_number=0,
                            violation_type="wcag_aa_violation",
                            message=f"{current_issue} {line}",
                        )
                    )
                    current_issue = None
        except Exception as exc:
            _log.warning("pa11y WCAG audit failed for %s: %s", html_file, exc)

    return violations


def run_psscriptanalyzer(project_dir: Path) -> List[FileValidationViolation]:
    """Run PSScriptAnalyzer on all PowerShell scripts in the project.

    PSScriptAnalyzer is the official Microsoft PowerShell linter.
    Install via: Install-Module PSScriptAnalyzer -Scope CurrentUser -Force

    Reports errors and warnings on all .ps1 files excluding .venv and node_modules.
    """
    import platform
    violations: List[FileValidationViolation] = []
    if platform.system().lower() != "windows":
        return violations

    ps1_files = [
        f for f in project_dir.rglob("*.ps1")
        if not any(skip in f.parts for skip in (".venv", "node_modules", ".git"))
    ]
    if not ps1_files:
        return violations

    # Build a comma-separated list of paths for the PS1 command
    paths_ps = ", ".join(f'"{str(f)}"' for f in ps1_files)
    ps_cmd = (
        f"$results = @({paths_ps}) | ForEach-Object {{ "
        "Invoke-ScriptAnalyzer -Path $_ -Severity Warning,Error "
        "}}; $results | ForEach-Object {{ "
        "\"$($_.ScriptPath):$($_.Line):$($_.Severity):$($_.Message)\" }}"
    )

    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        combined = proc.stdout + proc.stderr
        if "CommandNotFoundException" in combined or "is not recognized" in combined.lower():
            _log.warning(
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
                except (ValueError, IndexError):
                    continue
    except Exception as exc:
        _log.warning("PSScriptAnalyzer execution failed: %s", exc)
    return violations



def run_jsonlint(project_dir: Path) -> List[FileValidationViolation]:
    """Audit all JSON configuration and manifest files for syntax and structure errors."""
    violations: List[FileValidationViolation] = []
    skip_dirs = {
        ".git", ".venv", "node_modules", "__pycache__", "dist", "build", "target",
        ".pytest_cache", "raw-sets", "checkpoints", "weights", ".agents", "data", "scratch", ".cache", ".antigravity"
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
                except Exception as exc:
                    violations.append(
                        FileValidationViolation(
                            file_path=str(f_path),
                            line_number=0,
                            violation_type="json_read_error",
                            message=str(exc),
                        )
                    )
    return violations


def _tokenize_text(text: str) -> List[str]:
    """Extract normalized word tokens for similarity comparison."""
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


def _audit_docs_synchronization(docs_dir: Path) -> List[FileValidationViolation]:
    """Verify word-for-word synchronization between Markdown papers and HTML documents."""
    violations: List[FileValidationViolation] = []
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        _log.warning("bs4 not available for doc sync verification")
        return violations

    md_dir = docs_dir / "MD-Papers"
    papers_dir = docs_dir / "papers"
    if not md_dir.exists() or not papers_dir.exists():
        return violations

    pair_map = {
        "PAPER_DATASET_COMPILER.md": "dataset-compiler.html",
        "PAPER_FOREX_PREDICTOR.md": "forex_predictor.html",
        "PAPER_LEMGENDARY_FFANET.md": "ffanet.html",
        "PAPER_LEMGENDARY_HYBRID.md": "universal-hybrid.html",
        "PAPER_LEMGENDARY_MIRNET.md": "mirnet.html",
        "PAPER_LEMGENDARY_MPRNET.md": "mprnet.html",
        "PAPER_LEMGENDARY_NAFNET.md": "nafnet.html",
        "PAPER_LEMGENDARY_NIMA.md": "nima-quality.html",
        "PAPER_TRAINING_PATHOLOGY.md": "training-pathology.html",
        "PAPER_TRAINING_SUITE.md": "training-suite-master.html",
        "PAPER_AI_STUDIO_GUI.md": "ai-studio-whitepaper.html",
        "MANUAL_AI_STUDIO_GUI.md": "ai-studio-manual.html",
        "PAPER_ENV_MANAGER.md": "env_manager.html",
        "MANUAL_CLI.md": "cli-manual.html",
        "MANUAL_API.md": "api-manual.html",
    }

    for md_name, html_name in pair_map.items():
        md_file = md_dir / md_name
        html_file = papers_dir / html_name
        if not md_file.exists() or not html_file.exists():
            continue

        try:
            md_raw = md_file.read_text(encoding="utf-8")
            html_raw = html_file.read_text(encoding="utf-8")

            soup = BeautifulSoup(html_raw, "html.parser")
            for tag in soup.find_all(["nav", "header", "footer", "script", "style", "pre"]):
                tag.decompose()
            for cb in soup.find_all(class_="cli-block"):
                cb.decompose()
            for modal in soup.find_all("div", class_="modal"):
                modal.decompose()

            md_secs = _extract_md_sections(md_raw)
            html_secs = _extract_html_sections(soup)

            total_md_words = 0
            total_html_words = 0
            matched_words = 0

            for html_title, html_content in html_secs.items():
                matched_md = None
                h_num = html_title.split(".")[0].strip() if "." in html_title else ""
                for md_title, md_content in md_secs.items():
                    if (html_title.lower() == md_title.lower()) or (html_title.lower() in md_title.lower()) or (md_title.lower() in html_title.lower()):
                        matched_md = md_content
                        break
                if not matched_md:
                    for md_title, md_content in md_secs.items():
                        m_num = md_title.split(".")[0].strip() if "." in md_title else ""
                        if h_num and h_num == m_num:
                            matched_md = md_content
                            break

                w_html = _tokenize_text(html_content)
                total_html_words += len(w_html)
                if matched_md:
                    w_md = _tokenize_text(matched_md)
                    total_md_words += len(w_md)
                    seq_r = difflib.SequenceMatcher(None, w_md, w_html).ratio()
                    c_m = collections.Counter(w_md)
                    c_h = collections.Counter(w_html)
                    bow_r = sum((c_m & c_h).values()) / max(len(w_md), len(w_html), 1)
                    sim = max(seq_r, bow_r)
                    matched_words += int(sim * max(len(w_md), len(w_html)))

            if max(total_md_words, total_html_words) > 0:
                overall_ratio = matched_words / max(total_md_words, total_html_words)
            else:
                md_clean = re.sub(r"<!--.*?-->", "", md_raw, flags=re.DOTALL)
                md_clean = re.sub(r"```.*?```", "", md_clean, flags=re.DOTALL)
                md_tokens = _tokenize_text(md_clean)
                html_tokens = _tokenize_text(soup.get_text(separator=" "))
                if not md_tokens or not html_tokens:
                    continue
                seq_r = difflib.SequenceMatcher(None, md_tokens, html_tokens).ratio()
                c_m = collections.Counter(md_tokens)
                c_h = collections.Counter(html_tokens)
                bow_r = sum((c_m & c_h).values()) / max(len(md_tokens), len(html_tokens), 1)
                overall_ratio = max(seq_r, bow_r)

            if overall_ratio < 0.85:
                violations.append(
                    FileValidationViolation(
                        file_path=str(html_file),
                        line_number=1,
                        violation_type="doc_sync_drift",
                        message=f"Sync similarity with {md_name} is {overall_ratio*100:.1f}% (below mandatory 85% threshold)",
                    )
                )
        except Exception as exc:
            violations.append(
                FileValidationViolation(
                    file_path=str(html_file),
                    line_number=0,
                    violation_type="doc_sync_error",
                    message=f"Doc sync evaluation failed: {exc}",
                )
            )
    return violations


def _audit_dataset_manifests(project_dir: Path) -> List[FileValidationViolation]:
    """Verify dataset manifest schema correctness and manifold metadata integrity."""
    violations: List[FileValidationViolation] = []
    unified_yaml = project_dir / "unified_data.yaml"
    if unified_yaml.exists():
        try:
            import yaml
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
        except Exception as exc:
            violations.append(
                FileValidationViolation(
                    file_path=str(unified_yaml),
                    line_number=0,
                    violation_type="manifest_parse_error",
                    message=str(exc),
                )
            )

    models_yaml = project_dir / "models_metadata.yaml"
    if models_yaml.exists():
        try:
            import yaml
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
        except Exception as exc:
            violations.append(
                FileValidationViolation(
                    file_path=str(models_yaml),
                    line_number=0,
                    violation_type="manifest_parse_error",
                    message=str(exc),
                )
            )

    info_files = list(project_dir.glob("*/dataset_info.yaml")) + list(project_dir.glob("*/*/dataset_info.yaml"))
    for d_info in info_files:
        try:
            import yaml
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
        except Exception as exc:
            violations.append(
                FileValidationViolation(
                    file_path=str(d_info),
                    line_number=0,
                    violation_type="manifold_parse_error",
                    message=str(exc),
                )
            )
    # ── Manifold Directory Integrity ───────────────────────────────────────────
    manifold_dirs = [d for d in project_dir.iterdir() if d.is_dir() and d.name.startswith("LemGendized")]
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
            import yaml
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
        except Exception as exc:
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
                except Exception:
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
                        # 1. Check for retired verify_*.py scripts
                        for retired in retired_scripts:
                            if retired in line:
                                violations.append(
                                    FileValidationViolation(
                                        file_path=str(hook_file),
                                        line_number=idx,
                                        violation_type="retired_git_hook_script",
                                        message=(
                                            f"Hook invokes retired verification script '{retired}'. "
                                            f"Pre-commit hooks must delegate to 'lem-env validate --project {project_dir.name}'."
                                        ),
                                    )
                                )
                        # 2. Check for python <script>.py where <script>.py does not exist on disk
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
                except Exception as exc:
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

    if p_name == "lemgendary-docs":
        violations.extend(_audit_docs_synchronization(project_dir))
    elif p_name in ("lemgendary-datasets", "LemGendaryDatasets"):
        violations.extend(_audit_dataset_manifests(project_dir))
    elif p_name == "lemgendary-training-suite":
        violations.extend(_audit_training_suite(project_dir))
    elif p_name in ("lemgendary-models", "LemGendaryModels"):
        violations.extend(_audit_models_hub(project_dir))

    # ── Git Hook Integrity Gate (all repositories) ───────────────────────────
    violations.extend(_audit_git_hooks(project_dir))

    return violations


def validate_project(project_dir: Path, is_node_project: bool = False) -> ProjectValidationReport:
    """Execute complete validation suite across all files in a project.

    Performs:
    - py_compile bytecode compilation (Python projects)
    - Zero-emoji compliance (all scannable files)
    - yamllint (YAML files across all projects)
    - jsonlint (JSON files across all projects)
    - markdownlint (Markdown documentation across all projects)
    - PSScriptAnalyzer (PowerShell scripts across all projects)
    - ESLint and TypeScript tsc (Node.js projects)
    - W3C HTML and WCAG 2.2 AA (documentation projects)
    - Tailored domain verification gates (whitepaper sync, manifests, registries)
    """
    compile_errors: List[FileValidationViolation] = []
    emoji_violations: List[FileValidationViolation] = []
    lint_errors: List[FileValidationViolation] = []
    yaml_errors: List[FileValidationViolation] = []
    json_errors: List[FileValidationViolation] = []
    html_errors: List[FileValidationViolation] = []
    wcag_violations: List[FileValidationViolation] = []
    domain_errors: List[FileValidationViolation] = []
    compiled_count = 0

    scannable_extensions = {".py", ".ps1", ".json", ".yaml", ".yml", ".md", ".toml", ".ts", ".tsx", ".html", ".css"}
    skip_dirs = {
        ".git", ".venv", "node_modules", "__pycache__", "dist", "build", "target",
        ".pytest_cache", "raw-sets", "checkpoints", "weights", ".agents", "data", "scratch", ".cache",
        "images", "targets", "labels", "train", "val", "test", "lr", "hr"
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
            except Exception:
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

