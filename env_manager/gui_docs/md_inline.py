"""Inline Markdown formatting, math protection, and anchor slugging helpers."""

import html
import re

_MATH_DISPLAY_RE = re.compile(r"\$\$([\s\S]+?)\$\$")
_MATH_INLINE_RE = re.compile(r"(?<!\\)\$([^\n\$]+?)(?<!\\)\$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_IMG_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")


def github_slug(heading: str) -> str:
    """Generate GitHub-style anchor slug from heading text."""
    text = heading.replace("`", "").replace("*", "")
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[^\w\s\-]", "", text)
    text = text.strip().lower()
    text = re.sub(r" ", "-", text)
    return text.strip("-")


def sec_id_from_heading(heading: str) -> str:
    """Return the ``sec-`` prefixed HTML section/heading ID for a heading."""
    return f"sec-{github_slug(heading)}"


def rewrite_md_href(href: str) -> str:
    """Rewrite internal ``./PAPER.md`` links to sibling ``.html`` links."""
    mapping = {
        "./MANUAL_AI_STUDIO_GUI.md": "ai-studio-manual.html",
        "./PAPER_AI_STUDIO_GUI.md": "ai-studio-whitepaper.html",
        "./GUI_CONTROL_REGISTRY.md": "gui-control-registry.html",
        "./ECOSYSTEM_ARCHITECTURE.md": "ecosystem-architecture.html",
        "./VERSIONING_POLICY.md": "versioning-policy.html",
        "./PAPER_ENV_MANAGER.md": "env_manager.html",
        "./PAPER_DATASET_COMPILER.md": "dataset-compiler.html",
        "./PAPER_DEGRADATION_ENGINE.md": "degradation-engine.html",
        "./PAPER_TRAINING_SUITE_MASTER.md": "training-suite-master.html",
    }
    if href in mapping:
        return mapping[href]
    if href.startswith(("#sec-", "#figure-")):
        return href
    if href.startswith("#"):
        return f"#sec-{href[1:]}"
    return href


def format_inline(text: str) -> str:
    """Convert inline Markdown to HTML while preserving LaTeX math verbatim."""
    tokens: list[str] = []

    def _stash(raw_html: str) -> str:
        idx = len(tokens)
        tokens.append(raw_html)
        return f"\x00TOK{idx}\x00"

    # Protect display and inline math verbatim so parity checks pass 100%.
    work = _MATH_DISPLAY_RE.sub(lambda m: _stash(f"$${m.group(1)}$$"), text)
    work = _MATH_INLINE_RE.sub(lambda m: _stash(f"${m.group(1)}$"), work)
    work = _CODE_RE.sub(lambda m: _stash(f"<code>{html.escape(m.group(1))}</code>"), work)
    work = _IMG_RE.sub(
        lambda m: _stash(
            f'<img src="{html.escape(m.group(2), quote=True)}" '
            f'alt="{html.escape(m.group(1), quote=True)}">'
        ),
        work,
    )

    def _link_sub(match: re.Match[str]) -> str:
        label = html.escape(match.group(1), quote=False)
        label = _BOLD_RE.sub(r"<strong>\1</strong>", label)
        label = _ITALIC_RE.sub(r"<em>\1</em>", label)
        href = html.escape(rewrite_md_href(match.group(2)), quote=True)
        return _stash(f'<a href="{href}">{label}</a>')

    work = _LINK_RE.sub(_link_sub, work)
    work = html.escape(work, quote=False)
    work = _BOLD_RE.sub(r"<strong>\1</strong>", work)
    work = _ITALIC_RE.sub(r"<em>\1</em>", work)

    for idx in range(len(tokens) - 1, -1, -1):
        work = work.replace(f"\x00TOK{idx}\x00", tokens[idx])
    if "\x00" in work:
        raise ValueError(f"Unresolved inline token in rendered text: {text[:80]!r}")
    return work
