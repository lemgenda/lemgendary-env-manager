"""Markdown-to-HTML renderer compliant with html-validate, pa11y, and docs hooks."""

import html
import re
from dataclasses import dataclass

from env_manager.gui_docs.md_inline import format_inline, sec_id_from_heading

_H2_LEVEL = 2
_MIN_TABLE_ROWS = 2
_MATH_FENCE_LEN = 2
_TOC_HEADINGS = ("## table of contents", "# table of contents")
_TOF_MARKER = "### Table of Figures"
_LIST_ITEM_RE = re.compile(r"^(\s*)([-*]|\d+\.)\s+(.*)$")
_BLOCK_START = ("#", "```", "|", ">", "---", "$$", "![")
_FIGURE_RE = re.compile(r"^!\[([^\]]*)\]\(([^)\s]+)\)$")
_FIGURE_NUM_RE = re.compile(r"Figure\s+(\d+)")

_ALERT_MAP = {
    "NOTE": "callout-note",
    "TIP": "callout-tip",
    "IMPORTANT": "callout-important",
    "WARNING": "callout-warning",
    "CAUTION": "callout-warning",
}


@dataclass(frozen=True)
class HtmlPageConfig:
    """Metadata and navigation chrome for rendering a publication HTML page."""

    title: str
    subtitle: str
    canonical_url: str
    source_md_rel: str
    active_nav: str
    nav_links: tuple[tuple[str, str], ...]
    companion_links: tuple[tuple[str, str], ...] = ()


def _render_table(rows: list[str]) -> str:
    if len(rows) < _MIN_TABLE_ROWS:
        return "\n".join(f"<p>{format_inline(r)}</p>" for r in rows)
    headers = [c.strip() for c in rows[0].strip().strip("|").split("|")]
    body_rows = [
        [c.strip() for c in r.strip().strip("|").split("|")]
        for r in rows[2:]
        if r.strip()
    ]
    out = ["<table>", "<thead>", "<tr>"]
    for h in headers:
        out.append(f"<th>{format_inline(h)}</th>")
    out.extend(["</tr>", "</thead>", "<tbody>"])
    for cols in body_rows:
        out.append("<tr>")
        for idx in range(len(headers)):
            cell = cols[idx] if idx < len(cols) else ""
            out.append(f"<td>{format_inline(cell)}</td>")
        out.append("</tr>")
    out.extend(["</tbody>", "</table>"])
    return "\n".join(out)


def _render_list_items(items: list[tuple[int, bool, str]], start: int) -> tuple[str, int]:
    """Render list items sharing the indent of ``items[start]``, nesting deeper ones."""
    base_indent, ordered, _ = items[start]
    tag = "ol" if ordered else "ul"
    out: list[str] = [f"<{tag}>"]
    idx = start
    while idx < len(items) and items[idx][0] >= base_indent:
        indent, _, text = items[idx]
        if indent > base_indent:
            nested, idx = _render_list_items(items, idx)
            out[-1] = out[-1].removesuffix("</li>") + nested + "</li>"
            continue
        out.append(f"<li>{format_inline(text)}</li>")
        idx += 1
    out.append(f"</{tag}>")
    return "\n".join(out), idx


def _parse_list_items(lines: list[str]) -> list[tuple[int, bool, str]]:
    items: list[tuple[int, bool, str]] = []
    for line in lines:
        m = _LIST_ITEM_RE.match(line)
        if m:
            items.append((len(m.group(1)), m.group(2)[0].isdigit(), m.group(3).strip()))
        elif items:
            indent, ordered, text = items[-1]
            items[-1] = (indent, ordered, f"{text} {line.strip()}")
    return items


def _render_blockquote(lines: list[str]) -> str:
    stripped = [re.sub(r"^>\s?", "", ln) for ln in lines]
    first = stripped[0].strip() if stripped else ""
    alert_m = re.match(r"^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*(.*)$", first)
    if alert_m:
        kind = alert_m.group(1)
        css_cls = _ALERT_MAP.get(kind, "callout-note")
        rest = [alert_m.group(2), *stripped[1:]]
        body = " ".join(x.strip() for x in rest if x.strip())
        return (
            f'<div class="callout {css_cls}">\n'
            f"<p><strong>{kind}:</strong> {format_inline(body)}</p>\n"
            f"</div>"
        )
    body = " ".join(x.strip() for x in stripped if x.strip())
    return f"<blockquote><p>{format_inline(body)}</p></blockquote>"


def _extract_meta_and_body(md_text: str) -> tuple[str, list[str]]:
    lines = md_text.splitlines()
    idx = 0
    while idx < len(lines):
        st = lines[idx].strip()
        if st.startswith("# ") or st.lower().startswith("## category ") or not st:
            idx += 1
            continue
        break
    meta_lines: list[str] = []
    while idx < len(lines):
        st = lines[idx].strip()
        if st == "---":
            idx += 1
            break
        if st:
            meta_lines.append(st)
        idx += 1
    body_lines = lines[idx:]
    first = next((pos for pos, ln in enumerate(body_lines) if ln.strip()), None)
    if first is not None and body_lines[first].strip().lower() in _TOC_HEADINGS:
        for pos in range(first + 1, len(body_lines)):
            if body_lines[pos].strip() == "---":
                body_lines = body_lines[pos + 1 :]
                break
    return " ".join(meta_lines), body_lines


def _collect_toc(body_lines: list[str]) -> list[tuple[int, str, str]]:
    toc: list[tuple[int, str, str]] = []
    in_code = False
    seen_h2 = False
    for st in (ln.strip() for ln in body_lines):
        if st.startswith("```"):
            in_code = not in_code
        elif not in_code and (m := re.match(r"^(#{2,3})\s+(.+)$", st)) is not None:
            lvl = len(m.group(1))
            heading = m.group(2).strip()
            if heading.lower() == "table of contents" or (lvl > _H2_LEVEL and not seen_h2):
                continue
            seen_h2 = seen_h2 or lvl == _H2_LEVEL
            toc.append((lvl, heading, sec_id_from_heading(heading)))
    return toc


def _render_toc_nav(
    toc: list[tuple[int, str, str]], companion_links: tuple[tuple[str, str], ...]
) -> str:
    out = [
        '<nav class="toc-sidebar" aria-label="Table of Contents">',
        '<a href="../index.html" class="toc-brand">'
        '<img src="../assets/lemgenda-icon.svg" alt="LemGendary AI Documentation Hub"></a>',
        "<h4>Table of Contents</h4>",
        "<ul>",
        '<li><a href="../index.html" class="toc-link-accent">Back to Index</a></li>',
    ]
    for label, href in companion_links:
        out.append(
            f'<li><a href="{html.escape(href, quote=True)}" '
            f'class="toc-link-secondary">{html.escape(label)}</a></li>'
        )
    sub_open = False
    li_open = False
    for lvl, heading, sec_id in toc:
        if lvl == _H2_LEVEL:
            if sub_open:
                out.append("</ul></li>")
                sub_open = False
                li_open = False
            elif li_open:
                out.append("</li>")
                li_open = False
            out.append(f'<li><a href="#{sec_id}">{html.escape(heading)}</a>')
            li_open = True
        else:
            if not sub_open:
                out.append('<ul class="sub-item">')
                sub_open = True
            out.append(f'<li><a href="#{sec_id}">{html.escape(heading)}</a></li>')
    if sub_open:
        out.append("</ul></li>")
    elif li_open:
        out.append("</li>")
    out.extend(["</ul>", "</nav>"])
    return "\n".join(out)


def _consume_fenced_code(body_lines: list[str], start: int) -> tuple[str, int]:
    lang = body_lines[start].strip()[3:].strip()
    code_lines: list[str] = []
    idx = start + 1
    while idx < len(body_lines) and not body_lines[idx].strip().startswith("```"):
        code_lines.append(body_lines[idx])
        idx += 1
    cls = f' class="language-{html.escape(lang, quote=True)}"' if lang else ""
    return f"<pre><code{cls}>{html.escape(chr(10).join(code_lines))}</code></pre>", idx + 1


def _consume_display_math(body_lines: list[str], start: int) -> tuple[str, int]:
    st = body_lines[start].strip()
    math_lines = [body_lines[start]]
    idx = start + 1
    if not (st.endswith("$$") and len(st) > _MATH_FENCE_LEN):
        while idx < len(body_lines):
            math_lines.append(body_lines[idx])
            if body_lines[idx].strip().endswith("$$"):
                idx += 1
                break
            idx += 1
    return f"<p>{chr(10).join(math_lines)}</p>", idx


def _consume_list(body_lines: list[str], start: int) -> tuple[str, int]:
    collected: list[str] = []
    idx = start
    while idx < len(body_lines):
        line = body_lines[idx]
        st = line.strip()
        if not st or (st.startswith(_BLOCK_START) and not _LIST_ITEM_RE.match(line)):
            break
        collected.append(line)
        idx += 1
    html_out, _ = _render_list_items(_parse_list_items(collected), 0)
    return html_out, idx


def _consume_figure(body_lines: list[str], start: int) -> tuple[str, int] | None:
    """Parse a (possibly wrapped) image plus an immediately following italic caption."""
    joined = ""
    idx = start
    while idx < len(body_lines) and body_lines[idx].strip():
        joined = f"{joined} {body_lines[idx].strip()}".strip()
        idx += 1
        if joined.endswith(")"):
            break
    m = _FIGURE_RE.match(joined)
    if m is None:
        return None
    alt, src = m.group(1), m.group(2)
    caption_lines: list[str] = []
    if idx < len(body_lines) and body_lines[idx].strip().startswith("*"):
        while idx < len(body_lines) and body_lines[idx].strip():
            caption_lines.append(body_lines[idx].strip())
            idx += 1
    caption = " ".join(caption_lines).strip()
    if caption.startswith("*") and caption.endswith("*") and not caption.startswith("**"):
        caption = caption[1:-1]
    num_m = _FIGURE_NUM_RE.search(alt)
    fig_id = f' id="figure-{num_m.group(1)}"' if num_m else ""
    cap_html = format_inline(caption) if caption else html.escape(alt)
    return (
        f'<figure{fig_id} class="figure-center">'
        f'<img src="{html.escape(src, quote=True)}" alt="{html.escape(alt, quote=True)}"'
        f' class="img-paper" loading="lazy">'
        f"<figcaption>{cap_html}</figcaption></figure>",
        idx,
    )


def _consume_tof(body_lines: list[str], start: int) -> tuple[str, int]:
    """Render the Table of Figures marker plus its list as a styled navigation card."""
    idx = start + 1
    while idx < len(body_lines) and not body_lines[idx].strip():
        idx += 1
    list_html, idx = _consume_list(body_lines, idx)
    list_html = re.sub(
        r"<li>(<strong>Figure (\d+)\.</strong>.*?)</li>",
        r'<li><a href="#figure-\2">\1</a></li>',
        list_html,
    )
    list_html = list_html.replace("<ul>", '<ul class="tof-list">', 1)
    return (
        '<nav class="card tof-card" aria-label="Table of Figures">'
        '<h3 id="sec-table-of-figures" class="tof-title">Table of Figures</h3>'
        f"{list_html}</nav>",
        idx,
    )


def _consume_paragraph(body_lines: list[str], start: int) -> tuple[str, int]:
    para: list[str] = []
    idx = start
    while idx < len(body_lines):
        cur = body_lines[idx].strip()
        if not cur or cur.startswith(_BLOCK_START) or cur == _TOF_MARKER:
            break
        if re.match(r"^([-*]|\d+\.)\s+", cur):
            break
        para.append(cur)
        idx += 1
    return f"<p>{format_inline(' '.join(para))}</p>", idx


def _consume_next_block(body_lines: list[str], idx: int) -> tuple[str, int]:
    st = body_lines[idx].strip()
    if st.startswith("```"):
        result = _consume_fenced_code(body_lines, idx)
    elif st.startswith("$$"):
        result = _consume_display_math(body_lines, idx)
    elif st.startswith("|"):
        tbl: list[str] = []
        while idx < len(body_lines) and body_lines[idx].strip().startswith("|"):
            tbl.append(body_lines[idx])
            idx += 1
        result = (_render_table(tbl), idx)
    elif st.startswith(">"):
        bq: list[str] = []
        while idx < len(body_lines) and body_lines[idx].strip().startswith(">"):
            bq.append(body_lines[idx])
            idx += 1
        result = (_render_blockquote(bq), idx)
    elif re.match(r"^([-*]|\d+\.)\s+", st):
        result = _consume_list(body_lines, idx)
    elif st == _TOF_MARKER:
        result = _consume_tof(body_lines, idx)
    elif st.startswith("![") and (fig := _consume_figure(body_lines, idx)) is not None:
        result = fig
    else:
        result = _consume_paragraph(body_lines, idx)
    return result


def _render_body_sections(body_lines: list[str]) -> str:
    blocks: list[str] = []
    in_section = False
    idx = 0
    while idx < len(body_lines):
        st = body_lines[idx].strip()
        if not st or st == "---":
            idx += 1
            continue
        h_m = re.match(r"^(#{2,4})\s+(.+)$", st)
        if h_m:
            lvl = len(h_m.group(1))
            heading = h_m.group(2).strip()
            sec_id = sec_id_from_heading(heading)
            if lvl == _H2_LEVEL:
                if in_section:
                    blocks.append("</section>")
                blocks.append(f'<section id="{sec_id}">')
                in_section = True
                blocks.append(f"<h2>{format_inline(heading)}</h2>")
            else:
                blocks.append(f'<h{lvl} id="{sec_id}">{format_inline(heading)}</h{lvl}>')
            idx += 1
            continue
        rendered, idx = _consume_next_block(body_lines, idx)
        blocks.append(rendered)
    if in_section:
        blocks.append("</section>")
    return "\n".join(blocks)


def render_markdown_to_html(
    md_text: str, config: HtmlPageConfig, page_title: str = ""
) -> str:
    """Render a documentation Markdown paper into a compliant HTML5 publication."""
    meta_raw, body_lines = _extract_meta_and_body(md_text)
    toc = _collect_toc(body_lines)
    toc_html = _render_toc_nav(toc, config.companion_links)
    body_html = _render_body_sections(body_lines)
    desc = html.escape(f"{config.title} - {config.subtitle}", quote=True)
    title_esc = html.escape(config.title)
    page_title_esc = html.escape(page_title or config.title)
    sub_esc = html.escape(config.subtitle)
    canon_esc = html.escape(config.canonical_url, quote=True)
    tabs_html = "\n".join(
        f'<a href="{html.escape(href, quote=True)}" '
        f'class="tab-link{" active" if label == config.active_nav else ""}">'
        f"{html.escape(label)}</a>"
        for label, href in config.nav_links
    )
    meta_spans = f"<span>{format_inline(meta_raw)}</span>" if meta_raw else ""
    return "\n".join([
        "<!DOCTYPE html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="UTF-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">',
        f'<meta name="description" content="{desc}">',
        f'<link rel="canonical" href="{canon_esc}">',
        f'<meta property="og:title" content="{title_esc}">',
        f'<meta property="og:description" content="{desc}">',
        f'<meta property="og:url" content="{canon_esc}">',
        '<meta property="og:type" content="article">',
        '<meta name="twitter:card" content="summary">',
        f'<meta name="twitter:title" content="{title_esc}">',
        f'<meta name="twitter:description" content="{desc}">',
        f"<title>{page_title_esc}</title>",
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700'
        '&amp;family=Outfit:wght@500;600;700&amp;family=Fira+Code:wght@400;500&amp;display=swap"'
        ' rel="stylesheet">',
        '<link rel="stylesheet" href="../style.css">',
        '<script>window.MathJax = { tex: { inlineMath: [["$", "$"], ["\\\\(", "\\\\)"]] } };'
        "</script>",
        '<script id="MathJax-script" async '
        'src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>',
        "</head>",
        "<body>",
        '<a href="#main-content" class="skip-link">Skip to main content</a>',
        '<div class="container">',
        '<div class="tabs-nav">',
        tabs_html,
        "</div>",
        "<header>",
        f"<h1>{title_esc}</h1>",
        f'<p class="subtitle">{sub_esc}</p>',
        f'<div class="meta">{meta_spans}</div>',
        "</header>",
        '<main id="main-content">',
        toc_html,
        body_html,
        "</main>",
        "<footer>",
        "<p>LemGendary AI Documentation Hub. Source: "
        f"<code>{html.escape(config.source_md_rel)}</code>.</p>",
        "</footer>",
        "</div>",
        "</body>",
        "</html>",
        "",
    ])
