"""Add the old manuals' job numbers ("Job no.: 67-200") to WIS pages already
in an index -- once, by hand, instead of a whole `rebuild_wis.py`.

    python tools/wis_job_numbers.py --index index.db            # dry run: counts only
    python tools/wis_job_numbers.py --index index.db --apply

The importer writes the line itself since 2026-10-26 (`records.job_numbers`:
WIS's figure names carry the printed job number, which a scanned page has no
text for), so a WIS ingest or rebuild needs none of this. For an index built
before then, this reads each WIS page's "Figures:" line and title, works out
the same numbers, and puts the line where the importer would (before
"Figures:"). Each changed page goes back through `index.add_page`, so the
chunks and both FTS tables stay in step, duplicates keep pointing at their
canonical page, and a second run changes nothing. Nothing else is touched:
not the spend log, not the other manuals, not the EPC catalogue.

Stop the server first, or run it when nobody is asking: it writes to the
index (a few thousand pages, seconds once the scan is done).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from autorundown import index  # noqa: E402
from autorundown.importers.base import PageRecord  # noqa: E402
from autorundown.importers.records import job_numbers  # noqa: E402


def with_job_numbers(title: str, text: str) -> str | None:
    """The page text with its "Job no.:" line, or None when it needs none."""
    if "\n\nJob no.: " in text or text.startswith("Job no.: "):
        return None
    head, sep, figures = text.rpartition("Figures: ")
    if not sep or (head and not head.endswith("\n\n")):
        return None
    nrs = [n.strip() for n in figures.split("\n", 1)[0].split(",") if n.strip()]
    jobs = job_numbers(title, nrs)
    if not jobs:
        return None
    return head + "Job no.: " + ", ".join(jobs) + "\n\n" + sep + figures


def _record(row) -> PageRecord:
    return PageRecord(
        doc_id=row["doc_id"], page_no=row["page_no"], file_path=row["file_path"],
        title=row["title"] or "",
        section_path=(row["section_path"] or "").split(" > ") if row["section_path"] else [],
        system=row["system"] or "", doc_type=row["doc_type"] or "manual",
        text=row["text"] or "",
        images=(row["images"] or "").split("\n") if row["images"] else [],
        links=(row["links"] or "").split("\n") if row["links"] else [],
        applicability=row["applicability"] or "this_vehicle",
        vehicle_types=row["vehicle_types"] or "", engines=row["engines"] or "",
        codes_only=row["codes_only"] or "", codes_except=row["codes_except"] or "")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--index", required=True, help="the index to update")
    ap.add_argument("--apply", action="store_true", help="write (default: count only)")
    args = ap.parse_args(argv)
    if not Path(args.index).is_file():
        print(f"no index at {args.index}")
        return 2
    conn = index.connect(args.index, setup=args.apply)
    try:
        return _run(conn, args.apply)
    finally:
        conn.close()


def _run(conn, apply: bool) -> int:
    wis = [r[0] for r in conn.execute(
        "SELECT doc_id FROM vehicles WHERE source_name LIKE 'mercedes-wis%'")]
    if not wis:
        print("no WIS manuals in this index; nothing to do")
        return 0
    marks = ",".join("?" for _ in wis)
    # canonical pages first: a duplicate re-added after them finds its
    # canonical by the new text's hash, as at import
    rows = conn.execute(
        f"""SELECT * FROM pages WHERE doc_id IN ({marks})
            AND text GLOB '*Figures: *ra[0-9][0-9][0-9][0-9][0-9]*'
            AND text NOT LIKE '%Job no.: %'
            ORDER BY canonical_uid IS NOT NULL""", wis).fetchall()
    changed = []
    for row in rows:
        new = with_job_numbers(row["title"] or "", row["text"] or "")
        if new is not None:
            rec = _record(row)
            rec.text = new
            changed.append(rec)
    print(f"{len(rows)} WIS pages with old-manual figures; {len(changed)} get a job number")
    for rec in changed[:5]:
        line = next(l for l in rec.text.split("\n") if l.startswith("Job no.: "))
        print(f"  {rec.doc_id} {rec.page_no}: {line}  ({rec.title[:50].strip()})")
    if not apply:
        print("dry run: nothing written (add --apply)")
        return 0
    with conn:
        for rec in changed:
            index.add_page(conn, rec)
    print(f"written: {len(changed)} pages")
    return 0


if __name__ == "__main__":
    sys.exit(main())
