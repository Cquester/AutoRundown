"""The LEMON tab's folder import -- FREE, no API calls, ~3 s.

    python tests/test_lemon_import.py

Builds the sample LEMON archive (make_sample_lemon.py) in a temp folder and
checks: ingest.py --only imports just the named vehicle; the listing
(/api/import/lemon) marks what is imported and what is new; a phone on the
LAN can't list or start one; a vehicle folder that isn't in the listing is
refused; and a LEMON import from the API brings in only the picked vehicle.
Uses a throwaway AUTORUNDOWN_HOME, so nothing real is touched.
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo on sys.path)
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_lemon_"))
os.environ["AUTORUNDOWN_HOME"] = str(TMP / "home")  # before importing autorundown

from fastapi.testclient import TestClient  # noqa: E402

import ingest  # noqa: E402
import make_sample_lemon  # noqa: E402
from autorundown import server  # noqa: E402

fails = 0


def check(cond: bool, what: str) -> None:
    global fails
    print(("  ok   " if cond else "  FAIL ") + what)
    fails += not cond


def imported(db: Path) -> set[str]:
    with sqlite3.connect(db) as con:
        return {r[0] for r in con.execute("SELECT doc_id FROM vehicles WHERE complete=1")}


archive = make_sample_lemon.make(TMP / "lemon")
db = TMP / "index.db"

print("ingest.py --only")
rc = ingest.main(["--archive", str(archive), "--index", str(db), "--importer", "lemon",
                  "--only", "2015-ford-f-150"])
check(rc == 0, "imports the named vehicle")
check(len(imported(db)) == 1 and "f-150" in next(iter(imported(db))),
      f"only that one is in the index: {sorted(imported(db))}")
rc = ingest.main(["--archive", str(archive), "--index", str(db), "--importer", "lemon",
                  "--only", "no-such-car"])
check(rc == 1, "an unknown folder fails instead of importing nothing quietly")

APP = server.create_app(str(db))
pc = TestClient(APP, base_url="http://localhost:8756", client=("127.0.0.1", 50000))
phone = TestClient(APP, base_url="http://192.168.1.10:8756", client=("192.168.1.50", 50000))
same = {"Origin": "http://localhost:8756"}

print("listing")
r = pc.get("/api/import/lemon", params={"path": str(archive)}).json()
states = {v["folder"]: v["state"] for v in r["vehicles"]}
check(len(states) == 3, f"three vehicles listed: {sorted(states)}")
check(states.get("2015-ford-f-150") == "imported", "the imported one says so")
check(states.get("1987-mercedes-190e") == "new", "the others are new")
check(phone.get("/api/import/lemon", params={"path": str(archive)}).status_code == 403,
      "a phone on the LAN can't list folders")

print("import")
body = {"path": str(archive), "mode": "lemon", "vehicles": ["../../etc"]}
check(pc.post("/api/import", json=body, headers=same).status_code == 400,
      "a folder not in the listing is refused")
body["vehicles"] = []
check(pc.post("/api/import", json=body, headers=same).status_code == 400, "nothing picked is refused")
body["vehicles"] = ["1987-mercedes-190e"]
check(phone.post("/api/import", json=body, headers={"Origin": "http://192.168.1.10:8756"}).status_code == 403,
      "a phone can't start an import")
check(pc.post("/api/import", json=body, headers=same).status_code == 200, "the PC starts it")
for _ in range(120):
    s = pc.get("/api/import").json()
    if s["state"] != "running":
        break
    time.sleep(0.25)
check(s["state"] == "done", f"job finished: {s['state']}\n" + "\n".join(s.get("log", [])[-5:]))
check(any("--importer lemon" in l and "--only 1987-mercedes-190e" in l for l in s["log"]),
      "it ran ingest.py --importer lemon --only <the pick>")
r = pc.get("/api/import/lemon", params={"path": str(archive)}).json()
states = {v["folder"]: v["state"] for v in r["vehicles"]}
check(states == {"2015-ford-f-150": "imported", "1987-mercedes-190e": "imported",
                 "2008-toyota-camry": "new"}, f"only the pick was added: {states}")

print("manual contents (the Manual page tab before a question)")
f150 = next(d for d in imported(db) if "f-150" in d)
url = f"/api/manual/contents/{f150}"
top = pc.get(url).json()
check(top["path"] == [] and {"Engine, Cooling and Exhaust", "Brakes and Traction Control"}
      <= {c["name"] for c in top["children"]},
      f"the manual's own chapters at the top: {[c['name'] for c in top['children']]}")
n = pc.get(url, params={"path": ["Engine, Cooling and Exhaust", "Engine", "Specifications"]}).json()
check([p["title"] for p in n["pages"]] == ["Engine Oil Capacity"],
      f"a chapter lists its pages: {[p['title'] for p in n['pages']]}")
check(not any(c["name"] == "Engine Oil Capacity" for c in n["children"]),
      "a page is listed in its chapter, not as a chapter of its own")
check(n["count"] == len(n["pages"]) + sum(c["count"] for c in n["children"]), "the counts add up")
check(pc.get(url, params={"path": ["No such chapter"]}).status_code == 404, "an unknown chapter is 404")
check(phone.get(url).status_code in (200, 401, 403), "a phone gets the same rules as search")

print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
