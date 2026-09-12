#!/usr/bin/env python3
"""
Pre-commit Verification Suite for LemGendary Environment Manager.

Enforces four mandatory gates:
1. Python syntax and bytecode compilation (python -m py_compile)
2. Static type safety verification (pyright)
3. Code quality and linting verification (pylint)
4. Markdown documentation linting (markdownlint-cli)
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

# Resolve python interpreter within virtual environment
VENV_PYTHON = REPO_ROOT / ".venv" / "Scripts" / "python.exe"
if not VENV_PYTHON.exists():
    VENV_PYTHON = Path(sys.executable)


def log_header(title: str):
    print(f"\n{'=' * 70}")
    print(f" [GATE] {title}")
    print(f"{'=' * 70}")


def get_staged_files() -> list[Path]:
    try:
        res = subprocess.run(
            ["git", "diff", "--name-only", "--cached"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        files = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if line:
                p = REPO_ROOT / line
                if p.exists():
                    files.append(p)
        return files
    except Exception:
        return []


def check_python_compilation(target_files: list[Path] | None = None) -> bool:
    log_header("GATE 1: Python Syntax & Compilation (python -m py_compile)")
    if target_files:
        py_files = [p for p in target_files if p.suffix.lower() == ".py"]
    else:
        py_files = sorted(list(REPO_ROOT.glob("*.py")) + list((REPO_ROOT / "env_manager").glob("*.py")))

    if not py_files:
        print("[INFO] No Python files to compile.")
        return True

    print(f"[RUN] Compiling {len(py_files)} Python source files...")
    has_errors = False
    for py_file in py_files:
        cmd = [str(VENV_PYTHON), "-m", "py_compile", str(py_file)]
        res = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if res.returncode != 0:
            print(f"[FAIL] Syntax error in {py_file.name}:")
            if res.stderr:
                print(res.stderr.strip())
            has_errors = True
        else:
            print(f" [OK] {py_file.name}")

    if has_errors:
        print("[FAIL] One or more Python files failed syntax compilation.")
        return False

    print(f"[PASS] All {len(py_files)} Python files compiled cleanly.")
    return True


def check_pyright(target_files: list[Path] | None = None) -> bool:
    log_header("GATE 2: Static Type Safety (pyright)")
    if target_files:
        py_files = [p for p in target_files if p.suffix.lower() == ".py"]
        if not py_files:
            print("[INFO] No Python files to type-check.")
            return True
        cmd = [str(VENV_PYTHON), "-m", "pyright"] + [str(p) for p in py_files]
    else:
        cmd = [str(VENV_PYTHON), "-m", "pyright", "."]

    print("[RUN] Running pyright type checking...")
    try:
        res = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=False)
        if res.stdout:
            print(res.stdout.strip())
        if res.returncode != 0:
            if res.stderr:
                print(res.stderr.strip())
            print("[FAIL] Pyright reported type violations.")
            return False
        print("[PASS] Pyright type checking passed with 0 errors.")
        return True
    except FileNotFoundError:
        print("[WARN] Pyright is not available in current environment. Skipping.")
        return True


def check_pylint(target_files: list[Path] | None = None) -> bool:
    log_header("GATE 3: Code Quality & Linting (pylint)")
    rcfile = REPO_ROOT / ".pylintrc"
    if not rcfile.exists():
        print(f"[FAIL] .pylintrc not found at {rcfile}")
        return False

    if target_files:
        py_files = [p for p in target_files if p.suffix.lower() == ".py"]
        if not py_files:
            print("[INFO] No staged Python files to lint.")
            return True
        targets = [str(p) for p in py_files]
    else:
        targets = ["env_manager"]

    cmd = [str(VENV_PYTHON), "-m", "pylint", f"--rcfile={rcfile}"] + targets
    print(f"[RUN] Running pylint on {len(targets)} target(s)...")
    res = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, check=False)
    if res.stdout:
        print(res.stdout.strip())
    if res.stderr:
        print(res.stderr.strip())

    if res.returncode != 0:
        print("[FAIL] Pylint detected code quality issues or score < 10.00/10.")
        return False

    print("[PASS] Pylint code quality check passed.")
    return True


def check_markdown(target_files: list[Path] | None = None) -> bool:
    log_header("GATE 4: Markdown Documentation Linting (markdownlint-cli)")
    cfg_path = REPO_ROOT / ".markdownlint.json"
    if not cfg_path.exists():
        cfg_path = REPO_ROOT / ".markdownlint.yaml"

    if target_files:
        md_files = [p for p in target_files if p.suffix.lower() in [".md", ".markdown"]]
        if not md_files:
            print("[INFO] No staged Markdown files to lint.")
            return True
        target_args = [str(p) for p in md_files]
    else:
        target_args = ["**/*.md"]

    cmd = [
        "npx",
        "markdownlint-cli",
        *target_args,
        "-c",
        str(cfg_path),
        "--ignore",
        "node_modules/**",
        "--ignore",
        ".venv/**",
    ]

    print(f"[RUN] Linting Markdown files...")
    try:
        res = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, shell=True, check=False)
        if res.returncode != 0:
            print("[FAIL] Markdown lint violations detected:")
            if res.stdout:
                print(res.stdout.strip())
            if res.stderr:
                print(res.stderr.strip())
            return False
        print("[PASS] All Markdown files passed with 0 errors/warnings.")
        return True
    except FileNotFoundError:
        print("[WARN] npx markdownlint-cli is not available on PATH. Skipping.")
        return True


def clean_residual_artifacts() -> None:
    """Clean up test logs, caches, and scratch files."""
    for pattern in ["*.log", "*.tmp"]:
        for f in REPO_ROOT.glob(pattern):
            try:
                f.unlink()
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser(description="LemGendary Environment Manager Verification Suite")
    parser.add_argument("--staged", action="store_true", help="Audit only staged git files")
    args = parser.parse_args()

    print("\n" + "=" * 70)
    print(" LEMGENDARY ENVIRONMENT MANAGER - PRE-COMMIT AUDIT SUITE")
    print("=" * 70)

    clean_residual_artifacts()

    target_files = None
    if args.staged:
        target_files = get_staged_files()
        print(f"[MODE] Staged files audit ({len(target_files)} staged files)")

    g1 = check_python_compilation(target_files)
    g2 = check_pyright(target_files)
    g3 = check_pylint(target_files)
    g4 = check_markdown(target_files)

    print("\n" + "=" * 70)
    print(" ENVIRONMENT MANAGER AUDIT SUMMARY")
    print("=" * 70)
    print(f"  Gate 1: Python Syntax & Compilation (py_compile) : {'PASSED' if g1 else 'FAILED'}")
    print(f"  Gate 2: Static Type Safety (pyright)             : {'PASSED' if g2 else 'FAILED'}")
    print(f"  Gate 3: Code Quality Linting (pylint)            : {'PASSED' if g3 else 'FAILED'}")
    print(f"  Gate 4: Markdown Linting (markdownlint)          : {'PASSED' if g4 else 'FAILED'}")
    print("=" * 70)

    if not (g1 and g2 and g3 and g4):
        print("[ABORT] One or more pre-commit checks FAILED.\n")
        sys.exit(1)

    print("[SUCCESS] All pre-commit checks PASSED successfully.\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
