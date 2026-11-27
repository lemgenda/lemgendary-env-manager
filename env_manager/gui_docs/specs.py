"""Canonical figure and callout specifications for AI Studio GUI screenshots."""

import json
from pathlib import Path

from env_manager.gui_docs.spec_models import CalloutSpec, FigureSpec


def _load_figure_specs() -> tuple[FigureSpec, ...]:
    catalog_path = Path(__file__).resolve().parent / "specs_catalog.json"
    raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    return tuple(
        FigureSpec(
            filename=item["filename"],
            title=item["title"],
            tab_id=item["tab_id"],
            callouts=tuple(CalloutSpec(**c) for c in item["callouts"]),
            setup_steps=tuple(item.get("setup_steps", ())),
            clip_selector=item.get("clip_selector"),
            viewport=tuple(item.get("viewport", (1440, 900))),
        )
        for item in raw
    )


FIGURE_SPECS: tuple[FigureSpec, ...] = _load_figure_specs()

__all__ = [
    "FIGURE_SPECS",
    "CalloutSpec",
    "FigureSpec",
]
