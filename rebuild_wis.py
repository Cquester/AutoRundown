"""Rebuild the Mercedes WIS part of the index, keeping everything else.

    python rebuild_wis.py [--archive <corpus dir>] [--index index.db] [--importer records]

Restores `index.before-wis.db` (the index as it was before WIS was added; it
sits next to the index -- by default the configured one),
carries over every usage-log row the backup doesn't have, then re-ingests the
WIS corpus. Why this exists: the spend log lives inside the index, and
rebuilding by restoring the backup silently threw away every paid question
asked since the backup was taken -- that happened repeatedly before this
script. Free: no API calls.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import ingest
from autorundown import config

HERE = Path(__file__).resolve().parent
DEFAULT_ARCHIVE = HERE.parent / "Mercedes Documents" / "autorundown_out" / "wis_extract"


def usage_rows(conn: sqlite3.Connection) -> tuple[list[str], list[tuple]]:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(usage_log)") if r[1] != "id"]
    return cols, [tuple(r) for r in conn.execute(
        f"SELECT {', '.join(cols)} FROM usage_log ORDER BY id")]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--archive", default=str(DEFAULT_ARCHIVE))
    ap.add_argument("--index", default=config.load().index_path,
                    help="the live index (default: the configured one)")
    ap.add_argument("--importer", default="records")
    args = ap.parse_args(argv)
    live = Path(args.index).expanduser().resolve()
    backup = live.with_name("index.before-wis.db")
    print(f"index: {live}\nbackup: {backup}")

    if not backup.is_file():
        print(f"no backup at {backup}; refusing to rebuild", file=sys.stderr)
        return 1

    # 1. rows the backup doesn't have -- the spend since it was taken
    cur = sqlite3.connect(live)
    cols, now = usage_rows(cur)
    cur.close()
    bak = sqlite3.connect(backup)
    _, before = usage_rows(bak)
    new = [r for r in now if r not in set(before)]
    print(f"usage log: {len(now)} rows now, {len(before)} in the backup, "
          f"{len(new)} to carry over")

    # 2. restore the pre-WIS index (SQLite backup API; drop any stale WAL)
    for side in (live.with_name(live.name + "-wal"), live.with_name(live.name + "-shm")):
        side.unlink(missing_ok=True)
    dst = sqlite3.connect(live)
    bak.backup(dst)
    bak.close()

    # 3. put the carried-over spend back before anything else can fail
    if new:
        dst.executemany(
            f"INSERT INTO usage_log ({', '.join(cols)}) VALUES "
            f"({', '.join('?' * len(cols))})", new)
        dst.commit()
    check = dst.execute("SELECT COUNT(*) FROM usage_log").fetchone()[0]
    dst.close()
    if check != len(before) + len(new):
        print(f"usage log mismatch after restore: {check}", file=sys.stderr)
        return 1
    print(f"usage log restored: {check} rows")

    # 4. re-ingest WIS
    return ingest.main(["--archive", args.archive, "--index", str(live),
                        "--importer", args.importer])


if __name__ == "__main__":
    raise SystemExit(main())
