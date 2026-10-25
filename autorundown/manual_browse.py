"""The car's manual as contents, for the Manual page tab before a question.

Built 2026-10-24 at the user's request ("change the Manual page to be like
the parts page ... it will auto fill the page in sections for the car"),
next to parts_browse.py. Nothing new is extracted:

* a LEMON (or folder) manual files every page under its own chapter path
  (`section_path`, "Repair and Diagnosis > Engine, Cooling and Exhaust >
  ..."), which is the tree as the manual reads;
* a WIS page's `section_path` names only the models it fits, so WIS pages
  are grouped by their document number instead: "AR42.10-P-0100A" is
  function group 42 and subgroup 42.10, named by wis_links.group_names
  (WIS's Gruppen table; the labour book's group names before it is
  pulled). Pages with no number go under "Other documents".

The pages are the car's manual scope (search.scope_ids / split_scope),
filtered in SQL exactly as search filters them (search._filters; the general
pool narrowed by vehicle type), so what is listed is what a search for this
car can find. A WIS car has tens of thousands of pages, so the tree is built
once per car and handed out a level at a time (`node`).
"""
from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path

from . import wis_links
from .search import _filters, _types_clause, as_ids

MAX_PAGES = 120000
_DOCNR = re.compile(r"^([a-z]{2})(\d{2})(\d{2})")
# what a WIS document is, where its number or type says so plainly
KIND = {"ar": "Repair", "gf": "Function", "si": "Service information",
        "bf": "Capacities"}
DOC_TYPE = {"wiring": "Wiring diagram", "spec_table": "Values"}
OTHER = "Other documents"

_lock = threading.Lock()
_cache: dict[tuple, dict] = {}


def _new() -> dict:
    return {"children": {}, "pages": [], "count": 0}


def _add(root: dict, path: list[str], page: dict) -> None:
    node = root
    node["count"] += 1
    for name in path:
        node = node["children"].setdefault(name, _new())
        node["count"] += 1
    node["pages"].append(page)


def _rows(conn: sqlite3.Connection, scope) -> list[tuple]:
    ids = as_ids(scope)
    if not ids:
        return []
    where, args, _, _ = _filters(False, tuple(getattr(scope, "engines", ()) or ()),
                                 getattr(scope, "codes", ((), ())) or ((), ()))
    general = getattr(scope, "general", "")
    tclause, targs = _types_clause(getattr(scope, "types", "") or "")
    if tclause and general in ids:
        where += f" AND (p.doc_id != ? OR {tclause})"
        args = [*args, general, *targs]
    return conn.execute(
        f"SELECT p.doc_id, p.page_no, p.title, p.section_path, p.doc_type FROM pages p "
        f"WHERE p.doc_id IN ({','.join('?' * len(ids))}){where} LIMIT {MAX_PAGES}",
        [*ids, *args]).fetchall()


def _wis_tree(rows: list[tuple], corpus: Path) -> dict:
    root, seen = _new(), set()
    rows = [r for r in rows if not (r[1] in seen or seen.add(r[1]))]   # once per document
    numbers = wis_links.docnrs(corpus, [int(r[1]) for r in rows if str(r[1]).isdigit()])
    names = wis_links.group_names(corpus)
    for doc_id, page_no, title, _, doc_type in rows:
        nrs = sorted(numbers.get(int(page_no), []) if str(page_no).isdigit() else [])
        m = next((m for m in map(_DOCNR.match, nrs) if m), None)
        # a wiring diagram says so; else the number's kind ("AR": a repair
        # procedure, though it has a table), then "has a table", then the code
        kind = (DOC_TYPE["wiring"] if doc_type == "wiring" else "") or (KIND.get(m[1]) if m else "") \
            or DOC_TYPE.get(doc_type) or (m[1].upper() if m else "")
        page = {"page_no": page_no, "doc_id": doc_id, "title": (title or page_no).strip(), "kind": kind}
        if m:
            kg = m[2]
            sub = f"{kg}.{m[3]}"
            _add(root, [f"{kg} {names.get(kg, '')}".strip(), f"{sub} {names.get(sub, '')}".strip()], page)
        else:
            _add(root, [OTHER, DOC_TYPE.get(doc_type, "Manual pages")], page)
    return root


def _chapter_tree(rows: list[tuple]) -> dict:
    root = _new()
    for doc_id, page_no, title, section_path, doc_type in rows:
        path = [s.strip() for s in (section_path or "").split(" > ") if s.strip()]
        # the page's own title usually ends its path: list it in its chapter
        if path and title and (title.strip() == path[-1] or title.strip().endswith(": " + path[-1])):
            path = path[:-1]
        _add(root, path, {"page_no": page_no, "doc_id": doc_id, "title": (title or page_no).strip(),
                          "kind": DOC_TYPE.get(doc_type, "")})
    return root


def _sort_key(name: str):
    m = re.match(r"(\d+)(?:\.(\d+))?", name)
    return (name == OTHER, (int(m[1]), int(m[2] or 0)) if m else (10 ** 6, 0), name.lower())


def tree(conn: sqlite3.Connection, vehicle: dict, scope, wis: bool) -> dict:
    """The whole contents tree of this car (cached per car and index state)."""
    state = conn.execute("SELECT COUNT(*), MAX(rowid) FROM pages").fetchone()
    side = Path(vehicle.get("file_path") or "") / wis_links.NAME     # rebuilt by parse_links.py
    state = (*state, side.stat().st_mtime if wis and side.is_file() else 0)
    key = (vehicle["doc_id"], tuple(as_ids(scope)), getattr(scope, "engines", ()),
           getattr(scope, "codes", ()), getattr(scope, "types", ""), wis, tuple(state))
    with _lock:
        if key in _cache:
            return _cache[key]
    rows = _rows(conn, scope)
    t = _wis_tree(rows, Path(vehicle.get("file_path") or "")) if wis else _chapter_tree(rows)
    with _lock:
        if len(_cache) > 8:
            _cache.clear()
        _cache[key] = t
    return t


def node(t: dict, path: list[str]) -> dict | None:
    """One level of the tree: {path, count, children: [{name, count}], pages}.
    At the top, a single chapter holding everything is opened by itself."""
    n = t
    for name in path:
        n = n["children"].get(name)
        if n is None:
            return None
    path = list(path)
    if not path:
        while len(n["children"]) == 1 and not n["pages"]:
            (name, n), = n["children"].items()
            path.append(name)
    return {"path": path, "count": n["count"],
            "children": [{"name": k, "count": n["children"][k]["count"]}
                         for k in sorted(n["children"], key=_sort_key)],
            "pages": sorted(n["pages"], key=lambda p: (p["kind"], (p["title"] or "").lower()))}
