#!/usr/bin/env python3
"""Thin CLI entrypoint delegating to ``env_manager.line_length``."""

import argparse
import importlib
import sys
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="line_length_guard",
        description="Check or auto-wrap Markdown and Python line lengths.",
    )
    parser.add_argument("command", choices=("check", "fix"))
    parser.add_argument("paths", nargs="*", help="Files or directories to scan.")
    parser.add_argument("--staged", action="store_true", help="Inspect git staged files only.")
    parser.add_argument("--md-limit", type=int, default=80)
    parser.add_argument("--py-limit", type=int, default=100)
    parser.add_argument("--ignore-headings", action="store_true")
    parser.add_argument("--check-headings", action="store_true")
    parser.add_argument("--diff", action="store_true", help="Show unified diff when fixing.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse CLI arguments and delegate to ``env_manager.line_length``."""
    project_root = str(Path(__file__).resolve().parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    mod = importlib.import_module("env_manager.line_length")

    args = _build_parser().parse_args(argv)
    repo_root = Path.cwd()
    ignore_headings = bool(args.ignore_headings and not args.check_headings)
    opts = mod.GuardOptions(
        md_limit=args.md_limit,
        py_limit=args.py_limit,
        ignore_headings=ignore_headings,
        fix=args.command == "fix",
        show_diff=args.diff,
    )

    if args.staged:
        files = mod.get_staged_files(repo_root)
    else:
        targets = [Path(p) for p in args.paths] if args.paths else [repo_root]
        files = mod.discover_files(targets)

    if args.command == "fix":
        return int(mod.run_fix(files, opts, repo_root))
    return int(mod.run_check(files, opts, repo_root))


if __name__ == "__main__":
    sys.exit(main())
