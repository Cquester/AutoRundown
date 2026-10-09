"""The LEMON tab's catalogue tree -- FREE, no API calls, no network, ~3 s.

    python test_lemon_catalogue.py

Checks: the committed snapshot loads (279,988 vehicles, 65 makes); the tree
opens make > year (newest first) > model > variant with the right counts; a
LEMON manual imported under its catalogue name is marked "imported" on its
variant and counted on every folder above it; picking a folder picks
everything in it, unpicking one car inside splits the folder and leaves the
rest picked, unpicking the folder clears it all; picks survive a restart;
a torrent index.json, when one is configured, replaces the snapshot; and a
phone on the LAN can neither browse nor pick. Uses a throwaway
AUTORUNDOWN_HOME, so nothing real is touched.
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


LARIAT = "Lariat, 4D Pickup Extra Cab, 3.5L Eng VIN G, 4WD"   # a real catalogue variant of the 2015 F-150
make_sample_lemon.VEHICLES = [("2015-ford-lariat", "Ford", "2015", f"F-150 {LARIAT}")]
archive = make_sample_lemon.make(TMP / "lemon")
db = TMP / "index.db"
ingest.main(["--archive", str(archive), "--index", str(db), "--importer", "lemon"])

APP = server.create_app(str(db))
pc = TestClient(APP, base_url="http://localhost:8756", client=("127.0.0.1", 50000))
phone = TestClient(APP, base_url="http://192.168.1.10:8756", client=("192.168.1.50", 50000))
same = {"Origin": "http://localhost:8756"}
URL = "/api/import/lemon-catalogue"


def folder(*path: str) -> dict:
    r = pc.get(URL, params={"path": json.dumps(list(path))})
    assert r.status_code == 200, (path, r.status_code, r.text)
    return r.json()


def rows(*path: str) -> dict:
    return {r["name"]: r for r in folder(*path)["rows"]}


def pick(path: list[str], picked: bool = True) -> int:
    return pc.post(URL + "/picks", json={"path": path, "picked": picked}, headers=same).status_code


print("tree")
r = folder()
check(r["total"] == 279988 and len(r["rows"]) == 65, f"{r['total']:,} vehicles, {len(r['rows'])} makes")
check("snapshot" in r["source"], f"source says so: {r['source']}")
makes = rows()
check(makes["Ford"]["count"] == 38549 and makes["Ford"]["level"] == "make", "Ford: 38,549 vehicles")
years = folder("Ford")["rows"]
check([y["name"] for y in years] == sorted((y["name"] for y in years), reverse=True), "years newest first")
f150 = rows("Ford", "2015")["F-150"]
check(f150["count"] == 78 and f150["level"] == "model", "2015 F-150: 78 variants")
v = rows("Ford", "2015", "F-150")[LARIAT]
check(v["level"] == "variant" and v["doc_id"] == "2015-ford-f-150-" + LARIAT.lower()
      .replace(", ", "-").replace(" ", "-").replace(".", "-"), f"variant row carries its doc_id: {v['doc_id']}")
check(rows("Mercedes Benz", "1987", "190E").keys() >= {"2.3, 102.985, Automatic", "2.6"}, "1987 190E variants")
check(pc.get(URL, params={"path": json.dumps(["Nope"])}).status_code == 404, "an unknown make is a 404")
check(pc.get(URL, params={"path": "not json"}).status_code == 400, "a malformed path is refused")

print("imported")
check(v["state"] == "imported", "the imported manual's variant says imported")
check(sum(x["state"] == "imported" for x in rows("Ford", "2015", "F-150").values()) == 1, "and only that one")
check(makes["Ford"]["imported"] == 1 and f150["imported"] == 1 and rows("Ford")["2015"]["imported"] == 1,
      "counted on Ford, 2015 and F-150")
check(makes["Toyota"]["imported"] == 0, "not on other makes")

print("picks")
check(phone.get(URL).status_code == 403, "a phone can't browse")
check(phone.post(URL + "/picks", json={"path": ["Ford"]},
                 headers={"Origin": "http://192.168.1.10:8756"}).status_code == 403, "a phone can't pick")
check(pick(["Mercedes Benz", "1987"]) == 200, "pick a year")
check(folder()["picked_total"] == 17 and rows("Mercedes Benz", "1987")["190E"]["picked"] == "all",
      "17 picked, every model in it ticked")
check(rows()["Mercedes Benz"]["picked"] == "some", "the make shows part-picked")
pick(["Mercedes Benz", "1987", "190E", "2.3, 102.985, Automatic"], False)
check(folder()["picked_total"] == 16, "unpick one car inside: 16 left")
check(rows("Mercedes Benz")["1987"]["picked"] == "some" and rows("Mercedes Benz", "1987")["190D"]["picked"] == "all",
      "the year is part-picked, its other models still all")
check(rows("Mercedes Benz", "1987", "190E")["2.6"]["picked"] == "all", "the car's siblings still picked")
pick(["Mercedes Benz"])
check(folder()["picked_total"] == 3063 and len(lemon_catalogue.picks()) == 1, "pick the make: one pick, 3,063 cars")
pick(["Mercedes Benz"], False)
check(folder()["picked_total"] == 0 and not lemon_catalogue.picks(), "unpick the make: nothing left")
pick(["Ford", "2015", "F-150"])
check(len(lemon_catalogue.picked_vehicles()) == 78, "picked_vehicles() lists the 78 for the download step")
check(lemon_catalogue.PICKS.is_file() and str(TMP) in str(lemon_catalogue.PICKS), "saved in this AUTORUNDOWN_HOME")
check(pick(["Nope"]) == 404 and pick([]) == 400, "unknown or empty paths are refused")

print("torrent index.json")
tdir = TMP / "torrent" / "lemon"
tdir.mkdir(parents=True)
(tdir / "index.json").write_text(json.dumps({"database": "lemon", "vehicles": [
    {"make": "Ford", "years": ["2015"], "model": "F-150", "engine": LARIAT, "uriPath": "/Ford/2015/x/"},
    {"make": "Yugo", "years": ["1987"], "model": "GV", "engine": None, "uriPath": "/Yugo/1987/GV/"}]}),
    encoding="utf-8")
c = config.load()
c.lemon_torrent_dir = str(TMP / "torrent")
c.save()
r = folder()
check(r["total"] == 2 and "torrent" in r["source"], "the torrent's list replaces the snapshot")
check(rows("Ford", "2015", "F-150")[LARIAT]["state"] == "imported", "imported is still recognised")
check(rows("Yugo", "1987", "GV")["GV"]["level"] == "variant", "a car with no engine is named by its model")

print(f"\n{fails} failure(s)")
raise SystemExit(1 if fails else 0)
