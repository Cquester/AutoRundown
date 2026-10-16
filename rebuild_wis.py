"""Rebuild the Mercedes WIS part of the index, keeping everything else.

    python rebuild_wis.py [--archive <corpus dir>] [--index index.db] [--importer records]

Removes the WIS part in place (`remove_manuals.py --source mercedes-wis`),
then re-ingests the WIS corpus. Every other manual -- LEMON and folder
imports, the EPC parts catalogue -- and the spend log (usage_log) stay as
they are, and are counted before and after to prove it. Free: no API calls.

Why in place: this script used to restore `index.before-wis.db` (the index
as it was before WIS was first added) and carry the newer usage-log rows
across. That backup predates the EPC catalogue and every manual imported
since, so a WIS-only rebuild silently dropped them (found 2026-10-16). The
backup file is no longer read or written; it stays on disk as a rollback
copy, and copying it over the index by hand is still never done.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import ingest
import remove_manuals
from autorundown import config, importers

HERE = Path(__file__).resolve().parent
DEFAULT_ARCHIVE = HERE.parent / "Mercedes Documents" / "autorundown_out" / "wis_extract"
WIS = "mercedes-wis"


def corpus_sources(archive: Path) -> list[str]:
    """source_type of every records corpus under `archive`."""
    out = []
    for cdir in importers.get("records")._corpus_dirs(archive):
        try:
            m = json.loads((cdir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            m = {}
        out.append(m.get("source_type") or "")
    return out


def kept_snapshot(path: Path) -> tuple[dict, int]:
    """What the rebuild must not touch: every non-WIS vehicle with its page
    and chunk counts, and the number of usage-log rows."""
    con = sqlite3.connect(path)
    try:
        rows = con.execute(
            "SELECT v.doc_id, v.source_name, "
            "(SELECT COUNT(*) FROM pages p WHERE p.doc_id=v.doc_id), "
            "(SELECT COUNT(*) FROM chunks c WHERE c.doc_id=v.doc_id) "
            "FROM vehicles v WHERE COALESCE(v.source_name, '') NOT LIKE ?",
            (WIS + "%",)).fetchall()
        usage = con.execute("SELECT COUNT(*) FROM usage_log").fetchone()[0]
    finally:
        con.close()
    return {d: (s, p, c) for d, s, p, c in rows}, usage


def check_kept(path: Path, kept: dict, usage: int, when: str) -> bool:
    now, now_usage = kept_snapshot(path)
    ok = True
    if now != kept:
        lost = sorted(set(kept) - set(now))
        changed = sorted(d for d in kept if d in now and now[d] != kept[d])
        print(f"! {when}: other manuals changed -- {len(lost)} gone {lost[:5]}, "
              f"{len(changed)} changed {changed[:5]}", file=sys.stderr)
        ok = False
    if now_usage < usage:   # the server may log a question meanwhile; never fewer
        print(f"! {when}: spend log shrank {usage} -> {now_usage} rows", file=sys.stderr)
        ok = False
    if ok:
        print(f"{when}: {len(kept)} other manual(s) unchanged, spend log {now_usage} rows")
    return ok


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--archive", default=str(DEFAULT_ARCHIVE))
    ap.add_argument("--index", default=config.load().index_path,
                    help="the live index (default: the configured one)")
    ap.add_argument("--importer", default="records")
    args = ap.parse_args(argv)
    live = Path(args.index).expanduser().resolve()
    archive = Path(args.archive).expanduser()
    print(f"index: {live}\narchive: {archive}")

    # only a WIS corpus: anything else would be added on top while WIS is gone
    if args.importer == "records":
        sources = corpus_sources(archive) if archive.is_dir() else []
        if not sources:
            print(f"no records corpus under {archive}; refusing to rebuild", file=sys.stderr)
            return 1
        other = [s for s in sources if not s.startswith(WIS)]
        if other:
            print(f"{archive} holds {other[0] or 'an unnamed source'}, not Mercedes WIS; "
                  f"refusing to rebuild", file=sys.stderr)
            return 1

    if live.is_file():
        kept, usage = kept_snapshot(live)
        print(f"keeping: {len(kept)} other manual(s), spend log {usage} rows")
        # 1. remove WIS in place (refuses nothing else; checks the spend log itself)
        rc = remove_manuals.main(["--index", str(live), "--source", WIS])
        if rc:
            return rc
        if not check_kept(live, kept, usage, "after removing WIS"):
            return 1
    else:
        kept, usage = {}, 0

    # 2. re-ingest WIS
    rc = ingest.main(["--archive", str(archive), "--index", str(live),
                      "--importer", args.importer])
    if rc:
        return rc
    return 0 if check_kept(live, kept, usage, "after re-ingesting WIS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
