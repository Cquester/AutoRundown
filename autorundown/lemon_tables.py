"""A LEMON manual's own labour times and parts lists, for the right box's
Labour times and Parts tabs.

Both tabs were WIS/EPC only, so a LEMON car hid them although its manual
has both: the 1987 190E has 528 "<component>: Labor Times" pages
("Ignition Lock Cylinder | 0.7 | 0.4") and 326 "Parts Information" pages
("Ignition Lock Tumbler | MGB | 2014600104 | 105.00"), found only as plain
search hits (agent review, 2026-10-28). Only this car's pages
(applicability this_vehicle): the Honda Civic's labour pages are all the
Hybrid's, marked other_variant, so its tab stays empty -- which is right.

Rows are read from the page's own table, as printed: hours and prices are
never computed or converted.
"""
from __future__ import annotations

import re
import sqlite3

from .wis_links import _rank_labour

LABOUR_SUFFIX = ": Labor Times"
_NUMBER = re.compile(r"^\d+(?:\.\d+)?$")
_cache: dict = {}


def _table(text: str) -> list[list[str]]:
    """The pipe-table rows of a page, cells stripped; the |---| rule left out."""
    rows = []
    for line in (text or "").split("\n"):
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(re.fullmatch(r"[-: ]*", c) for c in cells):
            continue
        rows.append(cells)
    return rows


def _pages(conn: sqlite3.Connection, doc_id: str, where: str, arg: str) -> list[tuple]:
    return conn.execute(
        f"SELECT page_no, title, section_path, text FROM pages WHERE doc_id = ? "
        f"AND applicability = 'this_vehicle' AND {where} ORDER BY page_no", (doc_id, arg)).fetchall()


def _cached(conn: sqlite3.Connection, doc_id: str, kind: str, build):
    n = conn.execute("SELECT count(*) FROM pages WHERE doc_id = ?", (doc_id,)).fetchone()[0]
    key = (doc_id, kind, n)
    if key not in _cache:
        if len(_cache) > 32:
            _cache.clear()
        _cache[key] = build()
    return _cache[key]


def _group(section_path: str) -> str:
    """"Parts and Labor > Starting and Charging > ..." -> "Starting and Charging"."""
    parts = [p.strip() for p in (section_path or "").split(">")]
    return parts[1] if len(parts) > 2 and parts[0] == "Parts and Labor" else ""


def labour_items(conn: sqlite3.Connection, doc_id: str) -> list[dict]:
    """Every labour operation of the car's manual, in the shape
    wis_links.labour_times gives: {group, kg, op, text, minutes, hours},
    plus the page and the warranty hours as printed."""
    def build():
        items = []
        for page_no, title, section, text in _pages(conn, doc_id, "title LIKE ?", "%" + LABOUR_SUFFIX):
            component = (title or "")[:-len(LABOUR_SUFFIX)].strip()
            rows = _table(text)
            head = next((k for k, r in enumerate(rows) if "Standard Hours" in r), None)
            if head is None:
                continue
            std = rows[head].index("Standard Hours")
            warr = next((k for k, c in enumerate(rows[head]) if "Warranty" in c), None)
            action = ""
            for k, r in enumerate(rows[head + 1:]):
                name = r[0] if r else ""
                hours = r[std] if std < len(r) else ""
                if not _NUMBER.match(hours):
                    action = name or action      # "| Replace | | |": the rows below are replaced
                    continue
                # "| Replace | 1.0 |" (the component's own) or "| Ignition Lock Cylinder | 0.7 |"
                what = f"{component}, {name.lower()}" if name and not action else (
                    f"{name}, {action.lower()}" if action else component)
                h = float(hours)
                items.append({"group": _group(section), "kg": "", "op": f"p. {page_no}",
                              "page_no": str(page_no), "text": what,
                              "hours": h, "minutes": round(h * 60),
                              "warranty": r[warr] if warr is not None and warr < len(r) else ""})
        return items
    return _cached(conn, doc_id, "labour", build)


def labour(conn: sqlite3.Connection, doc_id: str, q: str = "", alt: str = "",
           limit: int = 200) -> dict:
    """{"total", "best", "items"} for the question's words, as WIS's tab."""
    items = labour_items(conn, doc_id)
    queries = [ws for ws in (re.findall(r"\w+", (s or "").lower()) for s in (q, alt)) if ws]
    if queries:
        return _rank_labour(items, queries, limit)
    return {"total": len(items), "best": 0, "items": items[:limit]}


def parts_pages(conn: sqlite3.Connection, doc_id: str) -> list[dict]:
    """The car's "Parts Information" pages: [{page_no, component, section_path,
    rows: [{part, maker, number, price, notes}]}]."""
    def build():
        out = []
        for page_no, title, section, text in _pages(conn, doc_id, "title = ?", "Parts Information"):
            rows = _table(text)
            head = next((k for k, r in enumerate(rows) if "OEM Part #" in r), None)
            if head is None:
                continue
            cols = rows[head]
            ix = {k: cols.index(k) if k in cols else None
                  for k in ("Part", "Manufacturer", "OEM Part #", "Price", "Notes")}

            def cell(r, k):
                i = ix[k]
                return r[i] if i is not None and i < len(r) else ""
            parts = [{"part": cell(r, "Part"), "maker": cell(r, "Manufacturer"),
                      "number": cell(r, "OEM Part #"), "price": cell(r, "Price"),
                      "notes": cell(r, "Notes")}
                     for r in rows[head + 1:] if cell(r, "Part") or cell(r, "OEM Part #")]
            path = [p.strip() for p in (section or "").split(">")]
            component = path[-2] if len(path) > 1 else ""
            if parts:
                out.append({"page_no": str(page_no), "component": component,
                            "section_path": section or "", "rows": parts})
        return out
    return _cached(conn, doc_id, "parts", build)


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def parts(conn: sqlite3.Connection, doc_id: str, queries: list[str], limit: int = 12) -> list[dict]:
    """Parts pages whose component or part names hold the most of a query's
    words, best first; each with its matching rows (all rows when the
    component itself matched)."""
    scored = []
    for page in parts_pages(conn, doc_id):
        comp = {_stem(w) for w in re.findall(r"\w+", page["component"].lower())}
        best = (0, [])
        for q in queries:
            words = {_stem(w) for w in re.findall(r"[a-z0-9]{3,}", (q or "").lower())} - _DROP
            if not words:
                continue
            rows = [r for r in page["rows"]
                    if words & {_stem(w) for w in re.findall(r"\w+", r["part"].lower())}]
            hit = len(words & comp)
            score = max(hit, max((len(words & {_stem(w) for w in re.findall(r"\w+", r["part"].lower())})
                                  for r in rows), default=0))
            if score > best[0]:
                best = (score, page["rows"] if hit else rows)
        if best[0]:
            scored.append((-best[0], page["page_no"], page, best[1]))
    scored.sort(key=lambda s: (s[0], s[1]))
    return [{"page_no": p["page_no"], "doc_id": doc_id, "title": f"{p['component']}: Parts Information",
             "section_path": p["section_path"], "applicability": "this_vehicle",
             "lemon_parts": rows} for _, _, p, rows in scored[:limit]]


_DROP = {"part", "number", "the", "for", "and", "what", "replace", "price", "oem", "how", "much"}
