"""Is this corpus already in the index, unchanged? (2026-11-05)

    python corpus_stamp.py --check  <corpus dir> --index <index.db>
    python corpus_stamp.py --record <corpus dir> --index <index.db>

Shop setup's Mercedes VM import used to re-import WIS (`rebuild_wis.py`)
and EPC (remove + ingest) on every run, even when the extraction had
nothing new -- most of an hour. Now the job runs `--check` first: exit 10
("unchanged") ends that part with the index left as it is; exit 0 means
import. After a successful import it runs `--record`.

The stamp is `indexed.json` in the corpus folder: per index file, a
fingerprint of what the importer reads -- every file directly in the folder
(contents hashed when under 32 MB, else size + modified time: records.jsonl
is rewritten only by the build scripts that change it), and for WIS the
`docnr` table of `wis_links.sqlite` (the importer turns cross-references
into links with it; the rest of that file is read live by the server, so a
new labour or bulletin table needs no import). It also checks the index
still holds that source, complete, so a removed import is imported again.
Read-only on the index.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

STAMP = "indexed.json"
UNCHANGED = 10                     # importer_jobs.UNCHANGED
SMALL = 32 * 1024 * 1024
SKIP_SUFFIXES = (".log", ".part", ".tmp")
SKIP_NAMES = {STAMP, "wis_links.sqlite", "wis_links.sqlite-journal"}


def corpora(root: Path) -> list[Path]:
    """The corpus folders under `root`: itself if it holds manifest.json,
    else its subfolders that do (the records importer reads both layouts;
    the sample corpus is wis_extract/wis_en)."""
    root = Path(root)
    if (root / "manifest.json").is_file():
        return [root]
    if not root.is_dir():
        return []
    return sorted(d for d in root.iterdir() if d.is_dir() and (d / "manifest.json").is_file())


def fingerprint(root: Path) -> str:
    h = hashlib.sha256()
    for c in corpora(root):
        h.update(f"[{c.name}]".encode())
        _one(h, c)
    return h.hexdigest()


def _one(h, corpus: Path) -> None:
    for p in sorted(Path(corpus).iterdir()):
        if not p.is_file() or p.name in SKIP_NAMES or p.name.endswith(SKIP_SUFFIXES):
            continue
        st = p.stat()
        h.update(f"{p.name}\0{st.st_size}\0".encode())
        if st.st_size < SMALL:
            h.update(hashlib.sha256(p.read_bytes()).digest())
        else:
            h.update(str(st.st_mtime_ns).encode())
    side = Path(corpus) / "wis_links.sqlite"
    if side.is_file():
        try:
            db = sqlite3.connect(f"file:{side.as_posix()}?mode=ro", uri=True)
            try:
                for row in db.execute("SELECT docnr, dok_nr_id, dok_id FROM docnr ORDER BY docnr, dok_id"):
                    h.update(repr(row).encode())
            finally:
                db.close()
        except sqlite3.Error:
            h.update(b"no docnr")


def source_of(corpus: Path) -> str:
    """'mercedes-wis' / 'mercedes-epc' (the vehicles' source_name prefix)."""
    try:
        m = json.loads((Path(corpus) / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return "-".join(str(m.get("source_type") or "").split("-")[:2])


def in_index(index: Path, source: str) -> bool:
    """The index holds this source, every entry of it completely imported."""
    if not source or not Path(index).is_file():
        return False
    try:
        db = sqlite3.connect(f"file:{Path(index).as_posix()}?mode=ro", uri=True)
        try:
            rows = db.execute("SELECT complete, indexed_pages FROM vehicles WHERE source_name LIKE ?",
                              (source + "%",)).fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return False
    return bool(rows) and all(c for c, _ in rows) and sum(n or 0 for _, n in rows) > 0


def _key(index: Path) -> str:
    return str(Path(index).resolve()).lower()


def _stamps(corpus: Path) -> dict:
    try:
        data = json.loads((Path(corpus) / STAMP).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def check(corpus: Path, index: Path) -> tuple[bool, str]:
    """(unchanged, why)."""
    e = _stamps(corpus).get(_key(index))
    if not e:
        return False, "not imported into this index from here yet"
    if not all(in_index(index, source_of(c)) for c in corpora(corpus)):
        return False, "the index no longer holds it all"
    if e.get("fingerprint") != fingerprint(corpus):
        return False, "the corpus changed"
    return True, f"imported {e.get('at', '?')}"


def record(corpus: Path, index: Path) -> None:
    data = _stamps(corpus)
    data[_key(index)] = {"fingerprint": fingerprint(corpus), "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    tmp = Path(corpus) / (STAMP + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(Path(corpus) / STAMP)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", metavar="CORPUS")
    g.add_argument("--record", metavar="CORPUS")
    ap.add_argument("--index", required=True)
    args = ap.parse_args(argv)
    corpus = Path(args.check or args.record)
    if not corpora(corpus):
        # nothing to compare: import (the importer says what is wrong), and
        # nothing to record
        print(f"{corpus}: no corpus there (no manifest.json)")
        return 0
    if args.record:
        record(corpus, Path(args.index))
        print(f"recorded: {corpus.name} is in the index as it is now")
        return 0
    same, why = check(corpus, Path(args.index))
    if same:
        print(f"UNCHANGED {corpus.name}: nothing new since it was {why}; the index is left as it is")
        return UNCHANGED
    print(f"importing {corpus.name}: {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
