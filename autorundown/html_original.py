"""A LEMON (or folder) HTML page as the manual printed it -- the "Original
layout" view for manuals that are HTML already.

Built 2026-10-24 at the user's request ("I dont think the way we have reading
view looks good. Lets default to original document"). The page's own HTML is
LEMON's website: a header, breadcrumbs to pages that are not in the archive,
its stylesheet and script, a footer. Only the manual's part (`div.main`) is
kept, and only tags and attributes on an allow-list -- the archive is a
download, never trusted as markup:

* no script, style, form, frame, object, meta or link element, and no event
  handler, `style` (except a highlight box's position), `id` or `src` of
  its own: pictures are re-pointed at /api/image the way the reading view
  shows them (extract.image_ref: inside images/ only, so UI icons drop out);
* a link to another manual page becomes `data-page`, which the frame's own
  script hands to the app (postMessage) to open; any other link is its text;
* an unknown tag is dropped and its text kept.

The server's CSP is the second guard: only this module's own script runs
(a nonce per response), pictures and styles only from this site, no forms,
plugins or base URL. Not an iframe sandbox: a sandboxed frame is its own
origin, and a browser may block its picture requests (the app's browser
pane did, 2026-10-24). The frame reports its height like wis_original's
sheet, so it never scrolls inside the pane.
"""
from __future__ import annotations

import re
from html import escape
from pathlib import Path

import lxml.html

from .extract import image_ref, link_ref


class NotAvailable(Exception):
    """This page has no HTML to show."""


KEEP = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "span", "br", "hr",
        "b", "strong", "i", "em", "u", "sub", "sup", "small", "pre", "code",
        "ul", "ol", "li", "dl", "dt", "dd", "table", "thead", "tbody", "tfoot",
        "tr", "td", "th", "caption", "colgroup", "col", "img", "a",
        "figure", "figcaption", "blockquote"}
DROP = {"script", "style", "noscript", "iframe", "frame", "frameset", "object",
        "embed", "form", "input", "button", "select", "textarea", "link", "meta",
        "base", "svg", "math", "template", "audio", "video", "source", "canvas"}
_CLASS = re.compile(r"^[A-Za-z0-9_ -]{1,200}$")
_SPAN = re.compile(r"^\d{1,3}$")
# a highlight box (LEMON's .oxe-region) is placed by its inline position
_POS = re.compile(r"\b(left|top|width|height)\s*:\s*(-?\d{1,5}(?:\.\d+)?(?:px|%))")


def _clean(el, img_url) -> None:
    for child in list(el):
        if not isinstance(child.tag, str):          # comments, processing instructions
            child.drop_tree()
            continue
        tag = child.tag.lower()
        if tag in DROP:
            child.drop_tree()
            continue
        _clean(child, img_url)
        if tag not in KEEP:
            child.drop_tag()
            continue
        attrs = dict(child.attrib)
        child.attrib.clear()
        cls = attrs.get("class", "")
        if cls and _CLASS.match(cls):
            child.set("class", cls)
        for a in ("colspan", "rowspan"):
            if _SPAN.match(attrs.get(a, "")):
                child.set(a, attrs[a])
        if "oxe-region" in cls.split():
            pos = "; ".join(f"{k}:{v}" for k, v in _POS.findall(attrs.get("style", "")))
            if pos:
                child.set("style", pos)
        if tag == "img":
            ref = image_ref(attrs.get("src", ""))
            if not ref:
                child.drop_tree()                   # UI icons, outside images/
                continue
            child.set("src", img_url(ref))
            child.set("alt", attrs.get("alt", "")[:200])
            child.set("loading", "lazy")
        elif tag == "a":
            page = link_ref(attrs.get("href", ""))
            if page:
                child.set("href", "#")
                child.set("data-page", page)
            elif not (child.text_content() or "").strip():
                child.drop_tree()                   # empty anchors (name=...)
            else:
                child.drop_tag()


def render(page_file: Path, root: Path, title: str, img_url, nonce: str = "") -> str:
    """The manual part of `page_file` (inside `root`, the manual's folder).
    `nonce` marks the frame's own script for the server's CSP: no other
    script on the page can run."""
    try:
        path = page_file.resolve()
        path.relative_to(root.resolve())
    except (OSError, ValueError):
        raise NotAvailable("The page is outside its manual's folder.")
    if path.suffix.lower() not in (".html", ".htm") or not path.is_file():
        raise NotAvailable(f"No HTML page at {page_file}.")
    raw = path.read_bytes()
    if not raw.strip():
        raise NotAvailable("The page is empty.")
    doc = lxml.html.document_fromstring(raw)
    main = next(iter(doc.xpath("//div[contains(concat(' ', normalize-space(@class), ' '), ' main ')]")),
                None)
    if main is None:
        main = doc.find("body")
    if main is None:
        raise NotAvailable("The page has no body.")
    main.attrib.clear()
    _clean(main, img_url)
    body = lxml.html.tostring(main, encoding="unicode", method="html")
    return HTML.format(title=escape(title or ""), body=body, nonce=escape(nonce))


# LEMON's own look for the manual part (its style.css: tables, figures,
# indentation, notes), without the site around it.
HTML = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
html,body{{margin:0;background:#fff}}
body{{font-family:Arial,Helvetica,sans-serif;color:#111;font-size:16px;line-height:1.4}}
.sheet{{padding:10px 14px 16px;max-width:60rem}}
h1{{font-size:1.35em;margin:.2em 0 .6em}} h2{{font-size:1.2em}} h3{{font-size:1.08em}}
p{{margin:.45em 0}}
table{{border-collapse:collapse;margin:.6em 0;max-width:100%}}
td,th{{border:1px solid #333;padding:.35rem .5rem;vertical-align:top}}
th{{background:#f0f0f0}}
img{{max-width:100%;height:auto}}
.imageHolder{{margin:.8em 0}}
.imageCaption{{font-weight:700;display:block;margin-bottom:.3em}}
.imageCourtesyNote{{display:block;color:#666;font-size:.8em}}
.oxe-image{{position:relative;display:inline-block}}
.oxe-region{{position:absolute;display:block;background:rgba(25,122,250,.3)}}
.clsEmphBOLD{{font-weight:700}} .NOTE{{font-style:italic}}
.other-warning{{background:#fff3cd;border:1px solid #e0c060;padding:.5em .7em;margin:.5em 0}}
[class*=indent-]{{white-space:pre-wrap}}
a[data-page]{{color:#1a5fb4}}
</style></head><body><div class="sheet">{body}</div>
<script nonce="{nonce}">
(function(){{
  var s=document.querySelector('.sheet');
  function fit(){{
    if(window.parent!==window)window.parent.postMessage({{wisOriginalHeight:Math.ceil(s.getBoundingClientRect().bottom+scrollY+6)}},'*');
  }}
  addEventListener('resize',fit);addEventListener('load',fit);fit();
  if(window.ResizeObserver)new ResizeObserver(fit).observe(s);
  document.addEventListener('click',function(e){{
    var a=e.target.closest('a[data-page]');
    if(!a)return;
    e.preventDefault();
    if(window.parent!==window)window.parent.postMessage({{openPage:a.getAttribute('data-page')}},'*');
  }});
}})();
</script></body></html>"""
