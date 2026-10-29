"""Parts pages and lines that fit the chosen car (agent review, 2026-10-28).

- A parts page's section line keeps only 10 model codes ("… 210.063 ..."),
  so a car whose code was the 11th saw none of its chassis pages (every
  W211 4MATIC, the W212 E 350 4MATIC…): `parts_codes_clause` now reads the
  page's "Models in this catalog:" line when the section line was cut.
- A line whose Fits field names only other models of the series or other
  engines of the family is left out (`line_fits`): 27% of the Parts tab's
  lines for a chosen car fitted other models only.
- A LEMON manual's page filed under another engine ("3.5L DURATEC" in a
  3.5L EcoBoost F-150) but not stamped other_variant is left out of search.

python tests/test_parts_fit.py   (<1 s, no index needed)
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo on sys.path)
import sqlite3

from autorundown.search import catalog_info, line_fits, parts_codes_clause

fails = 0


def check(cond: bool, what: str) -> None:
    global fails
    print(("  ok   " if cond else "  FAIL ") + what)
    fails += not cond


print("a page whose code list was cut")
con = sqlite3.connect(":memory:")
con.execute("CREATE TABLE pages(page_no TEXT, section_path TEXT, text TEXT)")
codes11 = [f"210.{n:03d}" for n in (6, 7, 16, 26, 35, 45, 48, 61, 62, 63, 81)]
con.executemany("INSERT INTO pages VALUES (?,?,?)", [
    ("P1", "MODEL 210 > " + ", ".join(codes11[:10]) + " ...",
     "Applies to: MODEL 210\n\nParts: model 210 (EPC catalog 66G), group 82\n"
     "Models in this catalog: " + ", ".join(f"{c} E 280" for c in codes11) + "\n\n"
     "Callout | Part number | Name | Fits | Notes\n"
     "005 | A 210 820 33 64 (A2108203364) | TAIL LAMP | qty 1: 210.999 | \n"),
    ("P2", "MODEL 210 > 210.020, 210.055", "Models in this catalog: 210.020 E 300, 210.055 E 320\n"),
    ("P3", "ENGINE 112", "an engine page names no model codes\n"),
])


def pages(*car: str) -> set:
    clause, args = parts_codes_clause((car, ()))
    return {r[0] for r in con.execute("SELECT page_no FROM pages p WHERE 1" + clause, args)}


check(pages("210.081") == {"P1", "P3"}, "the 11th code finds its page through the catalog line (it found none)")
check(pages("210.006") == {"P1", "P3"}, "a code in the cut list still finds it")
check(pages("210.055") == {"P2", "P3"}, "a page whose full list lacks the car is still left out")
check(pages("210.999") == {"P3"}, "a code only in a part line's Fits doesn't make the page the car's")
check(pages() == {"P1", "P2", "P3"}, "no car chosen: every page")

print("lines that fit the car")
car = (("210.055", "210.065"), ("104.990", "112.941"))
line = "008 | A 210 720 07 46 (A2107200746) | WINDOW REGULATOR | {} | "
check(line_fits(line.format("qty 1, all models"), car), "all models: kept")
check(line_fits(line.format("210.020, 210.055, 210.072"), car), "one of the car's codes: kept")
check(not line_fits(line.format("210.020, 210.072"), car), "only other models of the series: left out")
check(not line_fits(line.format("qty 1: 112.946"), car), "only another engine of the car's family: left out")
check(line_fits(line.format("qty 1: 722.605"), car), "a code it can't judge (a gearbox): kept")
check(line_fits(line.format("qty 1: 210.020"), ((), ())), "no car chosen: every line")
check(line_fits("Callout | Part number | Name | Fits | Notes", car), "not a part line: kept")

print("which catalogue a page is from")
con.execute("ALTER TABLE pages ADD COLUMN page_uid TEXT")
con.execute("UPDATE pages SET page_uid='epc:'||page_no")
con.execute("UPDATE pages SET text=? WHERE page_no='P2'",
            ("Models in this catalog: 210.020 E 300 DIESEL USA, 210.055 E 320 USA\n",))
info = catalog_info(con, {"page_uid": "epc:P2", "title": "Parts: model 210 (EPC catalog 45R), group 82"},
                    (("210.055",), ()))
check(info == {"catalog": "45R", "market": "USA"}, f"catalogue code and market: {info}")
info = catalog_info(con, {"page_uid": "epc:P1", "title": "Parts: model 210 (EPC catalog 66G), group 82"})
check(info == {"catalog": "66G", "market": ""}, f"no market named: none guessed: {info}")

print("another engine's pages in a LEMON manual")
from autorundown.search import _other_engines_clause, manual_engine_names  # noqa: E402
lm = sqlite3.connect(":memory:")
lm.execute("CREATE TABLE vehicles(doc_id TEXT, source_name TEXT)")
lm.execute("CREATE TABLE pages(doc_id TEXT, section_path TEXT, applicability TEXT)")
lm.execute("INSERT INTO vehicles VALUES ('f150','lemon')")
rows = ([("f150", "Engine - 3.5L Duratec > Water Pump", "other_variant")] * 30
        + [("f150", "Engine - 3.5L Duratec > Coolant Pump", "")] * 40      # unstamped, the bug
        + [("f150", "Engine - 3.5L EcoBoost > Coolant Pump", "")] * 40
        + [("f150", "Cooling - 3.5L EcoBoost and 3.5L Duratec > Notes", "")] * 5
        + [("f150", "Body > Doors", "")] * 10)
lm.executemany("INSERT INTO pages VALUES (?,?,?)", rows)
other, mine = manual_engine_names(lm, "f150")
check(other == ["3.5L DURATEC"] and mine == ["3.5L ECOBOOST"], f"engines learned: {other} / {mine}")
clause, args = _other_engines_clause(lm, "f150")
kept = lm.execute("SELECT section_path, COUNT(*) FROM pages p WHERE 1" + clause + " GROUP BY 1", args).fetchall()
check({k for k, _ in kept} == {"Engine - 3.5L EcoBoost > Coolant Pump", "Body > Doors",
                               "Cooling - 3.5L EcoBoost and 3.5L Duratec > Notes"},
      "another engine's pages left out, shared and plain pages kept")
lm.execute("UPDATE vehicles SET source_name='folder'")
lm.execute("INSERT INTO pages VALUES ('f150','x','')")           # new count: not the cached answer
check(_other_engines_clause(lm, "f150") == ("", []), "not a LEMON manual: nothing left out")

print("all passed" if not fails else f"{fails} FAILED")
raise SystemExit(1 if fails else 0)
