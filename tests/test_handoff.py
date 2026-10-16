"""Cars sent from a phone to the desk (handoff.py) -- FREE, no API calls, ~2 s.

    python tests/test_handoff.py

Drives the real FastAPI app in-process: this PC (127.0.0.1, the desk and
admin), phones on the LAN signed in through the real sign-in QR flow, and a
phone that is not signed in. Uses a throwaway AUTORUNDOWN_HOME, so the real
config, accounts and index are not touched.
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo on sys.path)
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_handoff_"))
os.environ["AUTORUNDOWN_HOME"] = str(TMP)  # before importing autorundown

from fastapi.testclient import TestClient  # noqa: E402

from autorundown import handoff, index, server  # noqa: E402

fails = 0


def check(cond: bool, what: str) -> None:
    global fails
    print(("  ok   " if cond else "  FAIL ") + what)
    fails += not cond


def client(addr: str, host: str = "localhost:8756") -> TestClient:
    return TestClient(APP, base_url=f"http://{host}", client=(addr, 50000))


def post(c: TestClient, path: str, body: dict | None = None, origin: str | None = "same"):
    headers = {}
    if origin == "same":
        headers["Origin"] = str(c.base_url).rstrip("/")
    elif origin:
        headers["Origin"] = origin
    return c.post(path, json=body or {}, headers=headers)


# a W211-like car with two variants on two engines, and a plain manual
VARIANTS = [
    {"name": "E 320", "baumuster": ["211.065"], "engine_families": {"112": "M112 · V6 petrol"},
     "engine_by_bm": {"211.065": ["112.949"]}},
    {"name": "E 500 <b>4MATIC</b>", "baumuster": ["211.083"],
     "engine_families": {"113": "M113 · V8 petrol", "273": "M273 · V8 petrol"},
     "engine_by_bm": {"211.083": ["113.967", "273.960"]}},
]
idx = TMP / "index.db"
conn = index.connect(idx)
conn.execute("INSERT INTO vehicles(doc_id, source_name, make, model, year_range, variants) "
             "VALUES ('w211','mercedes-wis','Mercedes-Benz','W211 E-Class','2003-2009',?)",
             (json.dumps(VARIANTS),))
conn.execute("INSERT INTO vehicles(doc_id, make, model, year_range) "
             "VALUES ('f150','Ford','F-150','2015')")
conn.commit()
conn.close()
APP = server.create_app(str(idx), TMP / "auth.db")

pc = client("127.0.0.1")
anon = client("192.168.1.40", "192.168.1.20:8756")
anon2 = client("192.168.1.41", "192.168.1.20:8756")


def sign_in(addr: str, uid: int) -> TestClient:
    """A phone scans a sign-in QR the PC made for mechanic `uid`."""
    c = client(addr, "192.168.1.20:8756")
    g = post(pc, f"/api/admin/users/{uid}/qr").json()
    r = c.get("/signin?" + g["url"].split("?", 1)[1], follow_redirects=False)
    assert r.status_code == 303 and c.cookies.get("ar_token"), r.text
    return c


def cars(c: TestClient) -> list[dict]:
    r = c.get("/api/handoff")
    assert r.status_code == 200, r.text
    return r.json()["cars"]


E320 = {"doc_id": "w211", "variant": 0, "engine": "112", "year": 2004, "vin": "WDBUF65J04A123456"}

print("sign-in off: a shop-wide list")
r = post(anon, "/api/handoff", E320)
check(r.status_code == 200 and r.json()["label"] == "2004 Mercedes-Benz E 320",
      "a phone not signed in sends a car; the label is built from the index")
got = cars(pc)
check(len(got) == 1 and got[0]["from"] == "a phone (not signed in)" and got[0]["vin"] == E320["vin"]
      and got[0]["variant"] == 0 and got[0]["engine"] == "112" and got[0]["year"] == 2004,
      "the PC sees it: the structured pick, VIN, and who sent it")
check("user_id" not in got[0], "  ... without the sender's account id")
check(len(cars(anon2)) == 1 and anon2.get("/api/handoff").json()["shared"] is True,
      "another device not signed in sees it too (shared, as the UI says)")
check(pc.get("/api/handoff").json()["shared"] is False, "  ... the PC's list is not 'shared'")
check(post(pc, "/api/handoff", E320).status_code == 400, "the PC is the desk: it cannot send")
r = post(anon, "/api/handoff", {**E320, "label": "<script>x</script>"})
check(r.status_code == 200 and "script" not in r.json()["label"],
      "a label sent by the phone is ignored")

print("the pick is checked")
for bad, what in (
        ({"doc_id": "nope"}, "an unknown vehicle"),
        ({"doc_id": "w211", "variant": 2}, "a variant past the end of the list"),
        ({"doc_id": "w211", "variant": -1}, "a negative variant"),
        ({"doc_id": "w211", "engine": "112; DROP"}, "an engine that is not family numbers"),
        ({"doc_id": "w211", "engine": "272"}, "an engine the car was never built with"),
        ({"doc_id": "w211", "variant": 0, "engine": "113"}, "an engine of another variant"),
        ({"doc_id": "f150", "variant": 0}, "a variant of a car that has none"),
        ({"doc_id": "w211", "vin": "WDBUF65J04A12345O"}, "a VIN with an O"),
        ({"doc_id": "w211", "vin": "WDB12"}, "a VIN too short"),
        ({"doc_id": "w211", "vin": "WDB<img src=x>1234"}, "a VIN with markup"),
        ({"doc_id": "w211", "year": 1800}, "the year 1800"),
        ({"doc_id": "w211", "variant": "x"}, "a variant that is not a number"),
        ({"doc_id": "x" * 500}, "a 500-character doc_id"),
        ({}, "no vehicle at all")):
    r = post(anon, "/api/handoff", bad)
    check(r.status_code in (400, 422), f"refused: {what} ({r.status_code})")
check(post(anon, "/api/handoff", {"doc_id": "w211", "engine": "113,273"}).status_code == 200,
      "a whole series with an engine choice is accepted")
check(post(anon, "/api/handoff", {"doc_id": "f150"}).status_code == 200,
      "a plain manual with no variants is accepted")
check(post(anon, "/api/handoff", E320, origin="http://evil.example").status_code == 403,
      "a cross-site page cannot send a car")

print("mechanics")
sam_id = post(pc, "/api/admin/users", {"name": "Sam"}).json()["id"]
dax_id = post(pc, "/api/admin/users", {"name": "Daxton"}).json()["id"]
sam = sign_in("192.168.1.50", sam_id)
sam_tablet = sign_in("192.168.1.51", sam_id)
dax = sign_in("192.168.1.60", dax_id)
check(post(dax, "/api/handoff", {**E320, "variant": 1, "engine": "113", "year": 2005}).status_code == 200,
      "Daxton's phone sends a car")
got = cars(pc)
check(got[0]["from"] == "Daxton" and got[0]["label"] == "2005 Mercedes-Benz E 500 <b>4MATIC</b>",
      "the PC sees it first, labelled with Daxton's name (text; the page escapes it)")
check(len(cars(sam)) == 0, "Sam's phone does not see Daxton's car")
check(all(c["from"] != "Daxton" for c in cars(anon)), "  ... nor does a device not signed in")
check(post(sam, "/api/handoff", E320).status_code == 200, "Sam sends one")
check([c["from"] for c in cars(sam_tablet)] == ["Sam"], "Sam's tablet sees Sam's car, and only that")
check([c["from"] for c in cars(dax)] == ["Daxton"], "Daxton sees only his own")
check(len(cars(pc)) == handoff.SHOW, f"the PC lists the latest {handoff.SHOW}")

print("dismiss")
sam_car = cars(sam)[0]["id"]
dax_car = cars(dax)[0]["id"]
check(post(dax, f"/api/handoff/{sam_car}/dismiss").status_code == 404,
      "Daxton cannot dismiss Sam's car (404, as if it weren't there)")
check(post(anon, f"/api/handoff/{sam_car}/dismiss").status_code == 404,
      "  ... nor can a device not signed in")
check(post(sam_tablet, f"/api/handoff/{sam_car}/dismiss").status_code == 200,
      "Sam's tablet can dismiss Sam's car")
check(cars(sam) == [] and all(c["id"] != sam_car for c in cars(pc)), "  ... gone everywhere")
check(post(pc, f"/api/handoff/{dax_car}/dismiss").status_code == 200, "the PC dismisses Daxton's")
check(post(pc, f"/api/handoff/{dax_car}/dismiss").status_code == 404, "  ... once")

print("sign-in required")
check(post(pc, "/api/settings", {"require_signin": True}).status_code == 200, "this PC turns it on")
check(anon.get("/api/handoff").status_code == 401, "a phone not signed in cannot list cars")
check(post(anon, "/api/handoff", E320).status_code == 401, "  ... or send one")
check(post(anon, f"/api/handoff/{sam_car}/dismiss").status_code == 401, "  ... or dismiss one")
check(post(sam, "/api/handoff", E320).status_code == 200, "a signed-in mechanic still can")
post(pc, f"/api/admin/users/{sam_id}", {"disabled": True})
check(sam.get("/api/handoff").status_code == 401, "a disabled mechanic's phone is refused")
check(any(c["from"] == "Sam" for c in cars(pc)), "  ... the PC still sees what Sam sent")
post(pc, "/api/settings", {"require_signin": False})

print("expiry, caps and rate")
a = sqlite3.connect(TMP / "auth.db")
a.execute("UPDATE handoffs SET expires=?", (time.time() - 1,))
a.commit()
check(cars(pc) == [] and cars(dax) == [], f"after {handoff.TTL_HOURS} h a car is gone")
post(anon, "/api/handoff", E320)   # a write purges the expired rows
check(a.execute("SELECT COUNT(*) FROM handoffs WHERE expires<?", (time.time(),)).fetchone()[0] == 0,
      "  ... and deleted from auth.db at the next send")
for _ in range(handoff.KEEP_PER_SENDER + 5):
    post(dax, "/api/handoff", E320)
check(a.execute("SELECT COUNT(*) FROM handoffs WHERE user_id=?", (dax_id,)).fetchone()[0]
      == handoff.KEEP_PER_SENDER, f"one sender keeps at most {handoff.KEEP_PER_SENDER}")
codes = [post(dax, "/api/handoff", E320).status_code for _ in range(handoff.MAX_SENDS)]
check(429 in codes, f"more than {handoff.MAX_SENDS} sends in 10 minutes are refused (429)")
lee = sign_in("192.168.1.70", post(pc, "/api/admin/users", {"name": "Lee"}).json()["id"])
check(post(lee, "/api/handoff", E320).status_code == 200,
      "  ... per sender: another mechanic is not limited by it")
a.close()

shutil = __import__("shutil")
shutil.rmtree(TMP, ignore_errors=True)
print("FAILED" if fails else "all passed", f"({fails} failures)" if fails else "")
raise SystemExit(1 if fails else 0)
