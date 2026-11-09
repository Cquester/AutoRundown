"""Parts suppliers: the parts list's "Find it at" links -- FREE, no network, <2 s.

    python tests/test_suppliers.py

Checks suppliers.py and its two routes on the real FastAPI app, in-process,
with a throwaway AUTORUNDOWN_HOME (the real config is not touched):

  - the built-in list (FCP Euro and ECS Tuning for European makes, RockAuto
    for every car) until the shop saves its own; "Use the built-in list"
    goes back to it;
  - which suppliers a car's make gets ("Mercedes Benz" = "Mercedes-Benz",
    WIS's "Mercedes-Benz Vans" too; a car with no make only the every-car
    ones);
  - what a saved list must be: a name, a web address (http/https only --
    never javascript: or a file) with {pn} exactly once; nothing is
    saved when a row is wrong;
  - only this PC edits it; a phone reads it from /api/me (no secrets in it).
The browser side (the links in the parts list, by the part's car) is in
tests/test_parts_list.py; no request ever goes to a supplier.
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo on sys.path)
import os
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_suppliers_"))
os.environ["AUTORUNDOWN_HOME"] = str(TMP)  # before importing autorundown

from fastapi.testclient import TestClient  # noqa: E402

from autorundown import config, index, server, suppliers  # noqa: E402

fails = 0


def check(cond: bool, what: str) -> None:
    global fails
    print(("  ok   " if cond else "  FAIL ") + what)
    fails += not cond


def names(items):
    return [s["name"] for s in items]


print("the built-in list")
d = suppliers.current(config.Config())
check(names(d) == ["FCP Euro", "ECS Tuning", "RockAuto"], "FCP Euro, ECS Tuning, RockAuto")
check(all(s["url"].count("{pn}") == 1 and s["url"].startswith("https://") for s in d),
      "  ... each an https search address with {pn}")
d[0]["makes"].append("Changed")
check("Changed" not in suppliers.DEFAULTS[0]["makes"], "  ... handed out as a copy")

print("which suppliers a car gets")
fit = lambda make: [s["name"] for s in suppliers.current(config.Config()) if suppliers.fits(s, make)]
check(fit("Mercedes-Benz") == ["FCP Euro", "ECS Tuning", "RockAuto"], "Mercedes-Benz: all three")
check(fit("Mercedes Benz") == ["FCP Euro", "ECS Tuning", "RockAuto"], "LEMON's 'Mercedes Benz' too")
check(fit("Mercedes-Benz Vans") == ["FCP Euro", "ECS Tuning", "RockAuto"], "WIS's 'Mercedes-Benz Vans' too")
check(fit("Volvo") == ["FCP Euro", "RockAuto"], "Volvo: FCP Euro and RockAuto (ECS has no Volvo)")
check(fit("Ford") == ["RockAuto"], "Ford: RockAuto only")
check(fit("") == ["RockAuto"], "no make: only the every-car supplier")

print("a saved list is checked")
ok = suppliers.clean([{"name": "  Local  store ", "url": " https://parts.example.com/s?q={pn}&x=1 ",
                       "makes": "Ford, Mercedes-Benz, ,Ford"}])
check(ok == [{"name": "Local store", "url": "https://parts.example.com/s?q={pn}&x=1",
              "makes": ["Ford", "Mercedes-Benz"]}], "spaces trimmed, makes split and de-duplicated")
check(suppliers.clean([]) == [], "an empty list is allowed (no links)")
for bad, why in [
        ({"name": "", "url": "https://a.example/{pn}"}, "no name"),
        ({"name": "X", "url": "javascript:alert(1)//{pn}"}, "a javascript: address"),
        ({"name": "X", "url": "file:///C:/x/{pn}"}, "a file address"),
        ({"name": "X", "url": "https://a.example/search"}, "no {pn}"),
        ({"name": "X", "url": "https://a.example/{pn}/{pn}"}, "{pn} twice"),
        ({"name": "X", "url": "https://localhost/{pn}"}, "a host with no dot"),
        ({"name": "X" * 41, "url": "https://a.example/{pn}"}, "a name over 40 characters"),
        ({"name": "X", "url": "https://a.example/{pn}", "makes": [f"M{i}" for i in range(41)]}, "41 makes")]:
    try:
        suppliers.clean([bad])
        check(False, f"refused: {why}")
    except ValueError:
        check(True, f"refused: {why}")
try:
    suppliers.clean([{"name": "A", "url": "https://a.example/{pn}"}, {"name": "a", "url": "https://b.example/{pn}"}])
    check(False, "refused: the same name twice")
except ValueError:
    check(True, "refused: the same name twice")
cfg = config.Config(suppliers=[{"name": "", "url": "nope"}])
check(names(suppliers.current(cfg)) == ["FCP Euro", "ECS Tuning", "RockAuto"],
      "a hand-broken config.json falls back to the built-in list")

print("Shop setup and /api/me")
idx = TMP / "index.db"
index.connect(idx).close()
APP = server.create_app(str(idx), TMP / "auth.db")
pc = TestClient(APP, base_url="http://localhost:8756", client=("127.0.0.1", 50000))
phone = TestClient(APP, base_url="http://192.168.1.20:8756", client=("192.168.1.50", 50000))
same = {"Origin": "http://localhost:8756"}

s = pc.get("/api/settings").json()
check(names(s["suppliers"]) == ["FCP Euro", "ECS Tuning", "RockAuto"] and s["suppliers_default"] is True,
      "settings: the built-in list, marked as such")
mine = [{"name": "Local store", "url": "https://parts.example.com/s?q={pn}", "makes": []},
        {"name": "FCP Euro", "url": "https://www.fcpeuro.com/Parts?keywords={pn}", "makes": ["Volvo"]}]
check(pc.post("/api/settings", json={"suppliers": mine}, headers=same).status_code == 200, "this PC saves a list")
s = pc.get("/api/settings").json()
check(s["suppliers"] == mine and s["suppliers_default"] is False, "  ... and it is the list now")
check(pc.get("/api/me").json()["suppliers"] == mine, "  ... /api/me hands it to the page")
r = pc.post("/api/settings", json={"suppliers": mine + [{"name": "Bad", "url": "javascript:x({pn})"}]},
            headers=same)
check(r.status_code == 400 and "Bad" in r.json()["detail"], "a wrong row is refused, by name")
check(config.load().suppliers == mine, "  ... and nothing of that list was saved")
r = phone.post("/api/settings", json={"suppliers": []}, headers={"Origin": "http://192.168.1.20:8756"})
check(r.status_code in (401, 403), "a phone cannot change the list")
check(config.load().suppliers == mine, "  ... it is unchanged")
check(pc.post("/api/settings", json={"require_signin": False}, headers=same).status_code == 200,
      "(sign-in off, so a phone reads /api/me)")
check(phone.get("/api/me").json()["suppliers"] == mine, "a phone gets the list from /api/me")
check(pc.post("/api/settings", json={"suppliers_default": True}, headers=same).status_code == 200,
      "Use the built-in list")
s = pc.get("/api/settings").json()
check(s["suppliers_default"] is True and names(s["suppliers"]) == ["FCP Euro", "ECS Tuning", "RockAuto"]
      and config.load().suppliers is None, "  ... back to FCP Euro, ECS Tuning, RockAuto")

print("all passed" if not fails else f"{fails} FAILED")
raise SystemExit(1 if fails else 0)
