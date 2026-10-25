"""Remove manuals from the index -- the Import dialog's Remove buttons run this.

    python remove_manuals.py --doc-id <id> [--doc-id <id> ...] [--index index.db]
    python remove_manuals.py --source mercedes-wis [--index index.db]
    add --compact to shrink the file afterwards (FTS optimize + VACUUM: slow
    on a big index, needs free disk about the size of the index)
    add --recycle <folder> (repeatable) to send extract folders to the
    Recycle Bin afterwards -- Shop setup's "Remove all Mercedes WIS / EPC
    data" passes the corpus folders; rebuild_wis.py and the VM import,
    which re-ingest from those folders, never do

A LEMON car or a folder import is removed one by one (--doc-id): each has its
own pages, chunks and connectors, and duplicate pages are only ever matched
within one car (index.add_page), so removing one never touches another.

Mercedes WIS is removed as a whole (--source, matching every source_name that
starts with it): its models share an engine bucket and a general pool, so
removing one model would leave the rest half-working. A WIS doc_id given to
--doc-id is refused for that reason.

Never touched: the spend log (usage_log), every manual not named, and the
manual files on disk other than --recycle's (only extract folders by name:
autorundown/recycle.py). Free: no API calls.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

from autorundown import config, index, recycle

# sources removed only as a whole: WIS models share engine and general
# pages; EPC is one corpus that a re-import replaces in full
GROUPED = ("mercedes-wis", "mercedes-epc")


def grouped(source: str) -> bool:
    return any((source or "").startswith(g) for g in GROUPED)


def remove(conn: sqlite3.Connection, doc_id: str) -> tuple[int, int]:
    """Delete one vehicle's chunks, pages, connectors and row; returns
    (pages, chunks) removed. The chunks_fts trigger keeps search in step."""
    chunks = conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,)).rowcount
    pages = conn.execute("DELETE FROM pages WHERE doc_id=?", (doc_id,)).rowcount
    conn.execute("DELETE FROM connectors WHERE doc_id=?", (doc_id,))
    conn.execute("DELETE FROM vehicles WHERE doc_id=?", (doc_id,))
    return pages, chunks


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--index", default=config.load().index_path)
    ap.add_argument("--doc-id", action="append", default=[], help="a vehicle to remove (repeatable)")
    ap.add_argument("--source", help="remove every vehicle whose source starts with this "
                                     f"(allowed: {', '.join(GROUPED)})")
    ap.add_argument("--compact", action="store_true", help="VACUUM afterwards to shrink the file")
    ap.add_argument("--recycle", action="append", default=[], metavar="FOLDER",
                    help="an extract folder to send to the Recycle Bin afterwards (repeatable)")
    args = ap.parse_args(argv)
    if bool(args.doc_id) == bool(args.source):
        ap.error("give --doc-id (one or more) or --source, not both")
    path = Path(args.index).expanduser()
    if not path.is_file():
        print(f"! no index at {path}")
        return 1
    # refuse before removing anything, not halfway
    bad = [w for w in (recycle.allowed(Path(f), keep=[path]) for f in args.recycle if Path(f).exists()) if w]
    if bad:
        print(f"! will not send to the Recycle Bin: {bad[0]}")
        return 1
    conn = index.connect(path)
    rows = {d: (s, f"{y or ''} {mk or ''} {mo or ''}".strip()) for d, s, y, mk, mo in conn.execute(
        "SELECT doc_id, source_name, year_range, make, model FROM vehicles")}

    if args.source:
        if args.source not in GROUPED:
            print(f"! --source is only for {', '.join(GROUPED)}; remove other manuals by --doc-id")
            return 1
        targets = [d for d, (s, _) in rows.items() if (s or "").startswith(args.source)]
        if not targets:
            print(f"nothing from {args.source} in this index")
            return 0
    else:
        targets = list(dict.fromkeys(args.doc_id))
        missing = [d for d in targets if d not in rows]
        if missing:
            print(f"! not in the index: {', '.join(missing)}")
            return 1
        bad = [d for d in targets if grouped(rows[d][0])]
        if bad:
            src = next(g for g in GROUPED if (rows[bad[0]][0] or "").startswith(g))
            print(f"! {bad[0]} is {src} data, which is only removed as a whole "
                  f"(--source {src})")
            return 1

    usage_before = conn.execute("SELECT COUNT(*) FROM usage_log").fetchone()[0]
    print(f"index   : {path}")
    print(f"removing: {len(targets)} manual(s)")
    t0 = time.time()
    tp = tc = 0
    for i, d in enumerate(targets, 1):
        p, c = remove(conn, d)
        conn.commit()
        tp += p
        tc += c
        if len(targets) <= 20 or i % 20 == 0 or i == len(targets):
            print(f"  [{i}/{len(targets)}] {rows[d][1] or d}: {p:,} pages, {c:,} chunks", flush=True)
    usage_after = conn.execute("SELECT COUNT(*) FROM usage_log").fetchone()[0]
    if usage_after != usage_before:   # cannot happen; checked because losing it is the one thing not allowed
        print(f"! spend log changed: {usage_before} -> {usage_after} rows")
        return 1
    print(f"done: {len(targets)} manual(s), {tp:,} pages, {tc:,} chunks removed in {time.time() - t0:.1f}s; "
          f"spend log kept ({usage_after} rows)")
    if args.compact:
        before = path.stat().st_size
        # FTS5 only marks deleted rows; their space stays in the tables'
        # segments until an optimize merges them (2026-10-23: after removing
        # WIS and EPC, parts_fts still held 84,598 data blocks for 0 rows,
        # and VACUUM alone kept them)
        print("compacting (search tables, then VACUUM)…", flush=True)
        for fts in ("chunks_fts", "parts_fts"):
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (fts,)).fetchone():
                conn.execute(f"INSERT INTO {fts}({fts}) VALUES('optimize')")
                conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("VACUUM")
        # in WAL mode VACUUM lands in the -wal file; the main file shrinks on checkpoint
        busy = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]
        if busy:
            print("  (the server is reading the index; the file shrinks fully on its next checkpoint)")
        print(f"index size: {before / 1e6:,.0f} MB -> {path.stat().st_size / 1e6:,.0f} MB")
    else:
        print("the freed space is reused by later imports (--compact gives it back now)")
    conn.close()
    failed = 0
    for f in args.recycle:
        if not Path(f).exists():
            continue
        print(f"sending {f} to the Recycle Bin…", flush=True)
        try:
            print(f"  moved to {recycle.recycle(Path(f), keep=[path])}", flush=True)
        except (RuntimeError, OSError) as exc:
            print(f"! {exc}")
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
