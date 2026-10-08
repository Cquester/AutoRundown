"""Draw a Mercedes WIS document roughly as WIS lays it out (PROTOTYPE).

    python tools/sids_render.py <dok_id> [--out page.html] [--base http://localhost:8756]

WIS stores each document as SIDS, a page-layout format: blocks stacked down
the page ("V" ... "W"), each with absolutely placed frames ("V,,,x,y,w,h"),
ruled boxes ("G" lines, "GP" polygons, "GA" arcs), paragraphs of text runs in
numbered fonts ("F95 SWISS 10"), and picture slots anchored at block
coordinates (".P.<graphic>|w_cm|h_cm|TIFF" with "A,,x,y"). Units are 1/567 cm.
The site shows the same text in reading order; this redraws the layout.
Approximate by design: WIS's own viewer is the authority.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = ROOT.parent / "Mercedes Documents" / "autorundown_out" / "wis_extract"
EXTRACTOR = ROOT / "extractor"
sys.path.insert(0, str(EXTRACTOR))
from parse_sids import split_records, split_fields, decompress_body  # noqa: E402

SCALE = 0.085          # px per SIDS unit (9240 units of text width -> ~785 px)
CM = 567               # SIDS units per cm
GFX = re.compile(r"\.P\.([A-Za-z0-9]+)\|([\d.]+)\|([\d.]+)\|")
FONTS = {"87": (8, 400), "95": (10, 400), "96": (10, 700), "104": (12, 700),
         "112": (18, 700), "10": (12, 400)}


def num(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


class Box:
    def __init__(self, x=0, y=0, w=None, h=None, kind="block"):
        self.x, self.y, self.w, self.h, self.kind = x, y, w, h, kind
        self.children: list = []       # ("p", html) | ("box", Box) | ("line", ...) | ("img", ...)
        self.para: list[str] = []
        self.anchor = None             # (x, y) for the next picture
        self.mode = None               # "label" / "text": a two-column step row
        self.label = None

    def flush(self):
        text = "".join(self.para).strip()
        self.para = []
        if self.mode == "label":
            self.label = text
            return
        if self.mode == "text" and self.label is not None:
            self.children.append(("row", self.label, text))
            self.label = None
            return
        if text:
            self.children.append(("p", text))


def render(dok_id: int, base: str) -> str:
    import parse                      # the extractor's reader for bodies.pack
    blobs = parse.read_pack(str(EXTRACT / "bodies.pack"))
    raw = decompress_body(blobs[dok_id]).decode("latin-1")
    rec = None
    with (EXTRACT / "records.jsonl").open(encoding="utf-8") as f:
        for line in f:
            if line.startswith(f'{{"dok_id": {dok_id},'):
                rec = json.loads(line)
                break
    files = {}
    if rec:
        catalog = {}
        with (EXTRACT / "images" / "catalog.tsv").open(encoding="utf-8") as f:
            next(f)
            for l in f:
                p = l.split("\t")
                if len(p) > 2 and p[2]:
                    catalog[(p[0], p[1])] = p[2]
        for g in rec.get("figures") or []:
            k = (str(g["cluster"]), str(g["graphik_id"]))
            if k in catalog:
                files[g["graphik_nr"].lower()] = catalog[k]
    doc_vehicle = "mercedes-wis-virtualbox-en-201"   # any WIS vehicle serves images

    page = Box(kind="page")
    stack = [page]
    font = ("95", (10, 400))
    for r in split_records(raw):
        r = r.strip()
        if not r or r.startswith("ASCII-SIDS") or r.startswith("F") and '"' in r and r[1:2].isdigit() is False and r[:2] in ("F,", "F1", "F8", "F9"):
            continue
        fields, quoted = split_fields(r)
        lead = fields[0].strip()
        cur = stack[-1]
        if lead == "V":
            nums = [num(f) for f in fields[1:]]
            w, h = (nums[4] if len(nums) > 4 else None), (nums[5] if len(nums) > 5 else None)
            if w or h:
                b = Box(nums[2] or 0, nums[3] or 0, w, h, "frame")
            else:
                b = Box(kind="block")
            cur.flush()
            cur.children.append(("box", b))
            stack.append(b)
            continue
        if lead == "W":
            cur.flush()
            if len(stack) > 1:
                stack.pop()
            continue
        if lead == "E":
            break
        if lead in ("G", "GP", "GA"):
            # coordinates are "(40,4400),(120,120)" -- commas inside the
            # parentheses, so read them from the raw record, not the fields
            groups = re.findall(r"\(([^)]*)\)", r)
            xs = [num(v) for v in re.findall(r"-?\d+", groups[0])] if len(groups) > 0 else []
            ys = [num(v) for v in re.findall(r"-?\d+", groups[1])] if len(groups) > 1 else []
            pts = list(zip(xs, ys))
            if len(pts) >= 2:
                cur.children.append(("line", pts, lead == "GP"))
            continue
        if lead.startswith("P"):
            cur.flush()
            # "PC,T,4880" opens a step row's left column (the part), the
            # next "PC" its right column (what to do with it)
            if lead == "PC" and len(fields) > 1 and fields[1].strip() == "T":
                new = "label"
            elif lead == "PC" and cur.mode == "label":
                new = "text"
            else:
                new = None
            # a left column with no right column ("Preceding work:" followed
            # by an indented paragraph) is still text -- don't drop it
            if cur.label and new != "text":
                cur.children.append(("p", cur.label))
                cur.label = None
            cur.mode = new
            if "A" in fields and fields.index("A") + 3 < len(fields) + 1:
                i = fields.index("A")
                ax, ay = num(fields[i + 2]) if i + 2 < len(fields) else 0, num(fields[i + 3]) if i + 3 < len(fields) else 0
                cur.anchor = (ax or 0, ay or 0)
            continue
        if lead in ("L",) or (lead == "T" and not quoted):
            cur.para.append("<br>")
            continue
        if lead.isdigit() and len(fields) > 1 and fields[1].strip():
            fid = fields[1].strip()
            font = (fid, FONTS.get(fid, (10, 400)))
        for q in quoted:
            m = GFX.search(q)
            if m:
                nr = m.group(1).lower()
                f = files.get(nr)
                ax, ay = cur.anchor or (0, 0)
                cur.anchor = None
                # the anchor is in block coordinates even when the picture is
                # declared inside a caption frame: hang it on the block
                cur = next((b for b in reversed(stack) if b.kind in ("block", "page")), cur)
                cur.children.append(("img", ax, ay, float(m.group(2)) * CM, float(m.group(3)) * CM,
                                     embed(f) if f else None, nr))
                cur.anchor = None
                continue
            if font[0] in ("173",):
                continue
            if font[0] == "182":
                cur.para.append('<span class="warn">&#9888;</span> ')
                continue
            size, weight = font[1]
            cur.para.append(f'<span style="font-size:{size}pt;font-weight:{weight}">{html.escape(q)}</span>')
    page.flush()
    return HTML.format(title=html.escape(rec["title"] if rec else str(dok_id)),
                       body=draw(page))


def embed(rel: str) -> str:
    """The converted graphic as a data: URI, so the page stands alone."""
    import base64
    path = EXTRACT / "images" / rel
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".gif": "image/gif",
            ".svg": "image/svg+xml"}.get(path.suffix, "application/octet-stream")
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode()


def draw(box: Box) -> str:
    out = []
    if box.kind == "page":
        out.append('<div class="page">')
    elif box.kind == "block":
        # height: the furthest thing drawn in it, else let text flow
        ext = [0.0]
        for c in box.children:
            if c[0] == "line":
                ext += [y for _, y in c[1] if y is not None]
            elif c[0] == "box" and c[1].kind == "frame" and c[1].h:
                ext.append((c[1].y or 0) + c[1].h)
            elif c[0] == "img":
                ext.append(c[2] + c[4])
        h = max(ext) * SCALE
        style = f"position:relative;min-height:{h:.0f}px"
        out.append(f'<div class="block" style="{style}">')
        svg = [f'<svg class="rules" width="100%" height="{h:.0f}">']
        for c in box.children:
            if c[0] == "line":
                pts = " ".join(f"{x * SCALE:.1f},{y * SCALE:.1f}" for x, y in c[1])
                svg.append(f'<polyline points="{pts}" fill="{"#fff" if c[2] else "none"}" stroke="#222" stroke-width="1"/>')
        svg.append("</svg>")
        out.append("".join(svg))
    else:
        style = (f"position:absolute;left:{box.x * SCALE:.0f}px;top:{box.y * SCALE:.0f}px;"
                 f"width:{(box.w or 9240) * SCALE:.0f}px")
        out.append(f'<div class="frame" style="{style}">')
    flow = []
    for c in box.children:
        if c[0] == "p":
            flow.append(f'<p>{c[1]}</p>')
        elif c[0] == "row":
            flow.append(f'<div class="steprow"><div class="lab">{c[1]}</div><div>{c[2]}</div></div>')
        elif c[0] == "box":
            flow.append(draw(c[1]))
        elif c[0] == "img":
            _, x, y, w, h, src, nr = c
            tag = (f'<img src="{src}" alt="{nr}">' if src else f'<div class="missing">{nr}</div>')
            flow.append(f'<div class="pic" style="left:{x * SCALE:.0f}px;top:{y * SCALE:.0f}px;'
                        f'width:{w * SCALE:.0f}px;height:{h * SCALE:.0f}px">{tag}</div>')
    out.append("".join(flow))
    out.append("</div>")
    return "".join(out)


HTML = """<!doctype html><html><head><meta charset="utf-8"><title>{title}</title>
<style>
body{{background:#e9e9e9;margin:0;padding:20px;font-family:Arial,Helvetica,sans-serif;color:#111}}
.sheet{{background:#fff;width:820px;margin:0 auto;padding:30px 24px;box-shadow:0 2px 12px #0003}}
.block{{margin:6px 0}}
.rules{{position:absolute;left:0;top:0;pointer-events:none;overflow:visible}}
.frame p{{margin:2px 4px}}
.block>p{{margin:4px 0}}
.pic{{position:absolute}}
.pic img{{width:100%;height:100%;object-fit:contain}}
.missing{{width:100%;height:100%;display:flex;align-items:center;justify-content:center;color:#999;font-size:11px}}
.warn{{color:#c00;font-size:13pt}}
.steprow{{display:flex;gap:10px;border-bottom:1px solid #ddd;padding:3px 0;font-size:10pt}}
.steprow .lab{{flex:0 0 42%}}
</style></head><body><div class="sheet">{body}</div></body></html>"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dok_id", type=int)
    ap.add_argument("--out", default="")
    ap.add_argument("--base", default="http://localhost:8756")
    a = ap.parse_args(argv)
    out = Path(a.out or f"wis_{a.dok_id}.html")
    out.write_text(render(a.dok_id, a.base), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
