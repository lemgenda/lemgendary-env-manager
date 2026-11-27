#!/usr/bin/env python3
"""Convenience CLI alias for 'python tools/line_length_guard.py fix'."""

import sys

try:
    from tools.line_length_guard import main
except ModuleNotFoundError:
    from line_length_guard import main

if __name__ == "__main__":
    sys.exit(main(["fix", *sys.argv[1:]]))
