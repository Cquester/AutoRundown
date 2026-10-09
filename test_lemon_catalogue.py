"""The "LEMON catalogue" import source -- FREE, no API calls, no network, ~3 s.

    python test_lemon_catalogue.py

Checks: the committed snapshot loads (279,988 vehicles, 65 makes); the
drop-downs narrow make -> year -> model; a LEMON manual imported under its
catalogue name is marked "imported" (the doc_id the `lemon` importer gives
it); picks are saved and survive a reload; a torrent index.json, when one is
configured, replaces the snapshot; and a phone on the LAN can neither browse
nor pick. Uses a throwaway AUTORUNDOWN_HOME, so nothing real is touched.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_lemcat_"))
os.environ["AUTORUNDOWN_HOME"] = str(TMP / "home")  # before importing autorundown

from fastapi.testclient import TestClient  # noqa: E402

import ingest  # noqa: E402
import make_sample_lemon  # noqa: E402
from autorundown import config, lemon_catalogue, server  # noqa: E402

fails = 0


def check(cond: bool, what: str) -> None:
    global fails
    print(("  ok   " if cond else "  FAIL ") + what)
    fails += not cond


LARIAT = "F-150 Lariat, 4D Pickup Extra Cab, 3.5L Eng VIN G, 4WD"   # a real catalogue name
make_sample_lemon.VEHICLES = [("2015-ford-lariat", "Ford", "2015", LARIAT)]
archive = make_sample_lemon.make(TMP / "lemon")
db = TMP / "index.db"
ingest.main(["--archive", str(archive), "--index", str(db), "--importer", "lemon"])

APP = server.create_app(str(db))
pc = TestClient(APP, base_url="http://localhost:8756", client=("127.0.0.1", 50000))
phone = TestClient(APP, base_url="http://192.168.1.10:8756", client=("192.168.1.50", 50000))
same = {"Origin": "http://localhost:8756"}
URL = "/api/import/lemon-catalogue"

print("snapshot")
r = pc.get(URL).json()
check(r["total"] == 279988 and len(r["makes"]) == 65, f"{r['total']:,} vehicles, {len(r['makes'])} makes")
check("snapshot" in r["source"], f"source says so: {r['source']}")
check(not r["years"] and not r["rows"], "no make chosen: no years, no rows")

print("drop-downs")
r = pc.get(URL, params={"make": "Ford"}).json()
check("2015" in r["years"] and r["years"] == sorted(r["years"], reverse=True), "Ford's years, newest first")
check(not r["rows"], "a make alone lists no rows")
r = pc.get(URL, params={"make": "Ford", "year": "2015"}).json()
check(any(m["name"] == "F-150" for m in r["models"]), "2015 Ford has the F-150")
r = pc.get(URL, params={"make": "Ford", "year": "2015", "model": "F-150"}).json()
check(r["rows"] and all(v["model"] == "F-150" for v in r["rows"]), f"F-150 only: {len(r['rows'])} variants")
r87 = pc.get(URL, params={"make": "Mercedes Benz", "year": "1987", "model": "190E"}).json()
check("2.3, 102.985, Automatic" in [v["engine"] for v in r87["rows"]], "1987 190E variants listed")
r = pc.get(URL, params={"make": "Nope", "year": "2015"}).json()
check(not r["years"] and not r["rows"], "an unknown make lists nothing")

print("imported")
rows = {v["name"]: v for v in pc.get(URL, params={"make": "Ford", "year": "2015", "model": "F-150"}).json()["rows"]}
check(rows[LARIAT]["state"] == "imported", "the imported manual is marked imported")
check(sum(v["state"] == "imported" for v in rows.values()) == 1, "and only that one")

print("picks")
other = next(v["doc_id"] for v in rows.values() if v["state"] == "new")
check(phone.post(URL + "/picks", json={"doc_ids": [other]},
                 headers={"Origin": "http://192.168.1.10:8756"}).status_code == 403, "a phone can't pick")
check(phone.get(URL).status_code == 403, "a phone can't browse")
r = pc.post(URL + "/picks", json={"doc_ids": [other]}, headers=same)
check(r.status_code == 200 and r.json()["picked"] == 1, "the PC picks one")
check(lemon_catalogue.PICKS.is_file() and str(TMP) in str(lemon_catalogue.PICKS), "saved in this AUTORUNDOWN_HOME")
rows = {v["doc_id"]: v for v in pc.get(URL, params={"make": "Ford", "year": "2015"}).json()["rows"]}
check(rows[other]["state"] == "picked", "and it shows as picked")
r = pc.post(URL + "/picks", json={"doc_ids": [other], "picked": False}, headers=same)
check(r.json()["picked"] == 0, "unpicking removes it")
check(pc.post(URL + "/picks", json={"doc_ids": ["x"] * 5001}, headers=same).status_code == 400,
      "an oversized request is refused")

print("torrent index.json")
tdir = TMP / "torrent" / "lemon"
tdir.mkdir(parents=True)
(tdir / "index.json").write_text(json.dumps({"database": "lemon", "vehicles": [
    {"make": "Ford", "years": ["2015"], "model": "F-150", "engine": "Lariat, 4D Pickup Extra Cab, 3.5L Eng VIN G, 4WD",
     "uriPath": "/Ford/2015/x/"},
    {"make": "Yugo", "years": ["1987"], "model": "GV", "engine": None, "uriPath": "/Yugo/1987/GV/"}]}),
    encoding="utf-8")
c = config.load()
c.lemon_torrent_dir = str(TMP / "torrent")
c.save()
r = pc.get(URL, params={"make": "Ford", "year": "2015"}).json()
check(r["total"] == 2 and "torrent" in r["source"], f"the torrent's list replaces the snapshot: {r['source'][:40]}…")
check([v["state"] for v in r["rows"]] == ["imported"], "imported is still recognised")
check(pc.get(URL, params={"make": "Yugo", "year": "1987"}).json()["rows"][0]["name"] == "GV",
      "a vehicle with no engine is named by its model")

print(f"\n{fails} failure(s)")
raise SystemExit(1 if fails else 0)
