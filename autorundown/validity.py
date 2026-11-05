"""Which models a WIS page is valid for, from its own "Validity:" lines.

A WIS page carries two model statements: "Applies to: MODEL 210; all
variants" is where it is filed (the series), and "Validity: MODEL 210.0 as of
1.7.99" is what WIS says it is for. Search filtered by the first only, so on
the W210 about 1,650 pages valid for the sedans only (210.0..) reached a
wagon (210.2..) and about 300 wagon-only pages a sedan -- "Trunk lid ambient
lamp" for an E 320 4MATIC T (2026-11-04, the VIN-filtering check's F9).

`models(text)` reads the Validity lines into model-code prefixes, stored per
page in `page_validity` (",210.0,210.2,211.," -- "211." is the whole
series); `clause()` is the search filter: a page whose Validity names the
car's series but none of the car's codes is left out.

An engine's own pages ("Applies to: ENGINE 102", filed under no model)
often name their models only in the title: "1700 - Testing 2 E-E carburetor
system … Engine 102 with carburetor 2 E-E  Models 210, 310, 410, 510" is
the vans' carburettor, and it reached a KE-Jetronic 190 E 2.3-16 (F5 of the
same check). `title_models` reads those into a row marked "t"
("t,210.,310.,410.,510."), and `clause()` leaves such a page out for a car
whose series it doesn't name. Only for engine pages: a page filed under the
car's own model is the car's, whatever its title says.

Conservative on purpose -- leaving a right page out is a miss, so anything
it can't read keeps the page: "except" anywhere in a Validity line, a
Validity line that names no model (an "ENGINE 112" section is valid for
every model with it), or no Validity line at all. Conditions after the model
list ("as of 1.7.99", "with CODE 979", "with ENGINE 112") only narrow, so the
list itself is enough to tell the car is not among them.
"""
from __future__ import annotations

import re
import sqlite3

_VALIDITY_RE = re.compile(r"^Validity:[ \t]*(.+)$", re.M)
_MODEL_KW_RE = re.compile(r"\bmodels?\b[ \t]*", re.I)
# "210", "210.0", "210.07", "210.282", "202.07/08/18", "203.0/2/7", "210.081/ 082"
_ITEM_RE = re.compile(r"(\d{3})(?:\.(\d{1,3}))?((?:[ \t]*/[ \t]*\.?\d{1,3})*)(?![\d.])")
_SEP_RE = re.compile(r"[ \t]*(?:,|;|&|\band\b)[ \t]*", re.I)


def _items(s: str) -> set[str]:
    """The model codes listed at the start of `s` (after "MODEL ")."""
    out: set[str] = set()
    pos = 0
    while True:
        m = _ITEM_RE.match(s, pos)
        if not m:
            break
        series, first, slashed = m.group(1), m.group(2), m.group(3)
        alts = [a.strip(" \t./") for a in slashed.split("/") if a.strip(" \t./")]
        if first is None:
            # "MODEL 210" or "MODEL 202/210": whole series
            out.add(f"{series}.")
            out |= {f"{a}." for a in alts if len(a) == 3}
        else:
            out.add(f"{series}.{first}")
            out |= {f"{series}.{a}" for a in alts}
        pos = m.end()
        sep = _SEP_RE.match(s, pos)
        if not sep or not _ITEM_RE.match(s, sep.end()):
            break
        pos = sep.end()
    return out


def models(text: str) -> str:
    """",210.0,210.2," -- the model-code prefixes the page's Validity lines
    name; "" when there is none or any line can't be read safely."""
    lines = _VALIDITY_RE.findall(text or "")
    if not lines:
        return ""
    found: set[str] = set()
    for line in lines:
        if "except" in line.lower():
            return ""
        mine: set[str] = set()
        for kw in _MODEL_KW_RE.finditer(line):
            mine |= _items(line[kw.end():])
        if not mine:
            return ""            # a section valid for every model (an engine, a code)
        found |= mine
    return "," + ",".join(sorted(found)) + ","


_APPLIES_ENGINE_RE = re.compile(r"^Applies to:[ \t]*ENGINE\b", re.M | re.I)
_APPLIES_MODEL_RE = re.compile(r"^Applies to:.*\bMODEL\b", re.M | re.I)


def title_models(title: str, text: str) -> str:
    """"t,210.,310.," -- the models an engine page's title names ("Models
    210, 310"); "" for any other page, a title naming none, "except" or
    "all models" ("A. Filler cap (all models), B. … Models 124, 201")."""
    title = title or ""
    if (not _APPLIES_ENGINE_RE.search(text or "") or _APPLIES_MODEL_RE.search(text or "")
            or re.search(r"except|all models", title, re.I)):
        return ""
    found: set[str] = set()
    for kw in _MODEL_KW_RE.finditer(title):
        found |= _items(title[kw.end():])
    return "t," + ",".join(sorted(found)) + "," if found else ""


def _prefixes(code: str) -> list[str]:
    """",210.,", ",210.2,", ",210.28,", ",210.282," for "210.282"."""
    series, _, rest = code.partition(".")
    return [f",{series}.{rest[:n]}," for n in range(len(rest) + 1)]


def clause(conn: sqlite3.Connection, vcodes) -> tuple[str, list]:
    """SQL (pages aliased `p`) leaving out pages whose Validity names the
    car's series and none of its codes. Nothing without the car's model
    codes, or on an index without `page_validity` (an older, read-only one)."""
    vcodes = [c for c in vcodes or () if re.fullmatch(r"\d{3}\.\d{3}", str(c))]
    if not vcodes or not conn.execute(        # cheap; the table may appear later
            "SELECT 1 FROM sqlite_master WHERE name='page_validity' "
            "UNION ALL SELECT 1 FROM sqlite_temp_master WHERE name='page_validity'").fetchone():
        return "", []
    sql, args = "", []
    every = list(dict.fromkeys(c[:3] for c in vcodes))
    for series in every:
        mine = [p for c in vcodes if c[:3] == series for p in _prefixes(c)]
        sql += (" AND NOT EXISTS (SELECT 1 FROM page_validity v WHERE v.page_uid = p.page_uid"
                " AND substr(v.models, 1, 1) = ',' AND instr(v.models, ?) > 0 AND NOT ("
                + " OR ".join("instr(v.models, ?) > 0" for _ in mine) + "))")
        args += [f",{series}.", *mine]
    # an engine page whose title names models: one of them must be the car's
    # series ("t,201.," or "t,201.024,")
    sql += (" AND NOT EXISTS (SELECT 1 FROM page_validity v WHERE v.page_uid = p.page_uid"
            " AND substr(v.models, 1, 1) = 't' AND NOT ("
            + " OR ".join("instr(v.models, ?) > 0" for _ in every) + "))")
    args += [f",{s}." for s in every]
    return sql, args


def fits(text: str, vcodes, title: str = "") -> bool:
    """`clause` for one page in Python (a page reached by a link, not search)."""
    m = models(text)
    every = list(dict.fromkeys(str(c)[:3] for c in vcodes or ()
                               if re.fullmatch(r"\d{3}\.\d{3}", str(c))))
    for series in every:
        if f",{series}." in m and not any(
                p in m for c in vcodes if str(c)[:3] == series for p in _prefixes(str(c))):
            return False
    t = "" if m else title_models(title, text)
    return not (t and every and not any(f",{s}." in t for s in every))


def row(text: str, title: str = "") -> str:
    """What `page_validity` holds for a page: its Validity line, else an
    engine page's title models, else nothing."""
    return models(text) or title_models(title, text)


def store(conn: sqlite3.Connection, page_uid: str, text: str, title: str = "") -> bool:
    """Write (or clear) a page's row; True if it has one."""
    conn.execute("DELETE FROM page_validity WHERE page_uid=?", (page_uid,))
    m = row(text, title)
    if m:
        conn.execute("INSERT INTO page_validity(page_uid, models) VALUES (?,?)", (page_uid, m))
    return bool(m)


# the pages `row` may give a row for: a Validity line, or an engine page
# whose title names a model
_FILL_WHERE = ("instr(text, 'Validity:') > 0 OR (instr(text, 'Applies to: ENGINE') > 0"
               " AND title LIKE '%model%')")
FILLED = "#filled"          # a row saying the table was filled for this index
BATCH = 5000
MAX_BYTES_PER_S = 100e6     # pace the scan: this PC crashes under heavy I/O


def filled(conn: sqlite3.Connection) -> bool:
    return bool(conn.execute("SELECT 1 FROM page_validity WHERE page_uid=?", (FILLED,)).fetchone())


def fill(conn: sqlite3.Connection, apply: bool = True, progress=None) -> dict:
    """Read every page with a Validity line and write its row: for an index
    built before 2026-11-04 (add_page writes them since). Pages are only
    read, paced; rows go in batches with a commit each, so searches keep
    running alongside. Ends with the FILLED row, so it runs once."""
    import time
    top = conn.execute("SELECT MAX(rowid) FROM pages").fetchone()[0] or 0
    have = {r[0] for r in conn.execute("SELECT page_uid FROM page_validity")}
    stats = {"with_line": 0, "rows": 0, "new": 0}
    start = time.time()
    for lo in range(0, top + 1, BATCH):
        t0, nbytes, batch = time.time(), 0, []
        for uid, text, title in conn.execute(
                "SELECT page_uid, text, title FROM pages WHERE rowid >= ? AND rowid < ?"
                f" AND ({_FILL_WHERE})", (lo, lo + BATCH)):
            nbytes += len(text or "")
            stats["with_line"] += 1
            m = row(text, title)
            if m:
                stats["rows"] += 1
                stats["new"] += uid not in have
                batch.append((uid, m))
        if apply and batch:
            conn.executemany("INSERT OR REPLACE INTO page_validity(page_uid, models) VALUES (?,?)", batch)
            conn.commit()
        wait = nbytes / MAX_BYTES_PER_S - (time.time() - t0)
        if wait > 0:
            time.sleep(wait)
        if progress and lo // BATCH % 20 == 0:
            progress(min(top, lo + BATCH), top, stats["rows"])
    if apply:
        conn.execute("INSERT OR REPLACE INTO page_validity(page_uid, models) VALUES (?, '')", (FILLED,))
        conn.commit()
    stats["seconds"] = round(time.time() - start)
    return stats


def fill_once(index_path: str) -> None:
    """The server's background fill: once per index, never in the way.
    Any failure leaves search as it was (no rows, no filter)."""
    from . import index
    try:
        conn = index.connect(index_path, setup=False)
        try:
            # any row: filled before, or built since add_page writes them
            if conn.execute("SELECT 1 FROM page_validity LIMIT 1").fetchone() or not conn.execute(
                    f"SELECT 1 FROM pages WHERE {_FILL_WHERE} LIMIT 1").fetchone():
                return
            s = fill(conn)
            print(f"WIS Validity lines read: {s['rows']:,} pages ({s['seconds']} s)", flush=True)
        finally:
            conn.close()
    except Exception as exc:          # never stops the app
        print(f"WIS Validity lines not read: {exc}", flush=True)
