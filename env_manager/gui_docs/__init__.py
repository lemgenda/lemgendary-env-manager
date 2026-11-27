"""GUI documentation automation package for LemGendary AI Studio."""

from env_manager.gui_docs.html_renderer import render_markdown_to_html
from env_manager.gui_docs.manual_sync import sync_gui_manual
from env_manager.gui_docs.registry_sync import sync_gui_registry
from env_manager.gui_docs.screenshots import capture_gui_screenshots
from env_manager.gui_docs.specs import FIGURE_SPECS, CalloutSpec, FigureSpec

__all__ = [
    "FIGURE_SPECS",
    "CalloutSpec",
    "FigureSpec",
    "capture_gui_screenshots",
    "render_markdown_to_html",
    "sync_gui_manual",
    "sync_gui_registry",
]
