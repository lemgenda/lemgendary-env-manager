#!/usr/bin/env python3
"""Convenience CLI alias for 'python tools/line_length_guard.py check'."""

import sys

try:
    from tools.line_length_guard import main
except ModuleNotFoundError:
    from line_length_guard import main

if __name__ == "__main__":
    cli_args = sys.argv[1:]
    if "--ignore-headings" not in cli_args and "--check-headings" not in cli_args:
        cli_args.append("--ignore-headings")
    sys.exit(main(["check", *cli_args]))
