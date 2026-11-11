"""Account levels: a manager adds cars from their own computer -- FREE, no network, ~10 s.

    python tests/test_manager.py

The user, 2026-11-10: a server with no screen of its own made adding a car
from another computer hard. A mechanic account searches and asks; a manager
account also adds cars (Add a car: start it, upload LEMON's zip from their
own computer, follow the import) and removes the cars *they* added. Keys,
accounts, settings, other imports and the Mercedes VM stay with the PC.

Checks, on the real FastAPI app with a throwaway AUTORUNDOWN_HOME, Downloads
and index:
  - an older auth.db (no level column) opens, every account a mechanic;
  - only the PC sets a level, and only to mechanic or manager;
  - /api/me says manager / can_add_cars;
  - a mechanic gets 403 on every Add a car route; a manager gets exactly
    those, and still 403 on settings, accounts, the folder import, cancel,
    the VM, the remove-anything import;
  - a manager uploads the Camry's zip: imported; the answer and the job
    status carry no path of the PC's and no log;
  - their account lists the car; another manager can't remove it, they can;
  - a mechanic who was a manager loses it at once (same sign-in).
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo and tools/ on sys.path)
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_manager_"))
DL = TMP / "downloads"
DL.mkdir()
os.environ["AUTORUNDOWN_HOME"] = str(TMP / "home")        # before importing autorundown
os.environ["AUTORUNDOWN_DOWNLOADS"] = str(DL)
os.environ["AUTORUNDOWN_RECYCLE_TO"] = str(TMP / "bin")

from fastapi.testclient import TestClient  # noqa: E402

import ingest  # noqa: E402
import make_sample_lemon  # noqa: E402
from autorundown import auth, lemon_catalogue  # noqa: E402
from autorundown import server  # noqa: E402

fails = 0


def check(cond: bool, what: str) -> None:
    global fails
    print(("  ok   " if cond else "  FAIL ") + what)
    fails += not cond


print("an auth.db from before levels")
old = TMP / "old_auth.db"
con = sqlite3.connect(old)
con.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, "
            "created TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0, monthly_cap_usd REAL)")
con.execute("INSERT INTO users(name, created) VALUES ('Old Timer', '2026-01-01 00:00:00')")
con.commit()
con.close()
c = auth.connect(old)
check([u["role"] for u in auth.users(c)] == ["mechanic"], "opens; the account is a mechanic")
c.close()

CAMRY = ["Toyota", "2008", "Camry", "LE, 2.4 E, Automatic"]
DOC = lemon_catalogue.doc_id(*CAMRY)
TOP = f"2008 Toyota {lemon_catalogue.vehicle_name(*CAMRY[2:])}"
archive = make_sample_lemon.make(TMP / "a" / "lemon")
db = TMP / "index.db"
check(ingest.main(["--archive", str(archive), "--index", str(db), "--importer", "lemon",
                   "--only", "2015-ford-f-150"]) == 0, "the sample F-150 is imported")
camry = TMP / "camry_src"
shutil.move(str(archive / "2008-toyota-camry"), camry)
zpath = TMP / "camry.zip"
with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
    for f in sorted(camry.rglob("*")):
        if f.is_file():
            z.write(f, f"{TOP}/{f.relative_to(camry).as_posix()}")
ZIP = zpath.read_bytes()

APP = server.create_app(str(db), TMP / "auth.db")
LAN = "http://192.168.1.20:8756"
pc = TestClient(APP, base_url="http://localhost:8756", client=("127.0.0.1", 50000))
same = {"Origin": "http://localhost:8756"}
lan = {"Origin": LAN}


def device(name: str, role: str | None) -> tuple[TestClient, int]:
    """A computer on the shop network, signed in as a new account."""
    uid = pc.post("/api/admin/users", json={"name": name}, headers=same).json()["id"]
    if role:
        r = pc.post(f"/api/admin/users/{uid}", json={"role": role}, headers=same)
        assert r.status_code == 200, r.text
    g = pc.post(f"/api/admin/users/{uid}/qr", headers=same).json()
    d = TestClient(APP, base_url=LAN, client=("192.168.1.50", 50000))
    r = d.post("/api/auth/signin", json={"code": g["code"]}, headers=lan)
    assert r.status_code == 200, r.text
    return d, uid


mech, mech_id = device("Sam", None)
boss, boss_id = device("Dana", "manager")
other, other_id = device("Lee", "manager")

print("levels")
check(pc.post(f"/api/admin/users/{mech_id}", json={"role": "owner"}, headers=same).status_code == 400,
      "only mechanic or manager (the owner is the PC, never an account)")
check(boss.post(f"/api/admin/users/{mech_id}", json={"role": "manager"}, headers=lan).status_code == 403,
      "a manager can't change a level")
users = {u["name"]: u["role"] for u in pc.get("/api/admin/users").json()["users"]}
check(users == {"Sam": "mechanic", "Dana": "manager", "Lee": "manager"}, f"the PC sees each level: {users}")
m, b = mech.get("/api/me").json(), boss.get("/api/me").json()
check(not m["manager"] and not m["can_add_cars"], "/api/me: a mechanic can't add cars")
check(b["manager"] and b["can_add_cars"] and not b["admin"], "/api/me: a manager can, and is not the PC")
check(pc.get("/api/me").json()["can_add_cars"], "/api/me: the PC can")

print("what each may reach")
UP = "/api/import/lemon-upload"
q = {"path": json.dumps(CAMRY)}
zipped = {**lan, "Content-Type": "application/zip"}
for path, how in [("/api/import/lemon-add", "post"), ("/api/import", "get"), ("/api/manager/cars", "get")]:
    r = mech.post(path, json={"path": CAMRY}, headers=lan) if how == "post" else mech.get(path)
    check(r.status_code == 403, f"a mechanic: {how.upper()} {path} is 403")
check(mech.put(UP, params=q, content=ZIP, headers=zipped).status_code == 403 and not any(DL.iterdir()),
      "a mechanic can't upload a zip (nothing written)")
for method, path, body in [
        ("get", "/api/settings", None), ("post", "/api/settings", {"require_signin": False}),
        ("get", "/api/admin/users", None), ("post", "/api/admin/users", {"name": "X"}),
        ("post", "/api/import", {"path": str(archive), "mode": "lemon", "vehicles": ["2008-toyota-camry"]}),
        ("post", "/api/import", {"path": ".", "mode": "remove", "vehicles": ["2015-ford-f-150"]}),
        ("post", "/api/import/cancel", None), ("post", "/api/import/lemon-zip", {"path": CAMRY}),
        ("get", "/api/import/manuals", None), ("get", "/api/vms", None), ("get", "/api/browse", None)]:
    r = getattr(boss, method)(path, **({"json": body, "headers": lan} if method == "post" else {}))
    check(r.status_code == 403, f"a manager: {method.upper()} {path} stays the PC's ({r.status_code})")
r = boss.post("/api/import/lemon-add", json={"path": CAMRY},
              headers={**lan, "Sec-Fetch-Site": "cross-site"})
check(r.status_code == 403, "a manager's Add a car from another web site is refused")

print("a manager adds a car from their own computer")
r = boss.post("/api/import/lemon-add", json={"path": CAMRY}, headers=lan)
d = r.json()
check(r.status_code == 200 and d["action"] == "download" and "downloads" not in d and d["links"]["lemon"],
      f"Add this car: LEMON's page to download from, no folder of the PC's named: {sorted(d)}")
r = boss.put(UP, params=q, content=ZIP, headers=zipped)
d = r.json()
check(r.status_code == 200 and d["action"] == "import" and "zip" not in d and set(d["job"]) == {"started"},
      f"the uploaded zip is imported; no path of the PC's in the answer: {d}")
for _ in range(600):
    st = boss.get("/api/import").json()
    if st["state"] != "running":
        break
    time.sleep(0.05)
check(st["state"] == "done" and "log" not in st and set(st) == {"state", "elapsed", "progress"},
      f"the manager follows the import, without its log: {st}")
with sqlite3.connect(db) as con:
    check(con.execute("SELECT 1 FROM vehicles WHERE doc_id=? AND complete=1", (DOC,)).fetchone() is not None,
          "the Camry is in the index")
check("log" in pc.get("/api/import").json(), "the PC still sees the whole job")

print("the cars a manager added")
mine = boss.get("/api/manager/cars").json()["cars"]
check([x["doc_id"] for x in mine] == [DOC] and "Camry" in mine[0]["label"], f"Dana's account lists the Camry: {mine}")
check(other.get("/api/manager/cars").json()["cars"] == [], "Lee's lists nothing")
r = other.post("/api/manager/cars/remove", json={"doc_id": DOC}, headers=lan)
check(r.status_code == 403, "another manager can't remove it")
r = boss.post("/api/manager/cars/remove", json={"doc_id": "2015-ford-f-150"}, headers=lan)
check(r.status_code == 403, "nor can Dana remove a car she didn't add")
r = boss.post("/api/manager/cars/remove", json={"doc_id": DOC}, headers=lan)
check(r.status_code == 200, "Dana removes the Camry")
for _ in range(600):
    st = pc.get("/api/import").json()
    if st["state"] != "running":
        break
    time.sleep(0.05)
with sqlite3.connect(db) as con:
    gone = con.execute("SELECT 1 FROM vehicles WHERE doc_id=?", (DOC,)).fetchone() is None
    kept = con.execute("SELECT 1 FROM vehicles WHERE doc_id LIKE '%f-150%'").fetchone() is not None
check(st["state"] == "done" and gone and kept, "the Camry is out, the F-150 untouched: "
      + f"{st['state']} gone={gone} kept={kept} " + " | ".join(st.get("log", [])[-5:]))
check(boss.get("/api/manager/cars").json()["cars"] == [], "  ... and off Dana's list")

print("a level taken back")
check(pc.post(f"/api/admin/users/{boss_id}", json={"role": "mechanic"}, headers=same).status_code == 200,
      "the PC makes Dana a mechanic")
check(not boss.get("/api/me").json()["can_add_cars"]
      and boss.post("/api/import/lemon-add", json={"path": CAMRY}, headers=lan).status_code == 403,
      "  ... her next request can't add a car (no new sign-in needed)")


def wait_done(c: TestClient) -> dict:
    for _ in range(600):
        st = c.get("/api/import").json()
        if st["state"] != "running":
            return st
        time.sleep(0.05)
    return st


print("sign-in off: every device adds cars (the user, 2026-11-10)")
check(pc.post("/api/settings", json={"require_signin": False}, headers=same).status_code == 200,
      "the PC turns sign-in off")
laptop = TestClient(APP, base_url=LAN, client=("192.168.1.60", 50000))
phone = TestClient(APP, base_url=LAN, client=("192.168.1.61", 50000))
lm = laptop.get("/api/me").json()
check(lm["can_add_cars"] and lm["shared_cars"] and not lm["signed_in"] and not lm["admin"],
      "a device not signed in may add cars, on the shared list")
check(mech.get("/api/me").json()["can_add_cars"], "so may a signed-in mechanic")
for method, path, body in [("get", "/api/settings", None), ("post", "/api/admin/users", {"name": "X"}),
                           ("post", "/api/import", {"path": ".", "mode": "remove", "vehicles": ["x"]}),
                           ("get", "/api/vms", None)]:
    r = getattr(laptop, method)(path, **({"json": body, "headers": lan} if method == "post" else {}))
    check(r.status_code == 403, f"  ... but {method.upper()} {path} stays the PC's ({r.status_code})")
r = laptop.put(UP, params=q, content=ZIP, headers=zipped)
check(r.status_code == 200 and r.json()["action"] == "import" and "zip" not in r.json(),
      f"the laptop uploads the Camry's zip: {r.status_code}")
st = wait_done(laptop)
check(st["state"] == "done" and "log" not in st, "  ... imported; no log for it")
shared = phone.get("/api/manager/cars").json()["cars"]
check([x["doc_id"] for x in shared] == [DOC], "another device not signed in sees it on the shared list")
check(mech.get("/api/manager/cars").json()["cars"] == [], "  ... a signed-in mechanic's own list doesn't have it")
check(phone.post("/api/manager/cars/remove", json={"doc_id": DOC}, headers=lan).status_code == 200,
      "  ... and that other device can remove it")
wait_done(pc)
with sqlite3.connect(db) as con:
    check(con.execute("SELECT 1 FROM vehicles WHERE doc_id=?", (DOC,)).fetchone() is None, "  ... gone")

check(pc.post("/api/settings", json={"require_signin": True}, headers=same).status_code == 200,
      "sign-in on again")
check(laptop.post("/api/import/lemon-add", json={"path": CAMRY}, headers=lan).status_code in (401, 403),
      "  ... a device not signed in can't add a car (it must sign in)")
check(not mech.get("/api/me").json()["can_add_cars"], "  ... and a mechanic is a mechanic again")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'all passed' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
