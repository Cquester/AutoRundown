"""What the extractor has already pulled and built, so a re-run does only
what is new (2026-11-05, the user: "doing that every time which takes an
hour isn't ideal").

The ledger is one JSON file in the output root, `extraction_state.json`.
It never decides on its own word alone: every entry also records what the
step's files on disk looked like when it finished (name, size, modified
time), so data that was deleted or replaced since -- "Delete all Mercedes
data", a hand copy -- makes the step run again. A step whose script
changed runs again too (its code's SHA-256 is recorded).

  pulls (VM on)   docs, applic, links, epc: done once their pull exits 0;
                  skipped while the script and their output files are the
                  same. Vehicle tables are tracked one by one, by the files
                  themselves: pull_vehicles.js writes `<table>.sql` (the
                  query it ran) next to `<table>.tsv`, and a table is pulled
                  again only if either is missing or the query in
                  `vehicle_tables.json` changed -- so a release that adds
                  tables pulls just those. Images need no entry: the pulled
                  count against the documents' figure list says it.
  build (VM off)  each script runs only when its inputs or its code changed
                  since it last succeeded, or a script before it that writes
                  the same files ran (parse -> apply_applic -> apply_images).
                  parse_vehicles.py and parse_links.py always run (seconds,
                  and they read every vehicle table).

`--full` on vm_pipeline.py ignores all of it (Shop setup: "Pull everything
again").
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

NAME = "extraction_state.json"
VERSION = 1
HERE = Path(__file__).resolve().parent
TABLES_FILE = "vehicle_tables.json"
# never part of a folder's signature: logs, half-written files, and what a
# build script writes back into a pull's folder
SKIP_SUFFIXES = (".log", ".part", ".tmp")
SKIP_NAMES = {"doc_applicability.tsv", "indexed.json", "list.tsv"}


class Dirs:
    """The output folders vm_pipeline.py uses, under one root."""

    def __init__(self, out: Path):
        self.out = Path(out)
        self.extract = self.out / "wis_extract"
        self.vehicles = self.out / "wis_vehicles"
        self.applic2 = self.out / "wis_applic2"
        self.images = self.out / "wis_images"
        self.links = self.out / "wis_links"
        self.epc = self.out / "epc_extract"


# a pull step: its scripts, and the files it writes (a folder = the files
# directly in it)
PULLS = {
    "docs": (["pull.js"], lambda d: [d.extract / "bodies.pack", d.extract / "metadata.tsv",
                                     d.extract / "figures.tsv"]),
    "applic": (["pull_applic.js", "pull_applic2.js"],
               lambda d: [d.extract / "baumusterzeilen.tsv", d.extract / "dok2reihe.tsv", d.applic2]),
    "links": (["pull_links.js"], lambda d: [d.links]),
    "epc": (["pull_epc.js", "epc_fv4.js"],
            lambda d: [d.epc / "records.jsonl", d.epc / "manifest.json", d.epc / "done_catalogs.txt"]),
}

# a build script: its code, what it reads, the scripts before it whose
# output it rewrites (if one ran, this one runs), and what must exist after
# it (missing -> run). `always` scripts run every time.
BUILD = {
    "parse.py": {"code": ["parse.py", "parse_sids.py"],
                 "in": lambda d: PULLS["docs"][1](d), "out": lambda d: [d.extract / "records.jsonl"]},
    "apply_applic.py": {"code": ["apply_applic.py"],
                        "in": lambda d: PULLS["applic"][1](d), "after": ["parse.py"]},
    "parse_vehicles.py": {"always": True},
    "parse_links.py": {"always": True},
    "convert_images.py": {"code": ["convert_images.py"],
                          "in": lambda d: [d.images / "pull_images.tsv"],
                          "out": lambda d: [d.extract / "images" / "catalog.tsv"]},
    "apply_images.py": {"code": ["apply_images.py"],
                        "in": lambda d: [d.extract / "images" / "catalog.tsv"],
                        "after": ["parse.py", "apply_applic.py", "convert_images.py"]},
}


# -- the file ----------------------------------------------------------------

def load(out: Path) -> dict:
    """The ledger, or an empty one (none yet, unreadable, another version)."""
    try:
        state = json.loads((Path(out) / NAME).read_text(encoding="utf-8"))
        if isinstance(state, dict) and state.get("version") == VERSION:
            state.setdefault("pulls", {})
            state.setdefault("build", {})
            return state
    except (OSError, ValueError):
        pass
    return {"version": VERSION, "pulls": {}, "build": {}}


def save(out: Path, state: dict) -> None:
    """Written whole and renamed into place: a crash never leaves half a file."""
    path = Path(out) / NAME
    tmp = path.with_name(NAME + ".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# -- signatures --------------------------------------------------------------

def code_sig(names: list[str], here: Path = HERE) -> str:
    """SHA-256 over the named scripts' contents (a missing one counts too)."""
    h = hashlib.sha256()
    for name in names:
        h.update(name.encode() + b"\0")
        try:
            h.update((here / name).read_bytes())
        except OSError:
            h.update(b"<missing>")
    return h.hexdigest()


def _entries(path: Path) -> list[str]:
    if path.is_dir():
        out = []
        for p in sorted(path.iterdir()):
            if p.is_file() and not p.name.endswith(SKIP_SUFFIXES) and p.name not in SKIP_NAMES:
                st = p.stat()
                out.append(f"{path.name}/{p.name}:{st.st_size}:{st.st_mtime_ns}")
        return out or [f"{path.name}/:empty"]
    if path.is_file():
        st = path.stat()
        return [f"{path.name}:{st.st_size}:{st.st_mtime_ns}"]
    return [f"{path.name}:-"]


def files_sig(paths: list[Path]) -> str:
    """What the files look like now: name, size and modified time of each
    (a folder: the files directly in it). Never reads the contents, so it
    costs nothing on records.jsonl's 861 MB."""
    return hashlib.sha256("\n".join(e for p in paths for e in _entries(Path(p))).encode()).hexdigest()


def outputs_present(paths: list[Path]) -> bool:
    return all(Path(p).is_file() or (Path(p).is_dir() and any(Path(p).iterdir())) for p in paths)


# -- pulls -------------------------------------------------------------------

def pull_done(state: dict, step: str, d: Dirs, args: str = "") -> str | None:
    """When the pull finished, if it is done and nothing about it changed
    since; None if it has to run."""
    scripts, outs = PULLS[step]
    e = state["pulls"].get(step)
    paths = outs(d)
    if not e or not outputs_present(paths):
        return None
    if (e.get("code") != code_sig(scripts) or e.get("files") != files_sig(paths)
            or e.get("args", "") != args):
        return None
    return e.get("at") or "?"


def mark_pull(state: dict, step: str, d: Dirs, args: str = "") -> None:
    scripts, outs = PULLS[step]
    state["pulls"][step] = {"at": now(), "code": code_sig(scripts),
                            "files": files_sig(outs(d)), "args": args}


def forget_pull(state: dict, step: str) -> None:
    state["pulls"].pop(step, None)


# -- vehicle tables ----------------------------------------------------------

def vehicle_tables(lang: int | str, here: Path = HERE) -> list[tuple[str, str]]:
    """(table, the query pull_vehicles.js runs for it), from vehicle_tables.json
    -- the one list both the pull and this check read."""
    spec = json.loads((here / TABLES_FILE).read_text(encoding="utf-8"))
    lang = int(lang)
    lang2 = f"{lang:02d}"
    out = []
    for t in spec["tables"]:
        where = (t.get("where") or "").replace("{LANG2}", lang2).replace("{LANG}", str(lang))
        out.append((t["name"], f"SELECT * FROM {t['name']}" + (f" WHERE {where}" if where else "")))
    return out


def vehicle_tables_needed(veh: Path, lang: int | str, here: Path = HERE) -> list[str]:
    """The tables to pull: no .tsv, or no .sql beside it, or a different
    query than the list now asks for."""
    need = []
    for name, sql in vehicle_tables(lang, here):
        tsv, done = Path(veh) / f"{name}.tsv", Path(veh) / f"{name}.sql"
        try:
            same = tsv.is_file() and done.read_text(encoding="utf-8").strip() == sql
        except OSError:
            same = False
        if not same:
            need.append(name)
    return need


# -- build -------------------------------------------------------------------

def build_reason(state: dict, script: str, d: Dirs, ran: set[str]) -> str | None:
    """Why `script` has to run now, or None to skip it."""
    spec = BUILD[script]
    if spec.get("always"):
        return "always runs"
    hit = [a for a in spec.get("after", []) if a in ran]
    if hit:
        return f"{hit[0]} ran"
    e = state["build"].get(script)
    if not e:
        return "not built yet"
    if not outputs_present(spec.get("out", lambda d: [])(d)):
        return "its output is missing"
    if e.get("code") != code_sig(spec["code"]):
        return "the script changed"
    if e.get("in") != files_sig(spec["in"](d)):
        return "new input"
    return None


def forget_build(state: dict, script: str) -> None:
    """A script that failed half-way: its next run must not be skipped."""
    state["build"].pop(script, None)


def mark_build(state: dict, script: str, d: Dirs) -> None:
    spec = BUILD[script]
    if spec.get("always"):
        state["build"][script] = {"at": now()}
        return
    state["build"][script] = {"at": now(), "code": code_sig(spec["code"]),
                              "in": files_sig(spec["in"](d))}


# -- what Shop setup shows ---------------------------------------------------

def status(out: Path, lang: int | str = 2, here: Path = HERE) -> dict:
    """What has been extracted, for a person: each pull's date (or None),
    vehicle tables present / wanted, and the last build of each script."""
    d, state = Dirs(out), load(out)
    pulls = {}
    for step in PULLS:
        e = state["pulls"].get(step)
        pulls[step] = {"at": e.get("at") if e else None,
                       "current": bool(pull_done(state, step, d, e.get("args", "") if e else ""))}
    try:
        wanted = [n for n, _ in vehicle_tables(lang, here)]
        need = vehicle_tables_needed(d.vehicles, lang, here)
    except (OSError, ValueError, KeyError):
        wanted, need = [], []
    return {"pulls": pulls,
            "vehicle_tables": {"wanted": len(wanted), "pulled": len(wanted) - len(need), "missing": need},
            "build": {k: v.get("at") for k, v in state["build"].items()}}
