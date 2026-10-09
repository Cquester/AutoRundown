"""The LEMON catalogue: every vehicle in the LEMON torrent, to browse by
make / year / model / engine and pick what to download later.

Where it comes from, in order:
1. the torrent's own `lemon/index.json`, when it is on this PC (under
   `config.lemon_torrent_dir`) -- the live list;
2. the snapshot committed in `importers/data/lemon_catalogue.json.gz`
   (built by tools/build_lemon_catalogue.py from that same file) -- so the
   list works before anything is downloaded.

The torrent cannot hand over one vehicle: pages.mtbl is sorted by content
type and hash, so a car's pages are spread over the whole 31.5 GB file. This
module only lists and remembers picks; downloading is a later step.

"imported" is exact for vehicles named the LEMON way: the `lemon` importer
files a car as slug(year-make-variant) and the variant is "model engine".
Manuals downloaded elsewhere under another name (e.g. "190E 2.3-16 (201.034)
L4-2.3L (102.983)") don't match a catalogue row and show as new.
"""
from __future__ import annotations

import gzip
import json
import sqlite3
import threading
from pathlib import Path

from . import config
from .importers.lemon import _slug

SNAPSHOT = Path(__file__).resolve().parent / "importers" / "data" / "lemon_catalogue.json.gz"
PICKS = config.APP_DIR / "lemon_picks.json"

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
            _cache.update(key=key, cat=cat)
        return _cache["cat"]


def vehicle_name(model: str, engine: str) -> str:
    return f"{model} {engine}" if engine else model


def doc_id(make: str, year: str, model: str, engine: str) -> str:
    """The doc_id the `lemon` importer gives this car (importers/lemon.py)."""
    return _slug(f"{year}-{make}-{vehicle_name(model, engine)}")


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


def picks() -> set[str]:
    try:
        return set(json.loads(PICKS.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def set_picks(ids: list[str], picked: bool) -> int:
    cur = picks()
    cur = cur | set(ids) if picked else cur - set(ids)
    PICKS.parent.mkdir(parents=True, exist_ok=True)
    PICKS.write_text(json.dumps(sorted(cur), indent=0), encoding="utf-8")
    return len(cur)


def browse(index_path: str | Path, make: str = "", year: str = "", model: str = "") -> dict:
    """Drop-down options for the choices so far, plus the vehicle rows once a
    make and year are chosen (all of a make's years at once would be up to
    ~46,000 rows)."""
    cat = load()
    makes = cat["makes"]
    out: dict = {
        "source": cat["source"], "total": cat["vehicles"],
        "makes": [{"name": m, "count": sum(len(e) for ys in makes[m].values() for e in ys.values())}
                  for m in sorted(makes, key=str.lower)],
        "years": [], "models": [], "rows": [], "picked": len(picks()),
    }
    if make not in makes:
        return out
    years = makes[make]
    out["years"] = sorted(years, reverse=True)
    if year not in years:
        return out
    models = years[year]
    out["models"] = [{"name": m, "count": len(e)} for m, e in models.items()]
    have = _indexed(index_path)
    chosen = picks()
    rows = []
    for m, engines in models.items():
        if model and m != model:
            continue
        for e in engines:
            d = doc_id(make, year, m, e)
            state = ("imported" if have.get(d) else "partial" if d in have else
                     "picked" if d in chosen else "new")
            rows.append({"make": make, "year": year, "model": m, "engine": e,
                         "name": vehicle_name(m, e), "doc_id": d, "state": state})
    out["rows"] = rows
    return out
