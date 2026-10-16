"""QR codes as inline SVG, made on this PC (segno: pure Python, no Pillow).

Two uses in Shop setup > Phones & mechanics: the site's own address (open
AutoRundown on a phone), and a mechanic's one-time sign-in link. Nothing is
fetched from anywhere; the shop network may have no internet.
"""
from __future__ import annotations

import segno


def svg(text: str, scale: int = 1) -> str:
    """An <svg> element for `text`. Error correction M: readable off a
    monitor at an angle; dark on white whatever the page's theme, as camera
    apps expect. No XML declaration or namespace clutter, so it can be put
    straight into the page (the text is a URL this server built, never
    anything typed by a phone)."""
    code = segno.make(text, error="m", micro=False)
    return code.svg_inline(scale=scale, border=4, dark="#000", light="#fff", omitsize=True)
