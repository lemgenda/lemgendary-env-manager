"""Locate the LemGendary workspace: the folder that holds the sibling repos.

This module is the only place that derives the workspace root. Callers that
accept a ``base_dir`` fall back to :func:`workspace_root` when none is given.
"""

import os
from pathlib import Path

WORKSPACE_ROOT_ENV = "LEMGENDARY_WORKSPACE_ROOT"
"""Environment variable that overrides the workspace root (used by tests)."""

ENV_MANAGER_DIR = Path(__file__).resolve().parent.parent
"""This repository's checkout; its bundled tools and manifests live here."""


def workspace_root() -> Path:
    """Return the workspace root.

    ``LEMGENDARY_WORKSPACE_ROOT`` wins when set; otherwise the root is the
    parent of this repository's checkout.
    """
    override = os.environ.get(WORKSPACE_ROOT_ENV)
    if override:
        return Path(override).resolve()
    return ENV_MANAGER_DIR.parent
