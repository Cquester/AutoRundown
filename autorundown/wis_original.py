"""Draw a Mercedes WIS document roughly as WIS lays it out ("Original layout").

WIS stores each document as SIDS, a page-layout format: blocks stacked down
the page ("V" ... "W"), each with absolutely placed frames ("V,,,x,y,w,h"),
ruled boxes ("G" lines, "GP" polygons, "GA" arcs), paragraphs of text runs in
numbered fonts ("F95 SWISS 10"), and picture slots anchored at block
coordinates (".P.<graphic>|w_cm|h_cm|TIFF" with "A,,x,y"). Units are 1/567 cm.
The reading view shows the same text in reading order; this redraws the
layout. Approximate by design: WIS's own viewer is the authority.

The raw SIDS is in the corpus's `bodies.pack` (468 MB: 8-byte header
DokID/length, then the gzip'd body). `PackIndex` keeps a sidecar SQLite file
next to it -- DokID -> offset/length, plus each document's graphic number ->
converted image file (from figures.tsv + images/catalog.tsv) -- so a request
reads one body with one seek instead of loading the pack. The sidecar is
rebuilt automatically when any of its three inputs changes (size + mtime).

Self-contained: the SIDS tokenizer is copied from the extractor's
parse_sids.py so the server never imports from the extractor folder.
"""
from __future__ import annotations

import gzip
import html
import os
import re
import sqlite3
import struct
import threading
from pathlib import Path

SIDECAR = "bodies.pack.idx.sqlite"
SCALE = 0.085          # px per SIDS unit (9240 units of portrait text width -> 785 px)
TEXT_W = 9240          # SIDS units: a portrait page's text width, the minimum drawn
PAD = 12               # px of white around the drawn area
CM = 567               # SIDS units per cm
PT = 20                # SIDS units per typographic point (567 / 28.35)
GFX = re.compile(r"\.P\.([A-Za-z0-9]+)\|([\d.]+)\|([\d.]+)\|")
# a cross-reference run: ".T.1;BA35.20-P-1000-02A;" or a tool number ".T.1;*000589403700;"
XREF = re.compile(r"^\s*\.T\.\d+;\*?([^;]*);?\s*$")
INLINE_CTL = re.compile(r"\.T\.\d+;")

# Mercedes' icon fonts. Each LOGO font is its own glyph set (LOGO1C357 is
# test-equipment symbols: lamp ON/OFF, multimeter, ...), so glyphs are keyed
# by (font, letter). Only glyphs whose meaning is clear from the text that
# follows them in a corpus sample are drawn; the rest show as a neutral
# placeholder rather than a guessed icon.
LOGO_NOTE = ("&#9432;", "Note", "note")
LOGO = {("LOGO1C357", "M"): LOGO_NOTE,             # "Anti-twist lock must engage ..."
        ("LOGO1C357", "h"): ("&#9889;", "Hazardous voltage", "warn")}
LOGO.update({("LOGO1C360", k): v for k, v in {
        "a": ("&#9888;", "Danger", "warn"),          # followed by "Danger!"
        "i": LOGO_NOTE,                              # "Notes on AGM battery"
        "p": LOGO_NOTE,                              # "Notes on carrying out repair work"
        "n": ("&#9656;", "See document", "ref"),     # before a referenced document
        "j": ("&#9656;", "See document", "ref"),
        "l": ("&#9874;", "Special tool", "ref"),     # before a tool number
        "k": ("&#9874;", "Workshop equipment", "ref")}.items()})
# GREEK is laid out like the Windows Symbol font ("4.5 W" = 4.5 ohms)
GREEK = dict(zip("abgdezhqiklmnxoprstufcywABGDEZHQIKLMNXOPRSTUFCYW",
                 "αβγδεζηθικλμνξοπρστυφχψωΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ"))


# -- SIDS tokenizer (from the extractor's parse_sids.py) -----------------------

def split_records(text: str) -> list[str]:
    """Split SIDS into records on ';' honoring double-quoted strings."""
    recs, buf, inq, i, n = [], [], False, 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            if inq and i + 1 < n and text[i + 1] == '"':
                buf.append('"'); i += 2; continue
            inq = not inq; buf.append(c); i += 1; continue
        if c == ';' and not inq:
            recs.append(''.join(buf)); buf = []; i += 1; continue
        buf.append(c); i += 1
    if buf:
        recs.append(''.join(buf))
    return recs


def split_fields(rec: str) -> tuple[list[str], list[str]]:
    """Comma-split a record honoring quotes; return (fields, quoted_strings)."""
    fields, quoted, buf, inq, i, n = [], [], [], False, 0, len(rec)
    cur_q = None
    while i < n:
        c = rec[i]
        if c == '"':
            if inq and i + 1 < n and rec[i + 1] == '"':
                cur_q.append('"'); buf.append('"'); i += 2; continue
            if not inq:
                inq = True; cur_q = []
            else:
                inq = False; quoted.append(''.join(cur_q)); cur_q = None
            buf.append(c); i += 1; continue
        if c == ',' and not inq:
            fields.append(''.join(buf)); buf = []; i += 1; continue
        if inq:
            cur_q.append(c)
        buf.append(c); i += 1
    fields.append(''.join(buf))
    return fields, quoted


# -- offset index ---------------------------------------------------------------

class NotAvailable(Exception):
    """The document has no SIDS layout to draw (or the corpus is missing)."""


def _stamp(p: Path) -> str:
    try:
        st = p.stat()
    except OSError:
        return "missing"
    return f"{st.st_size}:{int(st.st_mtime)}"


class PackIndex:
    """DokID -> (offset, length) in bodies.pack, and graphic nr -> image file."""

    _lock = threading.Lock()

    def __init__(self, corpus: Path):
        self.corpus = Path(corpus)
        self.pack = self.corpus / "bodies.pack"
        self.path = self.corpus / SIDECAR

    def _inputs(self) -> str:
        return "|".join(_stamp(p) for p in (
            self.pack, self.corpus / "figures.tsv",
            self.corpus / "images" / "catalog.tsv"))

    def _fresh(self) -> bool:
        if not self.path.is_file():
            return False
        try:
            with sqlite3.connect(self.path) as c:
                row = c.execute("SELECT v FROM meta WHERE k='inputs'").fetchone()
        except sqlite3.Error:
            return False
        return bool(row) and row[0] == self._inputs()

    def ensure(self) -> None:
        if not self.pack.is_file():
            raise NotAvailable(f"no bodies.pack in {self.corpus}")
        if self._fresh():
            return
        with self._lock:
            if not self._fresh():
                self.build()

    def build(self) -> None:
        """Scan headers only (one seek per document) and write the sidecar.

        Written to a temp file and renamed, so a reader never sees half of it.
        """
        tmp = self.path.with_suffix(".tmp")
        tmp.unlink(missing_ok=True)
        c = sqlite3.connect(tmp)
        c.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
        c.execute("CREATE TABLE body (dok_id INTEGER PRIMARY KEY, off INTEGER, len INTEGER)")
        c.execute("CREATE TABLE fig (dok_id INTEGER, nr TEXT, file TEXT, PRIMARY KEY (dok_id, nr))")
        rows = []
        size = self.pack.stat().st_size
        with self.pack.open("rb") as f:
            off = 0
            while off + 8 <= size:
                f.seek(off)
                dok, ln = struct.unpack(">ii", f.read(8))
                if off + 8 + ln > size:
                    break                       # truncated tail: stop, like read_pack
                rows.append((dok, off + 8, ln))
                off += 8 + ln
        c.executemany("INSERT OR REPLACE INTO body VALUES (?,?,?)", rows)

        catalog = {}
        cat = self.corpus / "images" / "catalog.tsv"
        if cat.is_file():
            with cat.open(encoding="utf-8") as f:
                next(f, None)
                for line in f:
                    p = line.split("\t")
                    if len(p) > 2 and p[2]:
                        catalog[(p[0], p[1])] = p[2]
        figs = []
        ft = self.corpus / "figures.tsv"
        if ft.is_file():
            with ft.open(encoding="utf-8") as f:
                next(f, None)                   # DokID GraphikID GraphikNr Klasse Cluster
                for line in f:
                    p = line.rstrip("\n").split("\t")
                    if len(p) >= 5:
                        file = catalog.get((p[4], p[1]))
                        if file:
                            figs.append((int(p[0]), p[2].lower(), file))
        c.executemany("INSERT OR REPLACE INTO fig VALUES (?,?,?)", figs)
        c.execute("INSERT INTO meta VALUES ('inputs', ?)", (self._inputs(),))
        c.commit()
        c.close()
        os.replace(tmp, self.path)

    def body(self, dok_id: int) -> bytes:
        self.ensure()
        with sqlite3.connect(self.path) as c:
            row = c.execute("SELECT off, len FROM body WHERE dok_id=?", (dok_id,)).fetchone()
        if not row:
            raise NotAvailable(f"document {dok_id} is not in bodies.pack")
        with self.pack.open("rb") as f:
            f.seek(row[0])
            return f.read(row[1])

    def figures(self, dok_id: int) -> dict[str, str]:
        self.ensure()
        with sqlite3.connect(self.path) as c:
            return dict(c.execute("SELECT nr, file FROM fig WHERE dok_id=?", (dok_id,)))


def sids_text(blob: bytes) -> str:
    """Only gzip'd bodies are SIDS; LZW bodies are XML, others RTF stubs.

    The bytes are UTF-8 (an en dash is E2 80 93); read as Latin-1 they put
    "Nm â 30" on the page. A rare non-UTF-8 body falls back to cp1252.
    """
    if blob[:2] != b"\x1f\x8b":
        raise NotAvailable("this document has no SIDS layout")
    raw = gzip.decompress(blob)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
    if not text.lstrip().startswith("ASCII-SIDS"):
        raise NotAvailable("this document has no SIDS layout")
    return text


# -- layout -----------------------------------------------------------------------

def num(s: str) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def field(fields: list[str], i: int) -> str:
    return fields[i].strip() if i < len(fields) else ""


def font_table(raw: str) -> dict[str, tuple[str, float, bool]]:
    """`F95,,"SWISS 10";` -> {"95": ("SWISS", 10, False)}; `F,,` is font 0."""
    fonts = {}
    for m in re.finditer(r'(?m)^F(\d*),,"([^"]*)"', raw):
        parts = m.group(2).split()
        if not parts:
            continue
        size = next((float(p) for p in parts[1:] if p.replace(".", "").isdigit()), 10.0)
        fonts[m.group(1) or "0"] = (parts[0].upper(), size, "BOLD" in parts)
    return fonts


class Cell:
    def __init__(self, left: float, right: float, fields: list[str]):
        self.left, self.right = left, right
        self.paras: list[tuple[str, str]] = []
        self.align = {"C": "center", "R": "right"}.get(field(fields, 4), "")
        # fields 15-18: top / bottom / right / left rule (a width, e.g. "10")
        self.rules = tuple(bool(num(field(fields, i))) for i in (15, 16, 17, 18))


class Box:
    def __init__(self, x=0, y=0, w=None, h=None, kind="block"):
        self.x, self.y, self.w, self.h, self.kind = x, y, w, h, kind
        # ("p", html, align) | ("row", [Cell]) | ("box", Box) | ("line", pts, filled)
        # | ("img", x, y, w, h, file, nr)
        self.children: list = []
        self.para: list[str] = []
        self.align = ""
        self.row: list[Cell] | None = None
        self.anchor = None             # (x, y) for the next picture

    def flush(self):
        text = re.sub(r"(<br>\s*){3,}", "<br><br>", "".join(self.para)).strip()
        while text.startswith("<br>"):
            text = text[4:].lstrip()
        while text.endswith("<br>"):
            text = text[:-4].rstrip()
        self.para = []
        if not text:
            return
        if self.row:
            self.row[-1].paras.append((text, self.align))
        else:
            self.children.append(("p", text, self.align))

    def end_row(self):
        if self.row:
            self.children.append(("row", self.row))
        self.row = None


def run_html(q: str, font: tuple[str, float, bool]) -> str:
    """One quoted text run in its font, or "" for a structural marker."""
    family, pt, bold = font
    if q.strip().startswith("<"):                # "<ord.k.t medium": a style marker
        return ""
    if family.startswith(("LOGO", "FONT")):
        glyph = q.strip()
        if not glyph:
            return ""
        icon = LOGO.get((family, glyph))
        if icon:
            return f'<span class="ico {icon[2]}" title="{icon[1]}">{icon[0]}</span>'
        return (f'<span class="ico unk" title="WIS icon ({html.escape(family)} '
                f'{html.escape(glyph)})"></span>')
    if family == "GREEK":
        return f'<span class="sym">{html.escape("".join(GREEK.get(c, c) for c in q))}</span>'
    if family in ("MATH", "SYMBOLS", "CLASSIC"):
        # symbol fonts with no known mapping: show the raw character, marked,
        # rather than a guessed sign -- a wrong "<" or "±" in a spec is worse
        # than an obvious unknown
        if not q.strip():
            return " "
        return (f'<span class="unk-sym" title="Undecoded WIS symbol ({family} '
                f'&quot;{html.escape(q.strip())}&quot;): check WIS">'
                f'{html.escape(q.strip())}</span>')
    m = XREF.match(q)
    if m:
        return f'<span class="xref">{html.escape(m.group(1))}</span>'
    q = INLINE_CTL.sub(" ", q)
    style = f"font-size:{pt * PT * SCALE:.1f}px" + (";font-weight:700" if bold else "")
    return f'<span style="{style}">{html.escape(q)}</span>'


def layout(raw: str, files: dict[str, str]) -> Box:
    fonts = font_table(raw)
    page = Box(kind="page")
    stack = [page]
    font = fonts.get("10", ("MODERN", 12.0, False))
    for r in split_records(raw):
        r = r.strip()
        if not r or r.startswith("ASCII-SIDS"):
            continue
        fields, quoted = split_fields(r)
        lead = fields[0].strip()
        cur = stack[-1]
        if lead == "E" or (lead.startswith("F") and quoted):
            break                               # end of body; the font table follows
        if lead == "V":
            nums = [num(f) for f in fields[1:]]
            w, h = (nums[4] if len(nums) > 4 else None), (nums[5] if len(nums) > 5 else None)
            b = Box(nums[2] or 0, nums[3] or 0, w, h, "frame") if (w or h) else Box(kind="block")
            cur.flush()
            cur.end_row()
            cur.children.append(("box", b))
            stack.append(b)
            continue
        if lead == "W":
            cur.flush()
            cur.end_row()
            if len(stack) > 1:
                stack.pop()
            continue
        if lead in ("G", "GP", "GA"):
            # coordinates are "(40,4400),(120,120)" -- commas inside the
            # parentheses, so read them from the raw record, not the fields
            groups = re.findall(r"\(([^)]*)\)", r)
            xs = [num(v) for v in re.findall(r"-?\d+", groups[0])] if groups else []
            ys = [num(v) for v in re.findall(r"-?\d+", groups[1])] if len(groups) > 1 else []
            pts = list(zip(xs, ys))
            if len(pts) >= 2:
                cur.children.append(("line", pts, lead == "GP"))
            continue
        if lead in ("P", "PC", "PI"):
            cur.flush()
            # "PC,T,x" starts a table row whose first column ends at x; each
            # following "PC,,x" adds a column ending at x. "PI" is a new
            # paragraph inside the same cell; a plain "P" leaves the table.
            if lead == "PC":
                x = num(field(fields, 2)) or 0
                if field(fields, 1) == "T" or cur.row is None:
                    cur.end_row()
                    cur.row = [Cell(0, x, fields)]
                else:
                    cur.row.append(Cell(cur.row[-1].right, x, fields))
            elif lead == "P":
                cur.end_row()
            cur.align = {"C": "center", "R": "right"}.get(field(fields, 4), "")
            if "A" in fields:
                i = fields.index("A")
                cur.anchor = (num(field(fields, i + 2)) or 0, num(field(fields, i + 3)) or 0)
            continue
        if lead == "L" or (lead == "T" and not quoted):
            cur.para.append("<br>")
            continue
        if lead.startswith("L") and lead[1:].isdigit():
            # a tab to an absolute position, usually beyond the cell edge
            # (Camber strut -> MODEL ...; M8 -> Nm): a gap, not a position
            cur.para.append('<span class="tab"> </span>')
            continue
        if lead.isdigit():
            fid = field(fields, 1)
            if fid:
                font = fonts.get(fid, ("MODERN", 10.0, False))
                hp = num(field(fields, 2))     # size in half-points: 24 = 12 pt
                if hp:
                    font = (font[0], hp / 2, font[2])
            # a run code not starting with 0 and carrying no text ("11",
            # "2", "12,12,24" between step number "1" and "Lock") is where
            # WIS puts a tab: measured on 400 documents, every one sits
            # between two pieces of text that print apart
            if not quoted and not lead.startswith("0"):
                cur.para.append('<span class="tab"> </span>')
        for q in quoted:
            m = GFX.search(q)
            if m:
                nr = m.group(1).lower()
                ax, ay = cur.anchor or (0, 0)
                cur.anchor = None
                # the anchor is in block coordinates even when the picture is
                # declared inside a caption frame: hang it on the block
                host = next((b for b in reversed(stack) if b.kind in ("block", "page")), cur)
                host.children.append(("img", ax, ay, float(m.group(2)) * CM,
                                      float(m.group(3)) * CM, files.get(nr), nr))
                continue
            cur.para.append(run_html(q, font))
    for b in reversed(stack):
        b.flush()
        b.end_row()
    return page


def extent(box: Box) -> float:
    """The widest thing drawn, in SIDS units, so landscape pages get their width."""
    w = (box.x or 0) + (box.w or 0) if box.kind == "frame" else 0
    for c in box.children:
        if c[0] == "row":
            w = max(w, c[1][-1].right)
        elif c[0] == "box":
            w = max(w, extent(c[1]))
        elif c[0] == "line":
            w = max([w] + [x for x, _ in c[1] if x is not None])
        elif c[0] == "img":
            w = max(w, c[1] + c[3])
    return w


def _rules(cell: Cell) -> str:
    sides = ("top", "bottom", "right", "left")
    return "".join(f"border-{s}:1px solid #333;" for s, on in zip(sides, cell.rules) if on)


def _para(text: str, align: str) -> str:
    return f'<p style="text-align:{align}">{text}</p>' if align else f"<p>{text}</p>"


def draw(box: Box, img_url) -> str:
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
        out.append(f'<div class="block" style="min-height:{h:.0f}px">')
        lines = [c for c in box.children if c[0] == "line"]
        if lines:
            svg = [f'<svg class="rules" width="100%" height="{h:.0f}">']
            for c in lines:
                pts = " ".join(f"{x * SCALE:.1f},{y * SCALE:.1f}" for x, y in c[1])
                svg.append(f'<polyline points="{pts}" fill="{"#fff" if c[2] else "none"}" '
                           f'stroke="#222" stroke-width="1"/>')
            svg.append("</svg>")
            out.append("".join(svg))
    else:
        # a frame with a height is placed (a caption box over a picture); one
        # without is a band that flows with the text
        pos = (f"position:absolute;left:{box.x * SCALE:.0f}px;top:{box.y * SCALE:.0f}px;"
               if box.h else "")
        out.append(f'<div class="frame" style="{pos}width:{(box.w or TEXT_W) * SCALE:.0f}px">')
    for c in box.children:
        if c[0] == "p":
            out.append(_para(c[1], c[2]))
        elif c[0] == "row":
            cells = []
            for cell in c[1]:
                style = f"width:{max(cell.right - cell.left, 0) * SCALE:.0f}px;{_rules(cell)}"
                if cell.align:
                    style += f"text-align:{cell.align};"
                cells.append(f'<div class="cell" style="{style}">'
                             + "".join(_para(t, a) for t, a in cell.paras) + "</div>")
            out.append('<div class="trow">' + "".join(cells) + "</div>")
        elif c[0] == "box":
            out.append(draw(c[1], img_url))
        elif c[0] == "img":
            _, x, y, w, h, file, nr = c
            if file:
                u = html.escape(img_url(file))
                tag = f'<a href="{u}" target="_blank"><img src="{u}" alt="{nr}"></a>'
            else:
                tag = f'<div class="missing">{html.escape(nr)}</div>'
            out.append(f'<div class="pic" style="left:{x * SCALE:.0f}px;top:{y * SCALE:.0f}px;'
                       f'width:{w * SCALE:.0f}px;height:{h * SCALE:.0f}px">{tag}</div>')
    out.append("</div>")
    return "".join(out)


def render(corpus: Path, dok_id: int, title: str, img_url) -> str:
    """The whole document as a standalone HTML page.

    `img_url(file)` turns a catalog path ("4/74443.png") into a URL -- the
    server passes its own /api/image route, so nothing is embedded.
    """
    idx = PackIndex(corpus)
    blob, files = idx.body(dok_id), idx.figures(dok_id)
    if blob.removeprefix(b"\xef\xbb\xbf")[:5] == b"{\\rtf":    # some carry a UTF-8 BOM
        return render_rtf(blob, files, title or str(dok_id), img_url)
    page = layout(sids_text(blob), files)
    width = round(max(extent(page), TEXT_W) * SCALE) + 2 * PAD
    return HTML.format(title=html.escape(title or str(dok_id)), body=draw(page, img_url),
                       w=width, pad=PAD)


# graphic reference inside an RTF stub: "&V& pe2819d200099c|70.72|11.80|DXF;;"
RTF_GFX = re.compile(r"&[A-Za-z]&\s*([^\s|]+)\|([\d.]+)\|([\d.]+)\|([A-Za-z]{2,5});")
# ("&H& 0|5.01|2.56|1.53|0.46;18366;" lines are hotspots, not graphics)


def render_rtf(blob: bytes, files: dict[str, str], title: str, img_url) -> str:
    """An RTF body is a stub naming a graphic (wiring diagrams, scanned
    pages): its original layout is the graphic itself, drawn page-wide."""
    refs = RTF_GFX.findall(blob.decode("latin-1"))
    if not refs:
        raise NotAvailable("this document has no layout to draw")
    parts = []
    for nr, w_cm, h_cm, _fmt in refs:
        file = files.get(nr.lower())
        if file:
            u = html.escape(img_url(file))
            parts.append(f'<a class="whole" href="{u}" target="_blank"><img src="{u}" alt="{html.escape(nr)}"></a>')
        else:
            parts.append(f'<div class="missing" style="height:120px">{html.escape(nr)} (not converted)</div>')
    width = round(TEXT_W * SCALE) + 2 * PAD
    return HTML.format(title=html.escape(title), body="".join(parts), w=width, pad=PAD)


# The sheet is drawn at a fixed width (positions are absolute) and zoomed to
# fit the viewport, like a PDF viewer: most WIS pages are landscape (~1,300
# px drawn), so on a phone that is small type, read by pinch-zooming or
# turning the phone -- a sideways-scrolling frame inside the page was worse.
# `zoom` changes layout size (unlike transform), so the embedding iframe can
# read the real height.
HTML = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
html,body{{margin:0;background:#e9e9e9}}
body{{font-family:Arial,Helvetica,sans-serif;color:#111;padding:6px 0;font-size:17px}}
.sheet{{background:#fff;width:{w}px;margin:0 auto;padding:{pad}px;box-sizing:border-box;box-shadow:0 1px 6px #0003}}
.block{{position:relative;margin:4px 0}}
.rules{{position:absolute;left:0;top:0;pointer-events:none;overflow:visible}}
.frame{{position:relative}}
p{{margin:2px 0;line-height:1.25}}
.frame p{{margin:2px 4px}}
.trow{{display:flex}}
.cell{{box-sizing:border-box;padding:2px 4px;flex:none;overflow-wrap:anywhere}}
.pic{{position:absolute}}
.pic a{{display:block;width:100%;height:100%}}
.pic img{{width:100%;height:100%;object-fit:contain}}
.whole{{display:block}} .whole img{{display:block;width:100%;height:auto}}
.missing{{width:100%;height:100%;display:flex;align-items:center;justify-content:center;color:#999;font-size:11px;border:1px dashed #ccc}}
.tab{{display:inline-block;min-width:1.2em}}
.ico{{display:inline-block;min-width:1.1em;text-align:center;font-weight:700}}
.ico.warn{{color:#c00}} .ico.note{{color:#1a5fb4}} .ico.ref{{color:#555}}
.ico.unk{{width:.8em;height:.8em;border:1px solid #999;border-radius:3px;min-width:0;vertical-align:-1px}}
.unk-sym{{border-bottom:1px dotted #c00;color:#a00}}
.xref{{font-family:Consolas,monospace;font-size:.85em;color:#1a5fb4}}
</style></head><body><div class="sheet">{body}</div>
<script>
(function(){{
  var s=document.querySelector('.sheet');
  function fit(){{
    var z=Math.min(1,(document.documentElement.clientWidth-4)/{w});
    s.style.zoom=z;
    // the sheet's own height: the document's scrollHeight never drops
    // below the frame's current height, so the frame could only ever grow
    if(window.parent!==window)window.parent.postMessage({{wisOriginalHeight:Math.ceil(s.getBoundingClientRect().bottom+scrollY+6)}},'*');
  }}
  addEventListener('resize',fit);addEventListener('load',fit);fit();
  if(window.ResizeObserver)new ResizeObserver(fit).observe(s);
}})();
</script></body></html>"""
