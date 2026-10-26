"""Old-manual job numbers and title matches in keyword search -- FREE, <2 s.

    python tests/test_job_numbers.py

A 1986 190 E 2.3-16's "67-200 Removal and installation of rear window" is a
scanned WIS page: three images, no text. Searching its own title showed
67-220 but not 67-200 (fifth in the W201's bucket, four slots of twelve), and
"67-200" found nothing (2026-10-26). Checks `records.job_numbers` (the number
from WIS's figure names), the importer's "Job no.:" line,
`tools/wis_job_numbers.py` on an existing index, and `search.title_fit` /
`_title_rank`.
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo and tools/ on sys.path)
import os
import sys
import tempfile
from pathlib import Path

os.environ["AUTORUNDOWN_HOME"] = tempfile.mkdtemp(prefix="ar_jobs_")  # before importing autorundown

from autorundown import index, search  # noqa: E402
from autorundown.importers.base import PageRecord, VehicleRecord  # noqa: E402
from autorundown.importers.records import _page_text, job_numbers  # noqa: E402
import wis_job_numbers  # noqa: E402

fails = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f" -- {detail}" if detail and not ok else ""))
    fails += not ok


REAR = "0200 - Removal and installation of the rear window                    Model 201"
FIGS = ["ra670020102000002p", "ra670020102001002p", "ra670020102002002p"]

print("-- job_numbers --")
check("figure + title agree -> 67-200", job_numbers(REAR, FIGS) == ["67-200"], str(job_numbers(REAR, FIGS)))
check("one number for three sheets", len(job_numbers(REAR, FIGS)) == 1)
check("a figure from another job gives none",
      job_numbers("0140 - Removal, installation and adjustment of the control cable",
                  ["ra770020104100002p"]) == [])
check("a chapter-numbered title gives none",
      job_numbers("2.1 - Removal and installation of the clutch", ["ra250020102010002p"]) == [])
check("a four-digit job keeps its digits",
      job_numbers("4725 - Removal and installation of input shaft", ["ra260012347250002p"]) == ["26-4725"])
check("modern figure names give none", job_numbers(REAR, ["p54101234", "w1518102401"]) == [])

print("-- importer --")
rec = {"title": REAR, "body_text": "", "image_files": ["5/411221.png"],
       "figures": [{"graphik_nr": n} for n in FIGS]}
text = _page_text(rec, images=["5/411221.png"])
check("page text has the job line before Figures",
      "\n\nJob no.: 67-200\n\nFigures: ra670020102000002p" in text, text[-120:])
check("tool computes what the importer writes",
      wis_job_numbers.with_job_numbers(REAR, text.replace("Job no.: 67-200\n\n", "")) == text)
check("tool leaves a page that has it alone", wis_job_numbers.with_job_numbers(REAR, text) is None)

with tempfile.TemporaryDirectory() as tmp:
    db = Path(tmp) / "index.db"
    conn = index.connect(db)
    wis, lemon = "mercedes-wis-virtualbox-en-201", "lemon-f150"
    index.upsert_vehicle(conn, VehicleRecord(doc_id=wis, source_name="mercedes-wis-virtualbox",
                                             file_path=tmp, model="W201 190"))
    index.upsert_vehicle(conn, VehicleRecord(doc_id=lemon, source_name="lemon", file_path=tmp))
    old = text.replace("Job no.: 67-200\n\n", "")       # as an index built before

    def page(no, title, body, doc=wis):
        index.add_page(conn, PageRecord(doc_id=doc, page_no=no, file_path=tmp, title=title,
                                        section_path=["MODEL 201", "all variants"], text=body,
                                        images=["5/411221.png"]))
    page("49008404", REAR, old)
    page("90000001", REAR, old)            # same text: a duplicate of 49008404
    page("49008405", "0220 - Removal and installation of the ornamental frame on the rear window",
         "Applies to: MODEL 201; all variants\n\n[diagram: 5/411224.png]\n\nFigures: ra670020102200002p")
    page("49008436", "0181 - Removal and installation of the window opener in rear door",
         "Applies to: MODEL 201; all variants\n\n[diagram: 5/1.png]\n\nFigures: p0001")
    page("7", "Rear window", old, doc=lemon)   # not WIS: never touched
    conn.commit()
    chunks_before = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    usage = conn.execute("SELECT COUNT(*) FROM usage_log").fetchone()[0]
    conn.close()

    print("-- tools/wis_job_numbers.py --")
    rc = wis_job_numbers.main(["--index", str(db)])
    conn = index.connect(db, setup=False)
    hit = lambda q: [r[0] for r in conn.execute(
        "SELECT p.page_no FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
        "JOIN pages p ON p.page_uid = c.page_uid WHERE chunks_fts MATCH ? ORDER BY p.page_no",
        (search.build_match(q),))]
    check("dry run writes nothing", rc == 0 and hit("67-200") == [])
    conn.close()
    rc = wis_job_numbers.main(["--index", str(db), "--apply"])
    conn = index.connect(db, setup=False)
    check("apply: '67-200' finds the page", rc == 0 and hit("67-200") == ["49008404"], str(hit("67-200")))
    check("apply: '67-220' finds the frame page", hit("67-220") == ["49008405"], str(hit("67-220")))
    check("'67 200' and '67.200' too", hit("67 200") == hit("67.200") == ["49008404"])
    canon = conn.execute("SELECT canonical_uid FROM pages WHERE page_no='90000001'").fetchone()[0]
    check("the duplicate still points at its canonical page", canon == f"{wis}:49008404", str(canon))
    check("chunk count unchanged", conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == chunks_before)
    lem = conn.execute("SELECT text FROM pages WHERE doc_id=?", (lemon,)).fetchone()[0]
    check("other manuals untouched", "Job no." not in lem)
    check("spend log untouched", conn.execute("SELECT COUNT(*) FROM usage_log").fetchone()[0] == usage)
    conn.close()
    import io, contextlib
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        wis_job_numbers.main(["--index", str(db), "--apply"])
    check("second run changes nothing", "0 get a job number" in out.getvalue(), out.getvalue())

    print("-- title match in keyword search --")
    conn = index.connect(db, setup=False)
    q = "Removal and installation of rear window"
    check("title_fit: the page's own title is 1", search.title_fit(q, REAR) == 1.0)
    check("title_fit: a longer title less",
          0 < search.title_fit(q, "0220 - Removal and installation of the ornamental frame on the rear window") < 1)
    check("title_fit: a word missing is 0", search.title_fit("rear window wiper", REAR) == 0)
    check("title_fit: 'remove' matches 'Removal'", search.title_fit("remove rear window", REAR) == 0.75)
    rows = search._search_one(conn, wis, q, 1, False)
    check("the exact title comes first", rows and rows[0]["page_no"] == "49008404",
          str([r["page_no"] for r in rows]))
    saved = search.TITLE_BOOST
    two = [r["page_no"] for r in search._search_one(conn, wis, "rear window", 3, False)]
    search.TITLE_BOOST = 0
    two_plain = [r["page_no"] for r in search._search_one(conn, wis, "rear window", 3, False)]
    search.TITLE_BOOST = saved
    check("two-word searches are not re-ranked", two == two_plain, f"{two} vs {two_plain}")
    conn.close()

print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
