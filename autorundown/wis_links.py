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

import ast
import functools
import operator
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


# -- reference data: oil capacities ------------------------------------------
#
# The sidecar's option-code tables (sa_code, sa_number) are no longer read:
# the Option codes dialog was removed (2026-10-16, the user: nobody knows
# their car's option codes). Reading a car's data card is a possible later
# way back to them.

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


# -- labour times (WIS's ASRA flat rates) ------------------------------------
#
# A car (Baumuster + engine) is in one or more labour-time books (ASRA
# family + type code; parse_links.py). A book's time for an operation is
# the row for its exact type code, else its type group ("4x" -> "40"), else
# "00" -- these never overlap (measured on all 478,402 operation x family
# cases). The stored value is raw; the flat rate is the region's formula
# applied to it, in minutes. Checked against LEMON's Mercedes warranty hours
# for the 190E 2.3-16: voltage regulator 44 min / 0.7 h, bleed brakes 19 /
# 0.3, ignition switch 84 / 1.5, starter 110 / 1.8, thermostat 25 / 0.5.
# Extra-work operations (category Z) have no formula and are left out, as
# are raw zeros (an operation that doesn't apply to that type).

REGION = "10"          # ASRA "International"; "00" is Germany

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow}


@functools.lru_cache(maxsize=256)
def _formula(expr: str):
    """WIS's formula text ("(0.9883**x*0.3+1.21)*x") as a function of x.

    Parsed as arithmetic only -- numbers, x, + - * / **, brackets. The text
    comes from a database, so it is never handed to eval(); anything else
    raises ValueError and that operation gets no time.
    """
    tree = ast.parse(expr.strip(), mode="eval")

    def ev(n, x):
        if isinstance(n, ast.Expression):
            return ev(n.body, x)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.Name) and n.id == "x":
            return x
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.left, x), ev(n.right, x))
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.USub):
            return -ev(n.operand, x)
        raise ValueError(f"not arithmetic: {ast.dump(n)[:60]}")

    ev(tree, 1.0)                    # refuse a bad formula now, not per row
    return lambda x: ev(tree, x)


def labour_books(corpus: Path, baumuster: list[str], engines: list[str]) -> list[dict]:
    """The labour-time books this car is in: [{book, family, type}].

    With engines given, only books listed for those engines -- unless none
    are, then every book of the Baumuster. Several books are listed, never
    one picked: which applies depends on the chassis number.
    """
    if not baumuster:
        return []
    marks = ",".join("?" * len(baumuster))
    rows = _rows(corpus, f"SELECT DISTINCT c.engine, c.sparte, c.pl, c.family, c.type, "
                         f"b.family_name, b.type_name FROM labour_car c LEFT JOIN labour_book b "
                         f"USING (sparte, pl, family, type) WHERE c.baumuster IN ({marks})",
                 baumuster) or []
    mine = [r for r in rows if r[0] in set(engines)] if engines else []
    seen, out = set(), []
    for _, sp, pl, fam, typ, fname, tname in (mine or rows):
        key = f"{sp}-{pl}-{fam}-{typ}"
        if key not in seen:
            seen.add(key)
            out.append({"book": key, "family": fname or f"Family {fam}", "type": tname or f"Type {typ}"})
    return sorted(out, key=lambda b: (b["family"], b["type"]))


# What a search for a job matches in an operation's text: its core -- the
# component, without the action ("Remove/install"), the conditions in
# brackets ("(with transm. removed)": a time for when other work is already
# done) and the equipment it applies to ("Veh. with transmission 722.6").
# Words of the question that name no component are dropped from it.
_ACTIONS = re.compile(
    r"\b(remove/install|disassemble/assemble|remove|install|replace|exchange|check|"
    r"perform|clean|correct|adjust|retrofit|flush|rinse|seal|test|bleed|lubricate|"
    r"deprogram|inspect|establish findings|if necessary|and|part\(s\))(?!\w)", re.I)
_EQUIP = re.compile(r"\b(?:on\s+)?(?:veh\.?|vehicles?)\s+(?:with|w/|without)(?!\w).*$", re.I)
_DEPENDENT = re.compile(r"\([^)]*\b(?:removed|installed|disconnected|drained|exposed|when|following|"
                        r"after leak|prior to|with additional)\b[^)]*\)", re.I)
_Q_DROP = {"replacement", "replacing", "replaced", "procedure", "removal", "installation",
           "how", "to", "a", "an", "and", "on", "in", "labour", "labor", "time", "times",
           "hours", "job", "flat", "rate", "remove", "install", "replace"}
_GEARBOX = re.compile(r"\b(automatic|manual)\s+transm", re.I)


def _core(text: str) -> str:
    t = re.split(r"Acc\.[ -]?no|Invoice No", text)[0]
    t = re.sub(r"^Operations?\s*:\s*", "", t)
    t = _EQUIP.sub("", re.sub(r"\([^)]*\)", " ", t))
    return " ".join(_ACTIONS.sub(" ", t).lower().split())


def _action(text: str) -> str:
    m = _ACTIONS.search(text.split("(")[0])
    return m.group(1).lower() if m else ""


def _rank_labour(items: list[dict], queries: list[list[str]], limit: int) -> dict:
    """The operations a search names, best first: {"total", "best", "items"}.

    `best` is how many of `items` (from the start) are the job itself: the
    fewest extra words in the core, plain operations before add-on times
    ("(with transm. removed)") and campaigns (group 02); the rest only
    mention the words. Where a job is done differently on an automatic and a
    manual gearbox, both are among the best -- the car box does not say which
    gearbox the car has (nor which of a model's automatics, 722.6 or 722.9).
    """
    queries = [[w for w in ws if w not in _Q_DROP] for ws in queries]
    queries = [ws for ws in queries if ws]
    # a word matches a word it starts ("pad" -> "pads"), never one it is
    # inside of ("change" is not in "exchanger")
    def hits(ws, words):
        return all(any(c.startswith(w) for c in words) for w in ws)
    ranked = []
    for i in items if queries else []:
        core = _core(i["text"]).split()
        in_core = [ws for ws in queries if hits(ws, core)]
        if in_core:
            tier = 2 if i["kg"] == "02" else (1 if _DEPENDENT.search(i["text"]) else 0)
            extra = min(len([c for c in core if not any(c.startswith(w) for w in ws)])
                        for ws in in_core)
        elif any(hits(ws, re.findall(r"\w+", i["text"].lower())) for ws in queries):
            tier, extra = 3, len(core)
        else:
            continue
        ranked.append((tier, extra, i))
    if not ranked:
        return {"total": 0, "best": 0, "items": []}
    ranked.sort(key=lambda r: (r[0], r[1], r[2]["op"]))
    top = ranked[0][:2]
    best = [r[2] for r in ranked if r[:2] == top][:8]
    picked = {i["op"] for i in best}
    # the other gearbox's version of a best operation: same repair group,
    # same action, a word of its component in common
    for b in list(best):
        m = _GEARBOX.search(b["text"])
        if not m:
            continue
        other = "manual" if m.group(1).lower() == "automatic" else "automatic"
        words = {w for w in _core(b["text"]).split()
                 if len(w) > 3 and w not in ("automatic", "manual", "transmission")}
        for i in items:
            if (i["op"] not in picked and i["kg"] == b["kg"]
                    and _action(i["text"]) == _action(b["text"])
                    and re.search(rf"\b{other}\s+transm", i["text"], re.I)
                    and words & set(_core(i["text"]).split())):
                best.append(i)
                picked.add(i["op"])
    # the rest: closest wording first ("Starter Exchange (with transm.
    # removed)" before "Selector lever ... and starter lockout switch")
    rest = sorted((r for r in ranked if r[2]["op"] not in picked), key=lambda r: (r[1], r[0]))
    out = best + [r[2] for r in rest]
    return {"total": len(out), "best": len(best), "items": out[:max(limit, len(best))]}


def labour_times(corpus: Path, book: str, q: str = "", limit: int = 200,
                 alt: str = "") -> dict:
    """Flat-rate times in one book: {"total", "best", "items": [{group, op,
    text, minutes, hours}]}. With `q` -- or `alt`, the manual's wording for
    it ("water pump" -> "coolant pump", synonyms.py) -- only the operations
    that name it, the job itself first (`best` of them, _rank_labour);
    without, the whole book by repair group and operation."""
    try:
        sp, pl, fam, typ = book.split("-")
    except ValueError:
        return {"total": 0, "best": 0, "items": []}
    groups = dict(_rows(corpus, "SELECT kg, name FROM labour_group") or [])
    raw = {}
    for kg, op, ta, value in _rows(
            corpus, "SELECT kg, op, typenart, raw FROM labour_time WHERE sparte=? AND pl=? "
                    "AND family=? AND typenart IN (?,?,?)",
            (sp, pl, fam, typ, typ[:1] + "0", "00")) or []:
        raw[(kg, op)] = value        # at most one of the three exists (never overlap)
    if not raw:
        return {"total": 0, "best": 0, "items": []}
    forms = {(kg, cat): e for kg, cat, e in _rows(
        corpus, "SELECT kg, category, expr FROM labour_formula WHERE region=? AND sparte=? "
                "AND pl=? AND family=?", (REGION, sp, pl, fam)) or []}
    queries = [ws for ws in (re.findall(r"\w+", (s or "").lower()) for s in (q, alt)) if ws]
    items = []
    for kg, op, cat, text in _rows(corpus, "SELECT kg, op, category, text FROM labour_op "
                                           "WHERE sparte=?", (sp,)) or []:
        x = raw.get((kg, op))
        if not x or cat == "Z" or (kg, cat) not in forms:
            continue
        try:
            minutes = _formula(forms[(kg, cat)])(x)
        except (ValueError, SyntaxError, ZeroDivisionError, OverflowError, TypeError):
            continue
        if minutes <= 0:
            continue
        items.append({"group": groups.get(kg, kg), "kg": kg, "op": f"{kg}-{op}", "text": text,
                      "minutes": round(minutes), "hours": round(minutes / 60, 1)})
    items.sort(key=lambda i: i["op"])
    if queries:
        return _rank_labour(items, queries, limit)
    return {"total": len(items), "best": 0, "items": items[:limit]}
