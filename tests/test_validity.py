"""WIS's own Validity lines narrow search to the car's body (validity.py)
-- FREE, <1 s, no index needed.

    python tests/test_validity.py

2026-11-04: a 2001 E 320 4MATIC T (wagon, 210.282) was shown "Trunk lid
ambient lamp" (Validity: MODEL 210.0 -- the sedans), and a sedan the
wagon's tailgate pages; the Validity line was never read. Checks the parser
on the line shapes WIS uses, that anything it can't read keeps the page,
and the search filter end to end on a small index.
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo on sys.path)
import sqlite3
import sys

from autorundown import index, search, validity
from autorundown.importers.base import PageRecord, VehicleRecord

fails = 0


def check(name: str, got, want) -> None:
    global fails
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + name + ("" if ok else f" -- got {got!r}, want {want!r}"))
    fails += not ok


print("reading Validity lines")
m = validity.models
check("sedans only", m("Validity: MODEL 210.0 as of 1.7.99"), ",210.0,")
check("slash list: sedan and wagon", m("Validity: MODEL 203.0, 209, 210.0/2, 211"),
      ",203.0,209.,210.0,210.2,211.,")
check("slash list of two-digit codes", m("Validity: MODEL 202.07/08/18, 210.2"),
      ",202.07,202.08,202.18,210.2,")
check("full codes in a slash list", m("Validity: MODEL 163, 210.081/082/281"),
      ",163.,210.081,210.082,210.281,")
check("engines before the model are not models",
      m("Validity: ENGINE 112, 113 in MODEL 210.2 with CODE (494a) USA version"), ",210.2,")
check("conditions after the list only narrow it", m("Validity: MODEL 210.07 without CODE 979"), ",210.07,")
check("'except': keep the page", m("Validity: Model 210.2 (except 210.274) with AMG wheel"), "")
check("a Validity line naming no model: keep the page", m("Validity: ENGINE 112"), "")
check("... even beside one that does", m("Validity: MODEL 210.0\n\nValidity: ENGINE 112"), "")
check("no Validity line: nothing", m("Applies to: MODEL 210; all variants\n\nText"), "")

print("the search filter on a small index")
con = index.connect(":memory:")
index.upsert_vehicle(con, VehicleRecord(doc_id="w210", source_name="records", file_path="x",
                                        make="Mercedes-Benz", model="W210 E-Class"))
pages = [("1", "Trunk lid ambient lamp", "Validity: MODEL 210.0 as of 1.7.99"),
         ("2", "Removing and installing tailgate", "Validity: MODEL 202.07/08/18, 210.2, 211.2/6"),
         ("3", "Trunk lid emergency release", "Validity: MODEL 210 with CODE 494"),
         ("4", "Trunk lid seal", "Validity: Model 210.0 (except 210.065)"),
         ("5", "Trunk lid hinge", "no validity line here"),
         ("6", "Trunk lid lock, armoured", "Validity: MODEL 210.07 without CODE 979"),
         ("7", "Trunk lid, 4MATIC only", "Validity: MODEL 210.082/282")]
for no, title, val in pages:
    index.add_page(con, PageRecord(doc_id="w210", page_no=no, file_path="x", title=title,
                                   section_path=["MODEL 210", "all variants"], system="", text=f"{val}\n\n{title}. Trunk lid and tailgate."))
con.commit()
check("ingest writes the rows (pages it can read)",
      {r[0] for r in con.execute("SELECT page_uid FROM page_validity")},
      {"w210:1", "w210:2", "w210:3", "w210:6", "w210:7"})


def found(model_codes) -> set:
    sc = search.Scope(["w210"])
    sc.codes = (tuple(model_codes), ())
    return {r["page_no"] for r in search.search(con, sc, "trunk lid tailgate", 20)}


check("wagon 210.282: no sedan-only pages", found(["210.282"]), {"2", "3", "4", "5", "7"})
check("sedan 210.082: no wagon-only pages", found(["210.082"]), {"1", "3", "4", "5", "7"})
check("armoured sedan 210.074: its own page too", found(["210.074"]), {"1", "3", "4", "5", "6"})
check("no car codes: every page", found([]), {"1", "2", "3", "4", "5", "6", "7"})
check("another series' car: not this filter's to judge", found(["124.030"]), {"1", "2", "3", "4", "5", "6", "7"})

print("a page reached by a link (query._fits_car)")
check("sedan-only page, wagon car: no", validity.fits("Validity: MODEL 210.0", ["210.282"]), False)
check("sedan-only page, sedan car: yes", validity.fits("Validity: MODEL 210.0", ["210.082"]), True)
check("'except': kept", validity.fits("Validity: Model 210.2 (except 210.274)", ["210.082"]), True)
check("no car codes: kept", validity.fits("Validity: MODEL 210.0", []), True)

print("an index without the table (older, read-only)")
old = sqlite3.connect(":memory:")
check("no table: no clause", validity.clause(old, ["210.282"]), ("", []))

print("an index built before the rows existed: filled once")
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402
tmp = Path(tempfile.mkdtemp()) / "old.db"
o = index.connect(tmp)
index.upsert_vehicle(o, VehicleRecord(doc_id="w210", source_name="records", file_path="x"))
for no, title, val in pages:
    index.add_page(o, PageRecord(doc_id="w210", page_no=no, file_path="x", title=title,
                                 section_path=["MODEL 210"], system="", text=f"{val}\n\n{title}."))
o.execute("DELETE FROM page_validity")          # as an index from before 2026-11-04
o.commit(); o.close()
validity.fill_once(str(tmp))
o = index.connect(tmp, setup=False)
check("fill_once writes the rows and its marker",
      {r[0] for r in o.execute("SELECT page_uid FROM page_validity")},
      {"w210:1", "w210:2", "w210:3", "w210:6", "w210:7", validity.FILLED})
o.execute("DELETE FROM page_validity WHERE page_uid='w210:1'"); o.commit()
validity.fill_once(str(tmp))
check("... and never again (a row is gone: not re-read)",
      o.execute("SELECT COUNT(*) FROM page_validity WHERE page_uid='w210:1'").fetchone()[0], 0)
o.close()

print("removing a manual removes its rows")
import remove_manuals  # noqa: E402
remove_manuals.remove(con, "w210")
check("rows gone", con.execute("SELECT COUNT(*) FROM page_validity").fetchone()[0], 0)

print("all passed" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
