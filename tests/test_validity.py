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

print("an engine page's title names its models")
t = validity.title_models
ENG = "Applies to: ENGINE 102\n\nText"
check("vans' carburettor page", t("1700 - Testing 2 E-E carburetor system   Engine 102 with carburetor 2 E-E"
                                  "   Models 210, 310, 410, 510", ENG), "t,210.,310.,410.,510.,")
check("one model", t("0030 - Removal and installation of the engine (oil capacity)  Engine 102 KE in model 201",
                     ENG), "t,201.,")
check("'all models': keep the page",
      t("0030 - Fuel tank ventilation  A. Filler cap (all models), B. Models 124, 201", ENG), "")
check("'except': keep the page", t("Engine 102 (except 102.983/99), Models 124, 201", ENG), "")
check("a model year is not a model", t("Engine 102.962 to model year 1986", ENG), "")
check("a page filed under a model is the model's, whatever its title",
      t("Engine 102 in model 201", "Applies to: MODEL 124; all variants\n\nText"), "")
check("a Validity line wins over the title",
      validity.row("Applies to: ENGINE 102\nValidity: MODEL 201.0\n\nText", "Engine 102, Models 124"),
      ",201.0,")
van = "Engine 102 with carburetor 2 E-E   Models 210, 310, 410, 510"
check("a link to it: not for a 190 E", validity.fits(ENG, ["201.034"], van), False)
check("... for a 310 van, yes", validity.fits(ENG, ["310.211"], van), True)
check("... with no car codes, yes", validity.fits(ENG, [], van), True)
e = index.connect(":memory:")
for d, model in (("w201", "W201 190"), ("e102", "Engine 102")):
    index.upsert_vehicle(e, VehicleRecord(doc_id=d, source_name="records", file_path="x",
                                          make="Mercedes-Benz", model=model))
for no, d, title, applies in (("10", "e102", "Carburetor test  Engine 102  Models 210, 310", "ENGINE 102"),
                              ("11", "e102", "Carburetor removal  Engine 102 in model 201", "ENGINE 102"),
                              ("12", "e102", "Carburetor overview  Engine 102", "ENGINE 102"),
                              ("13", "w201", "Carburetor note  Models 124, 126", "MODEL 201")):
    index.add_page(e, PageRecord(doc_id=d, page_no=no, file_path="x", title=title, section_path=[applies],
                                 system="", text=f"Applies to: {applies}\n\n{title}. Carburetor."))
e.commit()
sc = search.Scope(["w201", "e102"])
sc.codes = (("201.034",), ())
check("search: a 190 E gets its engine's pages for its model or none, and its own model's all",
      {r["page_no"] for r in search.search(e, sc, "carburetor", 20)}, {"11", "12", "13"})

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
