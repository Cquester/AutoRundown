"""The LEMON catalogue: every vehicle in the LEMON torrent, as a folder tree
(make > year > model > variant) to pick what to download.

Where it comes from, in order:
1. the torrent's own `lemon/index.json`, when it is on this PC (under
   `config.lemon_torrent_dir`) -- the live list;
2. the snapshot committed in `importers/data/lemon_catalogue.json.gz`
   (built by tools/build_lemon_catalogue.py from that same file) -- so the
   list works before anything is downloaded.

The torrent cannot hand over one vehicle: pages.mtbl is sorted by content
type and hash, so a car's pages are spread over the whole 31.5 GB file. This
module only lists and remembers picks; downloading is a later step.

A pick is a folder path, not a list of cars: ("Mercedes Benz",) picks every
Mercedes, ("Mercedes Benz", "1987", "190E", "2.3, 102.985, Automatic") one
variant. Picks never overlap: picking a folder drops the picks inside it, and
unpicking something inside a picked folder splits that folder into its other
children, level by level. So the file stays a handful of paths, and "picked"
for any folder is all / some / none without a list of 280,000 doc_ids.

"imported" is exact for vehicles named the LEMON way: the `lemon` importer
files a car as slug(year-make-variant) and the variant is "model engine".
Manuals downloaded elsewhere under another name (e.g. "190E 2.3-16 (201.034)
L4-2.3L (102.983)") don't match a catalogue row and show as new.
"""
from __future__ import annotations

import gzip
import json
import re
import sqlite3
import threading
import time
from functools import lru_cache
from pathlib import Path

from . import config
from .importers.lemon import _slug

SNAPSHOT = Path(__file__).resolve().parent / "importers" / "data" / "lemon_catalogue.json.gz"
PICKS = config.APP_DIR / "lemon_picks.json"
LEVELS = ("make", "year", "model", "variant")

_lock = threading.Lock()
_cache: dict = {}


def torrent_index() -> Path | None:
    """The torrent's lemon/index.json on this PC, if there is one."""
    base = config.load().lemon_torrent_dir
    if not base:
        return None
    root = Path(base).expanduser()
    for rel in ("lemon/index.json", "lemon-manuals/lemon/index.json", "index.json"):
        p = root / rel
        if p.is_file():
            return p
    return None


def _from_index_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("database") != "lemon":
        raise ValueError(f"{path} is not a LEMON index.json")
    makes: dict = {}
    for v in data["vehicles"]:
        for y in v["years"]:
            makes.setdefault(v["make"], {}).setdefault(str(y), {}).setdefault(
                v["model"], []).append(v.get("engine") or "")
    return {"source": f"torrent index.json ({path})", "vehicles": len(data["vehicles"]),
            "makes": makes}


def vehicle_name(model: str, engine: str) -> str:
    return f"{model} {engine}" if engine else model


def doc_id(make: str, year: str, model: str, engine: str) -> str:
    """The doc_id the `lemon` importer gives this car (importers/lemon.py)."""
    return _slug(f"{year}-{make}-{vehicle_name(model, engine)}")


# how often load() looks at the source file again: the car box asks the
# catalogue as people type, and a stat of the torrent folder per keystroke
# buys nothing -- index.json changes only when the torrent is re-fetched
RECHECK_S = 300.0


def load() -> dict:
    """The catalogue, cached until its source file changes (looked at again
    at most every RECHECK_S seconds)."""
    now, where = time.monotonic(), config.load().lemon_torrent_dir
    with _lock:
        if ("cat" in _cache and _cache.get("dir") == where
                and now - _cache.get("checked", 0) < RECHECK_S):
            return _cache["cat"]
    live = torrent_index()
    src = live or SNAPSHOT
    key = (str(src), src.stat().st_mtime if src.is_file() else 0)
    with _lock:
        _cache.update(checked=now, dir=where)
        if _cache.get("key") != key:
            if live:
                cat = _from_index_json(live)
            else:
                cat = json.loads(gzip.decompress(SNAPSHOT.read_bytes()).decode("utf-8"))
                cat["source"] = f"snapshot of the torrent's index.json, {cat.get('built', '?')}"
            _cache.update(key=key, cat=cat, counts={}, where={})
        return _cache["cat"]


def locate(cat: dict, doc: str) -> tuple | None:
    """The 4-part tree path of a LEMON doc_id ("1987-mercedes-benz-190e-…"),
    or None. Only that year's makes, and the one make the id names, are
    slugged: mapping all 279,988 cars up front took ~1.7 s and ~90 MB on the
    first open of the Import dialog."""
    where = _cache.setdefault("where", {})
    if doc in where:
        return where[doc]
    found = None
    m = re.match(r"(\d{4})-", doc)
    if m:
        year = m.group(1)
        for make, years in cat["makes"].items():
            if year in years and doc.startswith(f"{year}-{_slug(make)}-"):
                for model, engines in years[year].items():
                    for e in engines:
                        if doc_id(make, year, model, e) == doc:
                            found = (make, year, model, e)
                            break
                    if found:
                        break
            if found:
                break
    where[doc] = found
    return found


def _node(cat: dict, path: tuple):
    """The subtree at `path`: a dict for make/year, a list of engines for a
    model, a variant name for a leaf; None if it doesn't exist."""
    node = cat["makes"]
    for i, part in enumerate(path):
        if i < 3:
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        else:
            return part if part in node else None
    return node


def children(cat: dict, path: tuple) -> list[str]:
    node = _node(cat, path)
    if isinstance(node, dict):
        return list(node)
    if isinstance(node, list):
        return list(dict.fromkeys(node))   # an engine listed twice is one variant
    return []


def count(cat: dict, path: tuple) -> int:
    """Vehicles (one per year) under `path`."""
    counts = _cache.setdefault("counts", {})
    if path not in counts:
        node = _node(cat, path)
        if node is None:
            n = 0
        elif len(path) == 4:
            n = 1
        elif len(path) == 3:
            n = len(set(node))
        else:
            n = sum(count(cat, path + (c,)) for c in node)
        counts[path] = n
    return counts[path]


def picks() -> set[tuple]:
    try:
        raw = json.loads(PICKS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {tuple(p) for p in raw if isinstance(p, list) and 1 <= len(p) <= 4}


def _save(ps: set[tuple]) -> None:
    PICKS.parent.mkdir(parents=True, exist_ok=True)
    PICKS.write_text(json.dumps(sorted(list(p) for p in ps), ensure_ascii=False, indent=0),
                     encoding="utf-8")


def set_pick(path: tuple, picked: bool) -> set[tuple]:
    """Pick or unpick a folder or a variant; returns the new picks."""
    cat = load()
    if not path or _node(cat, path) is None:
        raise KeyError(f"not in the catalogue: {' / '.join(path) or '(root)'}")
    with _lock:
        ps = picks()
        inside = {p for p in ps if p[:len(path)] == path}       # path itself and below
        above = next((p for p in ps if len(p) < len(path) and path[:len(p)] == p), None)
        ps -= inside
        if picked:
            if above is None:
                ps.add(path)
        elif above is not None:
            # split the picked folder: keep every sibling on the way down
            ps.discard(above)
            for depth in range(len(above), len(path)):
                for c in children(cat, path[:depth]):
                    if c != path[depth]:
                        ps.add(path[:depth] + (c,))
        _save(ps)
        return ps


def _indexed(index_path: str | Path) -> dict[str, bool]:
    """doc_id -> complete, read-only."""
    p = Path(index_path)
    if not p.is_file():
        return {}
    try:
        con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
        try:
            return {d: bool(c) for d, c in con.execute("SELECT doc_id, complete FROM vehicles")}
        finally:
            con.close()
    except sqlite3.Error:
        return {}


def browse(index_path: str | Path, path: tuple = ()) -> dict:
    """One folder of the tree: its children with how many vehicles each holds,
    how many are imported, and whether they are picked (all / some / none)."""
    cat = load()
    if path and _node(cat, path) is None:
        raise KeyError(f"not in the catalogue: {' / '.join(path)}")
    ps = picks()
    have = _indexed(index_path)
    imported: dict[tuple, int] = {}
    for d, complete in have.items():
        where = locate(cat, d) if complete else None
        if where and complete:
            for i in range(1, 5):
                imported[where[:i]] = imported.get(where[:i], 0) + 1

    def picked_n(p: tuple) -> int:
        if any(p[:len(q)] == q for q in ps if len(q) <= len(p)):
            return count(cat, p)
        return sum(count(cat, q) for q in ps if len(q) > len(p) and q[:len(p)] == p)

    rows = []
    for c in children(cat, path):
        p = path + (c,)
        n, k = count(cat, p), picked_n(p)
        row = {"name": c, "path": list(p), "level": LEVELS[len(p) - 1], "count": n,
               "imported": imported.get(p, 0),
               "picked": "all" if k and k == n else "some" if k else "none"}
        if len(p) == 4:
            m, y, mo, e = p
            d = doc_id(m, y, mo, e)
            row.update(name=e or mo, doc_id=d,
                       state="imported" if have.get(d) else "partial" if d in have else "new")
        rows.append(row)
    if len(path) == 1:
        rows.sort(key=lambda r: r["name"], reverse=True)       # newest year first
    total_picked = sum(count(cat, q) for q in ps)
    return {"source": cat["source"], "total": cat["vehicles"], "path": list(path),
            "rows": rows, "picked_total": total_picked, "picks": len(ps)}


def picked_vehicles() -> list[dict]:
    """Every picked vehicle, for the download step."""
    cat = load()
    out = []

    def walk(p: tuple) -> None:
        if len(p) == 4:
            out.append({"make": p[0], "year": p[1], "model": p[2], "engine": p[3],
                        "doc_id": doc_id(*p)})
            return
        for c in children(cat, p):
            walk(p + (c,))

    for q in sorted(picks()):
        if _node(cat, q) is not None:
            walk(q)
    return out


# -- the car box: vehicles in the catalogue that are not downloaded -----------
# The car box suggests the manuals in the index and, after them, cars of the
# LEMON catalogue that are not (the user, 2026-10-26: typing "2016 Ford F-150"
# should offer it, marked "Not downloaded", with "Add this car"). The same
# matching as the box's own (index.html carMatches): every typed word must
# appear, a year narrows to that year, a one-letter word matches a whole word.

@lru_cache(maxsize=65536)
def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


@lru_cache(maxsize=65536)
def _words(s: str) -> frozenset:
    return frozenset(w for w in re.split(r"[^a-z0-9]+", s.lower()) if w)


def _year_of(w: str) -> int | None:
    if re.fullmatch(r"(19|20)\d\d", w):
        return int(w)
    if re.fullmatch(r"'?\d\d", w):
        n = int(w.lstrip("'"))
        return 2000 + n if n <= 30 else 1900 + n
    return None


def search(q: str, index_path: str | Path, limit: int = 6) -> dict:
    """Catalogue cars matching the typed words that the index does not hold,
    best first: {"rows": [{make, year, model, engine, label, doc_id, path}],
    "total": every match, "source": where the list comes from}. Needs a word
    besides the year (a year alone is ~10,000 cars)."""
    year, terms = None, []
    for w in q.lower().split():
        y = _year_of(w)
        if y is not None:
            year = y
        elif _compact(w):
            terms.append(_compact(w))
    cat = load()
    out = {"rows": [], "total": 0, "source": cat["source"]}
    if not terms or sum(len(t) for t in terms) < 2:
        return out
    have = _indexed(index_path)
    hits = []
    for make, years in cat["makes"].items():
        for y, models in years.items():
            if year is not None and y != str(year):
                continue
            for model, engines in models.items():
                head = f"{make} {model}"
                hay_head, w_head = _compact(head), _words(head)
                for e in dict.fromkeys(engines):
                    hay = hay_head + _compact(e)
                    words = w_head | _words(e)
                    if not all(t in words if len(t) == 1 else t in hay for t in terms):
                        continue
                    d = doc_id(make, y, model, e)
                    if d in have:
                        continue
                    # words found in the make/model before those only in the engine
                    in_head = sum(t in w_head if len(t) == 1 else t in hay_head for t in terms)
                    hits.append((-in_head, -int(y), len(model), model, e, make, y, d))
    hits.sort()
    out["total"] = len(hits)
    out["rows"] = [{"make": m, "year": y, "model": mo, "engine": e, "doc_id": d,
                    "label": f"{y} {m} {vehicle_name(mo, e)}", "path": [m, y, mo, e]}
                   for _, _, _, mo, e, m, y, d in hits[:limit]]
    return out
