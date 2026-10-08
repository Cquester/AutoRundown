"""HTML -> normalized text.

The archive stores manuals as real HTML, so spec tables arrive already
structured: no OCR, no table detection, no bounding boxes. Tables are the
whole ballgame for torque specs, so they are rendered as pipe tables that
keep row/column association intact for the model.
"""
from __future__ import annotations

import re

from lxml import html as lhtml

_PARSER = lhtml.HTMLParser(encoding="utf-8")

BLOCK_TAGS = {
    "div", "p", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
    "br", "tr", "section", "article", "blockquote", "pre",
}
SKIP_TAGS = {"script", "style", "noscript"}

_WS = re.compile("[ 	 ]+")
_ALLWS = re.compile(r"\s+")
_BLANKS = re.compile(r"\n{3,}")


def image_ref(src: str) -> str | None:
    """Path of a diagram relative to the archive's images/ directory.

    Two traps here, both invisible on a flat archive:
    - images may be nested (`../images/IMP49Q313/euro210/551.png`), so taking
      the basename loses the directories and the file cannot be found again;
    - UI chrome (`../icons/wrench.svg`) is also an <img>, and is not a
      diagram. Anything outside images/ is rejected.
    """
    if not src:
        return None
    cleaned = src.replace("\\", "/").split("?")[0]
    marker = "images/"
    idx = cleaned.find(marker)
    if idx == -1:
        return None
    ref = cleaned[idx + len(marker):].strip("/")
    return ref or None


LINK_RE = re.compile(r"(?:^|/)pages/(\d+)\.html")
# "::" rather than "|" so a marker inside a table cell cannot break the
# pipe-table rendering downstream
LINK_MARK = "[[ref:{page}::{label}]]"


def link_ref(href: str) -> str | None:
    """Page number a cross-reference points at, or None if it leaves the manual.

    Manual text is full of "Refer to: Interior Lamps" where the name is a link
    to another page. Dropping the href leaves a dead reference the reader can
    see but not follow, which is exactly the connector/schematic trail people
    need when diagnosing.
    """
    if not href:
        return None
    m = LINK_RE.search(href.replace("\\", "/").split("#")[0])
    return m.group(1) if m else None


def _emit_anchor(node, parts: list[str], links: list[str] | None) -> None:
    """Render an <a> as a marker the UI can turn back into a link."""
    page = link_ref(node.get("href") or "")
    label = _ALLWS.sub(" ", node.text_content() or "").strip()
    if page and label:
        if links is not None:
            links.append(f"{page}::{label}")
        parts.append(LINK_MARK.format(page=page, label=label))
    elif label:
        parts.append(label)  # external or dead link: keep the words


def _inline_text(el, links: list[str] | None = None) -> str:
    """Flatten an element to a single line (used for table cells).

    Recursive rather than `el.iter()` so an <a> subtree can be consumed whole;
    iterating would visit the anchor's children again and duplicate its text.
    """
    parts: list[str] = []

    def rec(node) -> None:
        if not isinstance(node.tag, str) or node.tag in SKIP_TAGS:
            pass
        elif node.tag == "img":
            ref = image_ref(node.get("src") or "")
            if ref:
                parts.append(f"[diagram: {ref}]")
        elif node.tag == "a":
            _emit_anchor(node, parts, links)
        else:
            if node.text:
                parts.append(node.text)
            for child in node:
                rec(child)
        if node.tail:
            parts.append(node.tail)

    if el.text:
        parts.append(el.text)
    for child in el:
        rec(child)
    return _ALLWS.sub(" ", " ".join(parts)).strip()


def _render_table(table, links: list[str] | None = None) -> str:
    """Render an HTML table as a pipe table, preserving header association."""
    rows: list[list[str]] = []
    header: list[str] = []
    for tr in table.iter("tr"):
        cells = [c for c in tr if isinstance(c.tag, str) and c.tag in ("td", "th")]
        if not cells:
            continue
        values = []
        for c in cells:
            text = _inline_text(c, links)
            span = c.get("colspan")
            values.append(text)
            # a colspan cell repeats so columns stay aligned for the reader
            if span and span.isdigit():
                values.extend([""] * (int(span) - 1))
        is_header = all(c.tag == "th" for c in cells)
        if is_header and not header:
            header = values
        else:
            rows.append(values)

    if not header and not rows:
        return ""
    # 1x1 tables are layout wrappers (typically around a diagram), not data
    if not header and len(rows) == 1 and len(rows[0]) == 1:
        return rows[0][0]
    width = max([len(header)] + [len(r) for r in rows]) if (header or rows) else 0
    if not header:
        header = [""] * width

    def line(vals: list[str]) -> str:
        padded = vals + [""] * (width - len(vals))
        return "| " + " | ".join(v.replace("|", "/") for v in padded) + " |"

    out = [line(header), "|" + "---|" * width]
    out.extend(line(r) for r in rows)
    return "\n".join(out)


def _walk(el, out: list[str], images: list[str],
          links: list[str]) -> None:
    tag = el.tag
    if not isinstance(tag, str):  # comments, processing instructions
        if el.tail:
            out.append(el.tail)
        return

    if tag in SKIP_TAGS:
        pass
    elif tag == "table":
        rendered = _render_table(el, links)
        if rendered:
            out.append("\n\n" + rendered + "\n\n")
        for img in el.iter("img"):
            ref = image_ref(img.get("src") or "")
            if ref:
                images.append(ref)
    elif tag == "a":
        _emit_anchor(el, out, links)
    elif tag == "img":
        ref = image_ref(el.get("src") or "")
        if ref:
            images.append(ref)
            out.append(f"\n[diagram: {ref}]\n")
    else:
        block = tag in BLOCK_TAGS
        if block:
            out.append("\n")
        if el.text:
            out.append(el.text)
        for child in el:
            _walk(child, out, images, links)
        if block:
            out.append("\n")

    if el.tail:
        out.append(el.tail)


def element_to_text(el) -> tuple[str, list[str], list[str]]:
    """Return (normalized text, image refs, cross-reference links).

    Links come back as "pageno::label" so the UI can offer the reader the
    same trail the original page had.
    """
    out: list[str] = []
    images: list[str] = []
    links: list[str] = []
    _walk(el, out, images, links)
    raw = "".join(out)
    lines = [_WS.sub(" ", ln).strip() for ln in raw.split("\n")]
    text = _BLANKS.sub("\n\n", "\n".join(lines)).strip()
    # de-dupe both lists, preserving order
    seen: set[str] = set()
    unique = [i for i in images if not (i in seen or seen.add(i))]
    seen_l: set[str] = set()
    uniq_links = [l for l in links if not (l in seen_l or seen_l.add(l))]
    return text, unique, uniq_links


def parse_file(path):
    """Parse an HTML file into an lxml tree, tolerating malformed markup."""
    with open(path, "rb") as fh:
        data = fh.read()
    if not data.strip():
        return None
    return lhtml.fromstring(data, parser=_PARSER)
