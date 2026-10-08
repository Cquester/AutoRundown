"""Build the search index from an archive directory.

Resumable: pages already indexed are skipped, so interrupting a long run and
re-running it picks up where it stopped. Use --force to rebuild a vehicle.

    python ingest.py --archive "C:/Users/me/Downloads" --index index.db
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from autorundown import config, index
from autorundown import importers


def main(argv: list[str] | None = None) -> int:
    cfg = config.load()
    ap = argparse.ArgumentParser(description="Index a repair-manual archive.")
    ap.add_argument("--archive", default=cfg.archive_dir,
                    help="archive root directory")
    ap.add_argument("--index", default=cfg.index_path, help="path to index.db")
    ap.add_argument("--importer", default=cfg.importer,
                    help=f"one of {importers.available()} or 'auto'")
    ap.add_argument("--force", action="store_true",
                    help="re-index pages that are already present")
    ap.add_argument("--limit", type=int, default=0,
                    help="stop after N pages per vehicle (for testing)")
    ap.add_argument("--repath", action="store_true",
                    help="only update where the archive lives, keeping the "
                         "existing index (use after moving machines)")
    args = ap.parse_args(argv)

    if not args.archive:
        ap.error("no archive directory configured; pass --archive")
    root = Path(args.archive).expanduser()
    if not root.is_dir():
        ap.error(f"archive directory not found: {root}")

    importer = (importers.autodetect(root) if args.importer == "auto"
                else importers.get(args.importer))
    print(f"archive : {root}")
    print(f"index   : {args.index}")
    print(f"importer: {importer.name}")

    conn = index.connect(args.index)
    vehicles = list(importer.discover(root))
    if not vehicles:
        print("no vehicles found in archive", file=sys.stderr)
        return 1

    print(f"\nfound {len(vehicles)} vehicle(s):")
    for v in vehicles:
        print(f"  - {v.label}  ({v.page_count} pages)")

    if args.repath:
        # Page text lives in the index, but diagrams are served from the
        # archive on disk, and the path to it is absolute. Moving machines
        # (or just moving the folder) leaves those pointing at nothing, so
        # repoint them without redoing the 40s of indexing.
        known = {v["doc_id"] for v in index.vehicles(conn)}
        moved = missing = 0
        for v in vehicles:
            if v.doc_id not in known:
                print(f"  ! {v.label} is not in this index -- run a full ingest")
                missing += 1
                continue
            conn.execute("UPDATE vehicles SET file_path=? WHERE doc_id=?",
                         (v.file_path, v.doc_id))
            print(f"  -> {v.doc_id}\n     {v.file_path}")
            moved += 1
        conn.commit()
        orphans = known - {v.doc_id for v in vehicles}
        for doc_id in sorted(orphans):
            print(f"  ! indexed but not found under this archive: {doc_id}")
        print(f"\nrepointed {moved} vehicle(s); {missing} not indexed, "
              f"{len(orphans)} indexed but absent")
        conn.close()
        return 0

    grand_pages = grand_chunks = 0
    for v in vehicles:
        print(f"\n=== {v.label} ===")
        index.upsert_vehicle(conn, v)
        conn.commit()

        done = set() if args.force else index.indexed_page_uids(conn, v.doc_id)
        if done:
            print(f"resuming: {len(done)} pages already indexed")

        started = time.time()
        n_pages = n_chunks = n_skip = 0
        for page in importer.pages(v):
            if page.page_uid in done:
                n_skip += 1
                continue
            n_chunks += index.add_page(conn, page)
            n_pages += 1
            if n_pages % 500 == 0:
                conn.commit()
                rate = n_pages / max(time.time() - started, 1e-6)
                print(f"  {n_pages:>6} pages  {n_chunks:>7} chunks  "
                      f"{rate:>5.0f} pages/s", flush=True)
            if args.limit and n_pages >= args.limit:
                break

        n_conn = index.build_connector_index(conn, v.doc_id)
        index.mark_complete(conn, v.doc_id)
        conn.commit()
        elapsed = time.time() - started
        print(f"  done: {n_pages} new pages, {n_chunks} chunks, "
              f"{n_skip} skipped, {n_conn} connectors, {elapsed:.1f}s")
        grand_pages += n_pages
        grand_chunks += n_chunks

    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    size_mb = Path(args.index).stat().st_size / 1e6
    print(f"\nindexed {grand_pages} pages / {grand_chunks} chunks")
    print(f"index size: {size_mb:.1f} MB")
    print("totals:", index.stats(conn))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
