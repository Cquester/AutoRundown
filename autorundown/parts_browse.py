"""The car's parts diagrams, the way the parts catalogue itself is read:
section (the car's chassis, each of its engines, its options) > group >
subgroup, each subgroup a page with its drawing(s) and its parts list.

Built 2026-10-17 at the user's request ("parts search using EPC diagrams").
Keyword search finds a part when you know its name; a mechanic with the car
in front of him often knows only where it sits ("the bracket by the engine
mount"), which is what the drawings are for. Nothing new is extracted: the
EPC pull already writes one page per (catalog, group, subgroup) titled

    Parts: model 210 (EPC catalog 15F), group 24 ENGINE SUSPENSION / subgroup 001 ...
    Parts: engine 112 (EPC catalog 9A1), group 01 CRANKCASE / subgroup 010 ...
    Parts: option SA 10372 (code 422), model 111 / subgroup 001 POWER STEERING

with its drawings in `images`. Pages come from the car's parts scope only
(search.scope_ids / split_scope), filtered in SQL exactly as search filters
(search._filters: the chosen engine and model codes; search.parts_codes_clause:
a page whose section line lists model codes must list the car's), so a W210
with an M112 chosen lists the M112 catalog, never the M104's, and a 190 E
2.3-16 only the 201.034 catalogs.

A series has one catalog per market and period (the W124: 4,178 parts pages,
582 distinct titles), so a subgroup is listed once with every page that has
it (`pages`), the first opened by default.
"""
from __future__ import annotations

import re
import sqlite3

from .search import _filters, as_ids, catalog_info, parts_codes_clause

_TITLE = re.compile(
    r"^Parts: (?P<kind>model|engine) (?P<code>\S+) \(EPC catalog (?P<cat>[^)]+)\), "
    r"group (?P<g>\w+)\s*(?P<gname>.*?) / subgroup (?P<s>\w+)\s*(?P<sname>.*)$")
_OPTION = re.compile(
    r"^Parts: option SA (?P<sa>\d+)(?: \(code (?P<codes>[^)]*)\))?, .*? / subgroup (?P<s>\w+)\s*(?P<sname>.*)$")
MAX_PAGES = 20000      # a whole generation with every engine is ~6k


def parse_title(title: str) -> dict | None:
    """{section, group, gname, sub, sname, catalog} or None (not a parts page)."""
    m = _TITLE.match(title or "")
    if m:
        what = "Chassis" if m["kind"] == "model" else "Engine"
        return {"section": f"{what} {m['code']}", "group": m["g"],
                "gname": m["gname"].strip(), "sub": m["s"], "sname": m["sname"].strip(),
                "catalog": m["cat"]}
    m = _OPTION.match(title or "")
    if m:
        codes = (m["codes"] or "").strip()
        return {"section": "Options", "group": f"SA {m['sa']}",
                "gname": f"code {codes}" if codes else "", "sub": m["s"],
                "sname": m["sname"].strip(), "catalog": ""}
    return None


def _section_key(name: str) -> tuple:
    return ({"Chassis": 0, "Engine": 1}.get(name.split()[0], 2), name)


def diagrams(conn: sqlite3.Connection, parts_scope) -> list[dict]:
    """[{section, groups: [{group, name, subs: [{sub, name, pages: [{page_no,
    doc_id, drawings}]}]}]}] for the car's parts scope."""
    ids = as_ids(parts_scope)
    if not ids:
        return []
    codes = getattr(parts_scope, "codes", ((), ())) or ((), ())
    where, args, _, _ = _filters(False, tuple(getattr(parts_scope, "engines", ()) or ()), codes)
    pclause, pargs = parts_codes_clause(codes)     # only the car's own catalogues
    where, args = where + pclause, [*args, *pargs]
    rows = conn.execute(
        f"SELECT p.doc_id, p.page_no, p.title, p.images FROM pages p "
        f"WHERE p.doc_id IN ({','.join('?' * len(ids))}){where} LIMIT {MAX_PAGES}",
        [*ids, *args]).fetchall()
    sections: dict[str, dict] = {}
    for doc_id, page_no, title, images in rows:
        t = parse_title(title)
        if not t:
            continue
        sec = sections.setdefault(t["section"], {})
        grp = sec.setdefault(t["group"], {"name": t["gname"], "subs": {}})
        if not grp["name"] and t["gname"]:
            grp["name"] = t["gname"]
        sub = grp["subs"].setdefault((t["sub"], t["sname"]), [])
        sub.append({"page_no": page_no, "doc_id": doc_id, "catalog": t["catalog"],
                    "drawings": len([i for i in (images or "").split("\n") if i])})
    # a subgroup in several catalogues (market x period): each one named by
    # its market too, so the browse list can offer them all, not the first
    market: dict = {}
    for sec in sections.values():
        for grp in sec.values():
            for pages in grp["subs"].values():
                for p in pages if len(pages) > 1 else ():
                    k = (p["doc_id"], p["catalog"])
                    if k not in market:
                        market[k] = catalog_info(conn, {
                            "page_uid": f"{p['doc_id']}:{p['page_no']}",
                            "title": f"(EPC catalog {p['catalog']})"}, codes)["market"]
                    p["market"] = market[k]
    out = []
    for name in sorted(sections, key=_section_key):
        groups = []
        for g in sorted(sections[name]):
            subs = sections[name][g]["subs"]
            groups.append({"group": g, "name": sections[name][g]["name"],
                           "subs": [{"sub": s, "name": n,
                                     # the page with drawings first
                                     "pages": sorted(subs[(s, n)], key=lambda p: -p["drawings"])}
                                    for s, n in sorted(subs)]})
        out.append({"section": name, "groups": groups})
    return out
