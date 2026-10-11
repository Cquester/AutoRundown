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


def load() -> dict:
    """The catalogue, cached until its source file changes."""
    live = torrent_index()
    src = live or SNAPSHOT
    key = (str(src), src.stat().st_mtime if src.is_file() else 0)
    with _lock:
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
