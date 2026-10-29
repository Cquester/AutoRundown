"""The car box's "Not downloaded" cars and "Add this car" -- FREE, no API calls, ~5 s.

    python tests/test_catalogue_suggest.py

The car box also suggests LEMON catalogue cars the index does not hold
(/api/catalogue/suggest, lemon_catalogue.search) and adds one from the box
(/api/import/lemon-add). Checks: the typed words and a year narrow the list;
a year alone asks nothing; a car in the index is never offered; a phone sees
the list but can't add; a car whose LEMON folder sits beside the manuals
already imported is imported by the same `lemon` job Shop setup runs, and
then drops out of the list; one that isn't on this PC gets its page on
LEMON's site (tests/test_lemon_zip.py: the zip the user saves from it); a
car not in the catalogue is refused. Uses a throwaway AUTORUNDOWN_HOME,
Downloads and index, so nothing real is touched.
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo on sys.path)
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_catsug_"))
os.environ["AUTORUNDOWN_HOME"] = str(TMP / "home")  # before importing autorundown
os.environ["AUTORUNDOWN_DOWNLOADS"] = str(TMP / "downloads")   # never the PC's own Downloads

from fastapi.testclient import TestClient  # noqa: E402

import ingest  # noqa: E402
import make_sample_lemon  # noqa: E402
from autorundown import lemon_catalogue, server  # noqa: E402

fails = 0


def check(cond: bool, what: str) -> None:
    global fails
    print(("  ok   " if cond else "  FAIL ") + what)
    fails += not cond


CAMRY = ["Toyota", "2008", "Camry", "LE, 2.4 E, Automatic"]   # the sample's name for it

archive = make_sample_lemon.make(TMP / "lemon")
db = TMP / "index.db"
rc = ingest.main(["--archive", str(archive), "--index", str(db), "--importer", "lemon",
                  "--only", "2015-ford-f-150"])
check(rc == 0, "the sample F-150 is imported (the Camry is left on disk)")

APP = server.create_app(str(db))
pc = TestClient(APP, base_url="http://localhost:8756", client=("127.0.0.1", 50000))
phone = TestClient(APP, base_url="http://192.168.1.10:8756", client=("192.168.1.50", 50000))
same = {"Origin": "http://localhost:8756"}


def suggest(q: str, who=pc) -> dict:
    r = who.get("/api/catalogue/suggest", params={"q": q})
    assert r.status_code == 200, r.text
    return r.json()


print("suggestions")
r = suggest("2016 ford f-150")
check(r["total"] > 10 and all(x["year"] == "2016" and x["model"] == "F-150" for x in r["rows"]),
      f"a year and a model: that year's cars of that model ({r['total']})")
check(len(r["rows"]) <= 6 and r["rows"][0]["label"].startswith("2016 Ford F-150 "),
      f"a handful, labelled as the box shows them: {r['rows'][0]['label']!r}")
check(r["rows"][0]["doc_id"] == lemon_catalogue.doc_id(*r["rows"][0]["path"]),
      "each row's doc_id is the one the lemon importer gives that car")
narrow = suggest("2016 ford f-150 lariat crew cab 3.5 4wd")
check(narrow["total"] == 1 and "Lariat, 4D Pickup Crew Cab, 3.5L" in narrow["rows"][0]["engine"],
      f"more words narrow it to one car: {[x['label'] for x in narrow['rows']]}")
check(suggest("2016")["total"] == 0 and suggest("x")["total"] == 0,
      "a year alone or one letter asks nothing (too many cars)")
check(suggest("2008 camry le 2.4 automatic")["total"] == 1, "the sample Camry is offered (not in the index)")
check(suggest("2016 ford f-150", who=phone)["total"] == r["total"], "a phone sees the same list")

print("add this car")
check(phone.post("/api/import/lemon-add", json={"path": CAMRY},
                 headers={"Origin": "http://192.168.1.10:8756"}).status_code == 403,
      "a phone can't add a car")
check(pc.post("/api/import/lemon-add", json={"path": ["Ford", "2016"]}, headers=same).status_code == 400,
      "a folder, not a car, is refused")
check(pc.post("/api/import/lemon-add", json={"path": ["Ford", "2016", "F-150", "Nope"]},
              headers=same).status_code == 404, "a car not in the catalogue is refused")

res = pc.post("/api/import/lemon-add", json={"path": CAMRY}, headers=same)
check(res.status_code == 200 and res.json().get("action") == "import",
      f"the Camry's folder is found beside the F-150's: {res.json()}")
for _ in range(600):
    st = pc.get("/api/import").json()
    if st["state"] != "running":
        break
    time.sleep(0.05)
check(st["state"] == "done" and st.get("mode") in (None, "lemon"), f"the lemon import job finished: {st['state']}")
with sqlite3.connect(db) as con:
    have = {d for (d,) in con.execute("SELECT doc_id FROM vehicles WHERE complete=1")}
check(lemon_catalogue.doc_id(*CAMRY) in have, "the Camry is in the index under the catalogue's doc_id")
check(suggest("2008 camry le 2.4 automatic")["total"] == 0, "and is no longer offered as not downloaded")

res = pc.post("/api/import/lemon-add", json={"path": narrow["rows"][0]["path"]}, headers=same).json()
check(res.get("action") == "download" and res["links"]["lemon"].startswith("https://lemon-manuals.la/Ford/2016/F-150%20"),
      f"a car that isn't on this PC: its page on LEMON's site, for the user to open: {res}")
check("charm" not in res["links"], "CHARM's backup page only for 1982-2013 cars")
check(not lemon_catalogue.picks(), "and it is not ticked on Shop setup's download list")

print("cars LEMON names only by codes (lemon_labels.csv)")
B23 = ("Mercedes Benz", "1987", "190E", "2.3, 102.983, Automatic")
check(lemon_catalogue.label(*B23).startswith("190 E 2.3-16 (201.034"),
      f"102.983 is shown as the 190 E 2.3-16: {lemon_catalogue.label(*B23)}")
check(lemon_catalogue.label("Toyota", "2008", "Camry", "LE, 2.4 E, Automatic") == lemon_catalogue.vehicle_name(
      "Camry", "LE, 2.4 E, Automatic"), "a car with a real name keeps it")
charm = "1987-mercedes-benz-190e-2-3-16-201-034-l4-2-3l-102-983"
check(lemon_catalogue.on_pc({charm: True}, *B23) == charm,
      "a manual named another way for the same Baumuster and engine counts as this car")
check(lemon_catalogue.on_pc({charm: True}, "Mercedes Benz", "1987", "190E", "2.3, 102.985, Automatic") is None,
      "but not as the 8-valve 2.3 (102.985)")
check(lemon_catalogue.on_pc({"1986-mercedes-benz-190e-2-3-16-201-034-l4-2-3l-102-983": True}, *B23) is None,
      "nor as another year's car")
hits = lemon_catalogue.search("1987 190 E 2.3-16", str(db))
check(hits["total"] >= 1 and all("2.3-16" in r["label"] for r in hits["rows"]),
      f"\"190 E 2.3-16\" finds it by its name: {[r['label'] for r in hits['rows']]}")

print("FAILED" if fails else "all passed")
sys.exit(1 if fails else 0)
