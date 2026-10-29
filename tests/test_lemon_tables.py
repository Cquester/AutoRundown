"""A LEMON manual's Labour times and Parts tabs (lemon_tables.py, 2026-10-28).

The tabs were WIS/EPC only, so a LEMON car hid them although the 190E's
manual has 528 "Labor Times" and 326 "Parts Information" pages. The rows
come from the pages' own tables, as printed; another variant's pages
(applicability other_variant) are never read.

python tests/test_lemon_tables.py   (<1 s, no index needed)
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo on sys.path)
import sqlite3
import sys

from autorundown import lemon_tables

fails = 0


def check(what: str, cond: bool, detail="") -> None:
    global fails
    print(("ok   " if cond else "FAIL ") + what + ("" if cond else f"  -- {detail}"))
    fails += not cond


HEAD = "| | | | | |\n|---|---|---|---|---|\n"
LAB = HEAD + "| | Standard Hours | Manufacturer Warranty Hours | Skill Level | Notes |\n"
PARTS = HEAD + "| Part | Manufacturer | OEM Part # | Price | Notes |\n"
SP = "Parts and Labor > Starting and Charging > Starting System > {} > {}"
con = sqlite3.connect(":memory:")
con.execute("CREATE TABLE pages(doc_id TEXT, page_no TEXT, title TEXT, section_path TEXT, "
            "applicability TEXT, text TEXT)")
con.executemany("INSERT INTO pages VALUES ('c', ?, ?, ?, ?, ?)", [
    ("1001", "Ignition Lock: Labor Times", SP.format("Ignition Lock", "Labor Times"), "this_vehicle",
     LAB + "| Replace | | | | |\n| Ignition Lock Cylinder | 0.7 | 0.4 | | |\n"),
    ("1003", "Voltage Regulator: Labor Times", SP.format("Voltage Regulator", "Labor Times"), "this_vehicle",
     LAB + "| Replace | 1.0 | 0.7 | | |\n"),
    ("1005", "Water Pump: Labor Times", SP.format("Water Pump", "Labor Times"), "other_variant",
     LAB + "| Replace | 9.9 | 9.9 | | |\n"),
    ("1000", "Parts Information", SP.format("Ignition Lock", "Parts Information"), "this_vehicle",
     PARTS + "| Ignition Lock Tumbler | MGB | 2014600104 | 105.00 | |\n"),
    ("1002", "Parts Information", SP.format("Ignition Ballast Resistor", "Parts Information"), "this_vehicle",
     PARTS + "| Ballast Resistor, .4 Ohm | MGB | 0001581945 | 61.00 | Bosch blue bracket |\n"),
    ("1257", "Parts Information", SP.format("Water Pump", "Parts Information"), "this_vehicle", PARTS),
])

print("labour")
items = lemon_tables.labour_items(con, "c")
check("each operation as printed: the heading row's action, the page's own row",
      [(i["text"], i["hours"], i["warranty"]) for i in items]
      == [("Ignition Lock Cylinder, replace", 0.7, "0.4"), ("Voltage Regulator, replace", 1.0, "0.7")],
      items)
check("another variant's labour page is never read", not any(i["page_no"] == "1005" for i in items))
r = lemon_tables.labour(con, "c", "ignition lock")
check("a question finds its job", r["best"] == 1 and r["items"][0]["page_no"] == "1001", r)
check("a job with no labour page: nothing", lemon_tables.labour(con, "c", "water pump")["total"] == 0)

print("parts")
rows = lemon_tables.parts(con, "c", ["ignition lock part number"])
check("the component's page first, its rows as printed",
      rows and rows[0]["page_no"] == "1000"
      and rows[0]["lemon_parts"] == [{"part": "Ignition Lock Tumbler", "maker": "MGB", "number": "2014600104",
                                      "price": "105.00", "notes": ""}], rows)
check("a page with a header and no parts is not offered", not lemon_tables.parts(con, "c", ["water pump"]))
check("a part named in a row finds its page",
      [r["page_no"] for r in lemon_tables.parts(con, "c", ["ballast resistor"])] == ["1002"])

print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
