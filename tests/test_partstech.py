"""PartsTech prices in the parts list -- FREE, no network beyond this PC, ~2 s.

    python tests/test_partstech.py

Runs autorundown/partstech.py and its routes on the real FastAPI app,
in-process, against tools/fake_partstech.py on a local port (shaped like
PartsTech's published OpenAPI spec), with a throwaway AUTORUNDOWN_HOME:

  - sign-in with partner + user ID and key, the token reused, a dropped
    token signed in again once;
  - only stores PartsTech has approved are asked; offers come back
    cheapest first, with core charges and stock;
  - one store failing doesn't stop the others; every store refusing (429)
    is said in words; a number nobody lists is "no offers";
  - odd text is never sent as a part number;
  - nothing is cached: asking twice asks PartsTech twice;
  - Shop setup: keys saved by this PC only, a key left empty keeps the
    saved one, only the last 4 shown, never in /api/me; "Remove the keys"
    turns prices off; Test the connection lists the approved stores;
  - /api/prices with no keys says prices are off and asks nothing.
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo and tools/ on sys.path)
import os
import tempfile
import threading
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_partstech_"))
os.environ["AUTORUNDOWN_HOME"] = str(TMP)  # before importing autorundown

import fake_partstech  # noqa: E402

fake_partstech.Handler.log_message = lambda *a: None   # quiet in a test run
FAKE = fake_partstech.serve(0)
threading.Thread(target=FAKE.serve_forever, daemon=True).start()
os.environ["PARTSTECH_BASE_URL"] = f"http://127.0.0.1:{FAKE.server_address[1]}/partstech"

from fastapi.testclient import TestClient  # noqa: E402

from autorundown import config, index, partstech, server  # noqa: E402

fails = 0


def check(cond: bool, what: str) -> None:
    global fails
    print(("  ok   " if cond else "  FAIL ") + what)
    fails += not cond


def asked(path: str) -> int:
    return sum(r["path"] == path for r in fake_partstech.REQUESTS)


KEYS = {"partner_id": "p-id", "partner_key": "p-key-abcdefgh-1234", "user_id": "shop-7", "user_key": "u-key-abcdefgh-5678"}

print("the client")
check(partstech.creds(config.Config()) is None, "no keys: not set up")
check(partstech.creds(config.Config(partstech={**KEYS, "user_key": " "})) is None, "a blank key: not set up")
c = partstech.creds(config.Config(partstech=KEYS))
check(c == KEYS, "four values: set up")

st = partstech.stores(c)
check([s["id"] for s in st] == [101, 102, 103], "approved stores only, by the shop's priority (Pending left out)")
check(asked("/oauth/access") == 1, "  ... signed in once")

p = partstech.prices(c, "0014208120")
prices = [o["price"] for o in p["offers"]]
check(p["stores"] == 3 and len(p["offers"]) == 4 and prices == sorted(prices), "4 offers from 3 stores, cheapest first")
check(not p["problems"], "  ... no store failed")
check(all(o["name"].startswith("[fake PartsTech]") for o in p["offers"]), "  ... the fake's parts (never a real price)")
check(not any(r["body"] and r["body"].get("storeId") == 104 for r in fake_partstech.REQUESTS),
      "  ... the pending store was never asked")
check(asked("/oauth/access") == 1, "  ... the token reused")
oos = [o for o in p["offers"] if o["brand"] == "FAKE Aftermarket"]
check(len(oos) == 1 and oos[0]["in_stock"] is False and oos[0]["status"] == "Out of stock",
      "  ... stock said in words (out of stock kept, marked)")

core = partstech.prices(c, "0014208125")
check(all(o["core"] == 5.0 for o in core["offers"]), "a core charge comes with the price")

none = partstech.prices(c, "1234560000")
check(none["offers"] == [] and not none["problems"], "a number no store lists: no offers, no problem")

one = partstech.prices(c, "1111111111")
check(len(one["problems"]) == 1 and "NAPA" in one["problems"][0] and len({o["store_id"] for o in one["offers"]}) == 2,
      "one store failing (500): said, and the other two still answer")

busy = partstech.prices(c, "2222222222")
check(not busy["offers"] and len(busy["problems"]) == 3 and "too many requests" in busy["problems"][0],
      "every store at its limit (429): said in words")

for bad in ["", "A 001; DROP", "x" * 41, "0014<script>"]:
    try:
        partstech.clean_part_number(bad)
        check(False, f"refused as a part number: {bad[:12]!r}")
    except ValueError:
        check(True, f"refused as a part number: {bad[:12]!r}")
check(partstech.clean_part_number(" A 001 420 81 20 ") == "A0014208120", "spaces taken out")

n = asked("/catalog/quote")
partstech.prices(c, "0014208120")
check(asked("/catalog/quote") == n + 3, "asking again asks PartsTech again (nothing cached)")

# a token PartsTech dropped early: one more sign-in, then the request again
with partstech._TOKEN_LOCK:
    for k in partstech._TOKENS:
        partstech._TOKENS[k] = ("dropped", partstech._TOKENS[k][1])
before = asked("/oauth/access")
check(len(partstech.stores(c)) == 3 and asked("/oauth/access") == before + 1, "a dropped token: signed in again once")

before = asked("/oauth/access")
try:
    partstech.stores(partstech.creds(config.Config(partstech={**KEYS, "user_key": "bad"})))
    check(False, "wrong keys refused")
except partstech.Unavailable as exc:
    check("refused the keys" in str(exc), "wrong keys: said in words")
check(asked("/oauth/access") == before + 1, "  ... after one sign-in (a refused sign-in is not tried twice)")
check(not any("p-key-abcdefgh-1234" in repr(r) or "u-key-abcdefgh-5678" in repr(r) for r in fake_partstech.REQUESTS),
      "the fake kept no key (what a test log would show)")

print("the routes")
idx = TMP / "index.db"
index.connect(idx).close()
APP = server.create_app(str(idx), TMP / "auth.db")
pc = TestClient(APP, base_url="http://localhost:8756", client=("127.0.0.1", 50000))
phone = TestClient(APP, base_url="http://192.168.1.20:8756", client=("192.168.1.50", 50000))
same = {"Origin": "http://localhost:8756"}
ph = {"Origin": "http://192.168.1.20:8756"}

n = len(fake_partstech.REQUESTS)
r = pc.get("/api/prices", params={"pn": "0014208120"}).json()
check(r == {"on": False, "offers": []} and len(fake_partstech.REQUESTS) == n, "no keys: prices off, PartsTech not asked")
check(pc.get("/api/me").json()["prices_on"] is False, "/api/me: prices off")
check(pc.get("/api/settings").json()["partstech"]["configured"] is False, "settings: not set up")

check(pc.post("/api/settings", json={"require_signin": False}, headers=same).status_code == 200,
      "(sign-in off, so the phone asks)")
r = phone.post("/api/settings", json={"partstech": KEYS}, headers=ph)
check(r.status_code in (401, 403) and not config.load().partstech, "a phone cannot save keys")

check(pc.post("/api/settings", json={"partstech": KEYS}, headers=same).status_code == 200, "this PC saves the keys")
s = pc.get("/api/settings").json()["partstech"]
check(s["configured"] and s["partner_id"] == "p-id" and s["user_id"] == "shop-7"
      and s["partner_key_last4"] == "1234" and s["user_key_last4"] == "5678", "  ... settings show the IDs and the keys' last 4")
check("p-key-abcdefgh-1234" not in pc.get("/api/settings").text and "u-key-abcdefgh-5678" not in pc.get("/api/settings").text,
      "  ... never the keys")
me = phone.get("/api/me")
check(me.json()["prices_on"] is True and "p-key" not in me.text and "u-key" not in me.text and "p-id" not in me.text,
      "/api/me: prices on, and no ID or key")

check(pc.post("/api/settings", json={"partstech": {"user_id": "shop-8", "user_key": ""}}, headers=same).status_code == 200,
      "a new user ID with the key field empty")
check(config.load().partstech == {**KEYS, "user_id": "shop-8"}, "  ... the saved key kept")
for bad, why in [({"api_secret": "x"}, "an unknown field"), ({"user_key": "two words"}, "a key with a space"),
                 ({"user_key": "k" * 201}, "a key over 200 characters")]:
    r = pc.post("/api/settings", json={"partstech": bad}, headers=same)
    check(r.status_code == 400, f"refused: {why}")
check(config.load().partstech == {**KEYS, "user_id": "shop-8"}, "  ... nothing of those saved")

r = phone.get("/api/prices", params={"pn": "A 001 420 81 20"})
d = r.json()
check(r.status_code == 200 and d["on"] and len(d["offers"]) == 4 and d["part_number"] == "A0014208120",
      "a phone asks /api/prices: 4 offers")
check(phone.get("/api/prices", params={"pn": "0014<b>"}).status_code == 400, "odd text: 400, nothing sent")
check(phone.get("/api/settings/partstech/test").status_code in (401, 403), "a phone cannot run Test the connection")
t = pc.get("/api/settings/partstech/test")
check(t.status_code == 200 and [s["name"] for s in t.json()["stores"]] == ["Worldpac Hartford", "NAPA Main St", "O'Reilly Route 9"],
      "Test the connection: the three approved stores")

check(pc.post("/api/settings", json={"partstech": {"user_key": "bad"}}, headers=same).status_code == 200, "a wrong key saved")
r = phone.get("/api/prices", params={"pn": "0014208120"})
check(r.status_code == 502 and "refused the keys" in r.json()["detail"], "  ... /api/prices: 502, said in words")
t = pc.get("/api/settings/partstech/test")
check(t.status_code == 400 and "refused the keys" in t.json()["detail"], "  ... Test the connection says so")

check(pc.post("/api/settings", json={"partstech_clear": True}, headers=same).status_code == 200, "Remove the keys")
check(config.load().partstech == {} and pc.get("/api/me").json()["prices_on"] is False, "  ... prices off, no keys left")

FAKE.shutdown()
print("all passed" if not fails else f"{fails} FAILED")
raise SystemExit(1 if fails else 0)
