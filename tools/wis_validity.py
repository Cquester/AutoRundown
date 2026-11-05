"""Fill `page_validity` (the models each WIS page is valid for, from its own
"Validity:" lines; autorundown/validity.py) for pages already in an index --
once, by hand, instead of a whole `rebuild_wis.py`.

    python tools/wis_validity.py --index index.db            # dry run: counts only
    python tools/wis_validity.py --index index.db --apply

`index.add_page` writes the rows itself since 2026-11-04, so a WIS ingest or
rebuild needs none of this. For an older index this reads every page that
has a Validity line and writes a short row for each one it can read. Pages
are only read, never rewritten, and the reading is paced (about 100 MB/s)
so a 3.5 GB index doesn't saturate the disk. A second run changes nothing.
Until it has run, search simply doesn't use the Validity lines.

The server does the same by itself, once, in the background the first time
it starts on such an index (validity.fill_once); this is for doing it by
hand, or to see the counts first.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autorundown import index, validity  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--index", required=True)
    ap.add_argument("--apply", action="store_true", help="write the rows (default: count only)")
    a = ap.parse_args(argv)
    if not Path(a.index).is_file():
        print(f"no index at {a.index}")
        return 2
    # --apply: index.connect makes the table on an older index; a dry run is read-only
    conn = (index.connect(a.index) if a.apply else
            sqlite3.connect(f"file:{Path(a.index).as_posix()}?mode=ro", uri=True))
    if not a.apply and not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='page_validity'").fetchone():
        conn.execute("CREATE TEMP TABLE page_validity(page_uid TEXT PRIMARY KEY, models TEXT)")
    s = validity.fill(conn, apply=a.apply, progress=lambda done, top, rows: print(
        f"  {done:,} / {top:,} pages scanned, {rows:,} with a readable Validity line", flush=True))
    print(f"{s['with_line']:,} pages have a Validity line; {s['rows']:,} name models it can read "
          f"({s['new']:,} new); {s['seconds']} s"
          + ("" if a.apply else " -- dry run, nothing written (--apply to write)"))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
