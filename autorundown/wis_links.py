"""WIS cross-references, resolved to pages when a page is opened.

WIS pages name other documents by number ("Refer to: AR42.10-P-0100A"). The
extractor's `parse_links.py` writes `wis_links.sqlite` next to the corpus,
whose `docnr` table maps each document number WIS has to the DokID of our
record (= `pages.page_no`).

A printed reference leaves off the variant letters that end a stored number:
"AR15.40-P-5710E" is ...5710ea, ...5710eb, ...5710ecm, one per model, and WIS
picks the one for the car being looked at. So a reference matches every
stored number that is the printed one plus letters only, and the variant
filed under the chosen car's scope comes first. Measured on the English
corpus: 46% of the 1.28M printed references match a stored number exactly,
and most of the rest only this way. Choosing a variant without the car would
open another model's version -- the wrong-vehicle failure -- which is why
this happens here, with the scope, and not at import.

Free, local: no API calls. Without the sidecar every reference stays text.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path

NAME = "wis_links.sqlite"

# the extractor's DOCNR_CORE (parse_sids.py), so both read the same numbers
DOCNR_RE = re.compile(
    r"\b[A-Z]{2}[0-9]{2}\.[0-9]{2}-[A-Z]{1,2}-[0-9]{3,4}[A-Z]?(?:-[0-9]{2}[A-Z]?)?\b")

_lock = threading.Lock()
_open: dict[str, sqlite3.Connection | None] = {}


def norm(docnr: str) -> str:
    """'AR42.10-P-0100A' (printed) and 'ar4210p0100a' (stored) -> the same."""
    return re.sub(r"[^0-9a-z]", "", docnr.lower())


def _db(corpus: Path) -> sqlite3.Connection | None:
    """The corpus's sidecar, opened read-only once per process; None if absent."""
    path = Path(corpus) / NAME
    key = str(path)
    with _lock:
        if key not in _open or _open[key] is None:
            if not path.is_file():
                return None
            try:
                _open[key] = sqlite3.connect(f"file:{path}?mode=ro", uri=True,
                                             check_same_thread=False)
            except sqlite3.Error:
                return None
        return _open[key]


def _rows(corpus: Path, sql: str, args=()) -> list[tuple] | None:
    """Rows from the sidecar; None without one, [] if a table is missing (a
    sidecar from an older parse_links.py) -- a page view must never fail
    over a lookup that only adds to it."""
    db = _db(corpus)
    if db is None:
        return None
    try:
        with _lock:
            return db.execute(sql, args).fetchall()
    except sqlite3.Error:
        return []


def candidates(corpus: Path, printed: str) -> list[int]:
    """DokIDs a printed reference can mean: the same number, plus only
    variant letters. A number printed without its letter ("...-P-0100")
    must not match the sub-document "...-P-0100-01A"."""
    key = norm(printed)
    rows = _rows(corpus, "SELECT docnr, dok_id FROM docnr WHERE docnr >= ? AND docnr < ?",
                 (key, key + "{")) or []     # '{' sorts after 'z'
    return sorted({dok for docnr, dok in rows if docnr[len(key):].isalpha()
                   or docnr == key})


def pages_for(conn: sqlite3.Connection, corpus: Path, printed: str,
              scope: list[str], wis_ids: list[str], own_page: str = "") -> list[dict]:
    """The pages a document number opens: [{page_no, title, applies, doc_id,
    in_scope}], the chosen car's scope first (in scope order), then the same
    document filed under other models. Looked up in `wis_ids` only -- the
    doc_ids of this corpus's vehicles."""
    doks = [str(d) for d in candidates(corpus, printed) if str(d) != own_page]
    if not doks or not wis_ids:
        return []
    rows = conn.execute(
        f"SELECT doc_id, page_no, title, section_path FROM pages WHERE page_no IN "
        f"({','.join('?' * len(doks))}) AND doc_id IN ({','.join('?' * len(wis_ids))})",
        [*doks, *wis_ids]).fetchall()
    rank = {d: i for i, d in enumerate(scope)}
    best: dict[str, tuple] = {}
    for doc_id, page_no, title, section in rows:
        r = rank.get(doc_id, len(rank))
        if page_no not in best or r < best[page_no][0]:
            best[page_no] = (r, doc_id, title, section)
    # `applies`: the page's applicability line, which is what tells two
    # variants of one document apart ("MODEL 211.0xx" / "211.2xx")
    return [{"page_no": p, "title": t, "applies": sec, "doc_id": d,
             "in_scope": r < len(rank)}
            for p, (r, d, t, sec) in sorted(best.items(), key=lambda kv: (kv[1][0], kv[0]))]


def resolve(conn: sqlite3.Connection, corpus: Path, text: str,
            scope: list[str], wis_ids: list[str], own_page: str = "") -> list[dict]:
    """The references in `text` that open a page, in order of first mention:
    [{"docnr": as printed, "pages": pages_for(...)}]."""
    if _db(corpus) is None or not wis_ids:
        return []
    out, seen = [], set()
    for m in DOCNR_RE.finditer(text or ""):
        printed = m.group(0)
        if printed in seen:
            continue
        seen.add(printed)
        pages = pages_for(conn, corpus, printed, scope, wis_ids, own_page)
        if pages:
            out.append({"docnr": printed, "pages": pages})
    return out


# -- wiring diagrams --------------------------------------------------------
#
# WIS stores, per diagram document, each component code's label position
# (DXFLegendePos) and its name (DXFLegende); some codes also link to a
# document (AW_SVGReferenz). Positions are drawing units = 1/1000 of a
# millimetre, and the diagrams reach us in two forms, each mapped to a
# fraction of the drawn image so the UI can place a button over an <img>:
#
# - converted from DXF (convert_images.py): the SVG says its size in mm and
#   has no text layer. DXF's Y runs up from the bottom edge, so Y flips.
#   Checked by eye on 25131415: every code lands on its printed label.
# - WIS's own SVG ("daisy" namespace): size in pt, a viewBox in the same
#   units as the positions / 1000, Y down, laid out "xMinYMid meet" -- the
#   viewport may show more than the viewBox, so fractions are of what is
#   drawn, not of the viewBox.

_MM = re.compile(r'width="([\d.]+)mm"\s+height="([\d.]+)mm"')
_PT_W = re.compile(r'\bwidth="([\d.]+)pt"')
_PT_H = re.compile(r'\bheight="([\d.]+)pt"')
_VB = re.compile(r'viewBox="([-\d.]+)\s+([-\d.]+)\s+([\d.]+)\s+([\d.]+)"')


def _svg_frame(path: Path):
    """A function (x, y) -> (fx, fy) in 0..1 of the drawn image, or None."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = f.read(4096)
    except OSError:
        return None
    i = head.find("<svg")
    if i < 0:
        return None
    tag = head[i:head.find(">", i) + 1]
    mm = _MM.search(tag)
    if mm:
        w, h = float(mm[1]) * 1000, float(mm[2]) * 1000
        return lambda x, y: (x / w, 1 - y / h)
    pw, ph, vb = _PT_W.search(tag), _PT_H.search(tag), _VB.search(tag)
    if pw and ph and vb:
        W, H = float(pw[1]), float(ph[1])
        vx, vy, vw, vh = (float(v) for v in vb.groups())
        if not (W and H and vw and vh):
            return None
        s = min(W / vw, H / vh)            # "meet"
        uw, uh = W / s, H / s              # user units actually drawn
        oy = (uh - vh) / 2                 # "YMid": centred vertically
        return lambda x, y: ((x / 1000 - vx) / uw, (y / 1000 - vy + oy) / uh)
    return None


def diagram(corpus: Path, dok_id: int, image: str) -> dict | None:
    """Component codes on one WIS diagram page, placed on its image.

    {"image": file, "codes": [{code, name, at: [[fx, fy], ...], docnr}]},
    one entry per code (a code is printed wherever its wires go), sorted by
    code; `at` is empty when the image's frame is unknown, so the legend
    still works. None when WIS has no component data for the page.
    """
    pos = _rows(corpus, "SELECT code, x, y FROM diagram_code WHERE dok_id=?", (dok_id,)) or []
    names = dict(_rows(corpus, "SELECT code, name FROM diagram_name WHERE dok_id=?",
                       (dok_id,)) or [])
    refs = dict(_rows(corpus, "SELECT code, docnr FROM diagram_svgref WHERE dok_id=?",
                      (dok_id,)) or [])
    if not pos and not names:
        return None
    frame = _svg_frame(Path(corpus) / "images" / image) if image.endswith(".svg") else None
    codes: dict[str, dict] = {}
    for code in sorted(set(names) | {c for c, _, _ in pos}, key=_code_key):
        codes[code] = {"code": code, "name": names.get(code, ""), "at": [],
                       "docnr": refs.get(code, "")}
    if frame:
        for code, x, y in pos:
            fx, fy = frame(x, y)
            if -0.01 <= fx <= 1.01 and -0.01 <= fy <= 1.01:
                codes[code]["at"].append([round(fx, 5), round(fy, 5)])
    return {"image": image, "codes": list(codes.values())}


def _code_key(code: str):
    """N3/10 before N3/9 numerically, letters first: N3/9, N3/10, N15/5."""
    return [(0, int(t)) if t.isdigit() else (1, t) for t in re.findall(r"\d+|\D+", code)]


def has_diagram(corpus: Path, dok_id: int) -> bool:
    """Whether WIS has component data for this page (cheap: one index probe)."""
    return bool(_rows(corpus, "SELECT 1 FROM diagram_name WHERE dok_id=? LIMIT 1", (dok_id,)))


# -- reference data: option codes and oil capacities -------------------------

SA_CODE_RE = re.compile(r"[0-9A-Z]{3}")


# WIS's FzgArt codes, named as parse_vehicles.py writes a variant's
# vehicle_type (FzgArt.tsv, English rows). smart has no option codes of its
# own and uses the car list.
FZG_ART = {"CAR": 1, "4xWD": 2, "Light transporter": 3, "Heavy transporter": 4,
           "TRUCK": 5, "BUS": 8, "UNIMOG": 9, "smart": 1}


def sa_codes(corpus: Path, text: str, vehicle_type: str = "") -> list[dict] | None:
    """Option (SA) codes typed or pasted from a data card ("494 U10 P31",
    "494/U10") -> [{code, name, other}]. One code means different things for
    different vehicle types (494: "US version" on a car, a tyre on a bus), so
    `name` is the meaning for this car's type and `other` the rest, shown
    only when this type has none. None without the sidecar."""
    if _db(corpus) is None:
        return None
    mine = FZG_ART.get(vehicle_type)
    if vehicle_type == "TRUCK":
        mine = None                         # three truck codes (5-7): list them all
    out = []
    for code in dict.fromkeys(SA_CODE_RE.findall((text or "").upper())):
        rows = _rows(corpus, "SELECT vehicle_type, name FROM sa_code WHERE code=? "
                             "ORDER BY vehicle_type", (code,)) or []
        name = next((n for t, n in rows if t == mine), "")
        other = [] if name else list(dict.fromkeys(n for _, n in rows))
        out.append({"code": code, "name": name, "other": other})
    return out


def oil(corpus: Path, series: str, engines: list[str]) -> list[dict]:
    """Engine oil capacity from WIS's service data (AW_AssystFuellmengen)
    for exactly these engine codes ("133.980") in this series -- never a
    whole series' engines at once, so no other engine's figure is shown."""
    if not series or not engines:
        return []
    keys = {e.replace(".", ""): e for e in engines}
    rows = _rows(corpus, f"SELECT engine, litres, unit, note FROM oil WHERE series=? AND "
                         f"engine IN ({','.join('?' * len(keys))}) ORDER BY engine, note",
                 [series, *keys]) or []
    return [{"engine": keys[e], "litres": l, "unit": u, "note": n} for e, l, u, n in rows]
