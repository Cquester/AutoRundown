"""Parts diagrams: browse the car's EPC drawings by group -- FREE, ~10 s.

    python tests/test_parts_diagrams.py

Builds the sample WIS corpus and the stand-in parts catalogue
(tools/make_sample_epc.py: invented parts and drawings), and drives the
real server in-process. Checks parts_browse.py and the Parts tab's data
(2026-10-17):

- /api/parts/diagrams lists the car's catalogue as chassis, engines,
  options > group > subgroup, from the car's parts scope only: a 190 E
  2.3-16 (M102) never lists the 190 E 2.6's M103 catalog, the whole W201
  lists both;
- one subgroup filed in two catalogs (markets) is one entry with both pages,
  the one with a drawing first;
- each subgroup opens its page, whose drawings are served;
- Parts tab rows carry their page's drawings.
"""
from __future__ import annotations

from _paths import ROOT
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_partsdia_"))
os.environ["AUTORUNDOWN_HOME"] = str(TMP / "home")  # before importing autorundown

from fastapi.testclient import TestClient  # noqa: E402

from autorundown import index, parts_browse, server  # noqa: E402

fails = 0


def check(label, ok, got=None):
    global fails
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else f" -- got {got!r}"))
    fails += not ok


def run(*cmd):
    env = {**os.environ, "PYTHONUTF8": "1"}
    subprocess.run([sys.executable, *cmd], cwd=ROOT, env=env, check=True, capture_output=True)


def main() -> int:
    print("-- titles --")
    t = parts_browse.parse_title(
        "Parts: model 210 (EPC catalog 15F), group 24 ENGINE SUSPENSION / subgroup 001 RUBBER MOUNTING")
    check("a chassis page", t and (t["section"], t["group"], t["gname"], t["sub"], t["sname"])
          == ("Chassis 210", "24", "ENGINE SUSPENSION", "001", "RUBBER MOUNTING"), t)
    t = parts_browse.parse_title("Parts: engine 102 (EPC catalog 300), group 01 / subgroup 010 CYLINDER HEAD GASKET")
    check("an engine page with no group name", t and (t["section"], t["gname"], t["sname"])
          == ("Engine 102", "", "CYLINDER HEAD GASKET"), t)
    t = parts_browse.parse_title(
        "Parts: option SA 10406 (code 461), model 111, model 112 / subgroup 001 INSTRUMENTS IN ENGLISH")
    check("an option page", t and (t["section"], t["group"], t["gname"]) == ("Options", "SA 10406", "code 461"), t)
    check("a manual page is not a parts page", parts_browse.parse_title("Remove/install oil pan") is None)

    print("-- the endpoint --")
    run("tools/make_sample_corpus.py")
    run("tools/make_sample_epc.py", "--out", str(TMP / "epc"))
    idx = TMP / "index.db"
    run("ingest.py", "--archive", str(ROOT / "sample_corpus"), "--index", str(idx))
    run("ingest.py", "--archive", str(TMP / "epc"), "--index", str(idx))
    conn = index.connect(idx)
    vs = index.vehicles(conn)
    conn.close()

    def car(code):
        return next(v for v in vs if v.get("model_code") == code and v.get("doc_type") != "parts"
                    and not v.get("joined_into") and "-e" not in v["doc_id"].rsplit("-", 1)[-1][:1])

    def variant(v, name):
        return next(i for i, x in enumerate(json.loads(v["variants"])) if x["name"].startswith(name))

    app = TestClient(server.create_app(str(idx), TMP / "auth.db"),
                     base_url="http://localhost:8756", client=("127.0.0.1", 50000))
    w210 = car("210")
    d = app.get(f"/api/parts/diagrams/{w210['doc_id']}",
                params={"variant": variant(w210, "E 320 4MATIC"), "engine": "112"}).json()
    check("chassis, its engine, its options -- in that order",
          [s["section"] for s in d] == ["Chassis 210", "Engine 112", "Options"], [s["section"] for s in d])
    mount = next(sub for s in d for g in s["groups"] for sub in g["subs"] if sub["name"] == "RUBBER MOUNTING")
    check("a subgroup in two catalogs is one entry with both pages, the drawing first",
          [p["page_no"] for p in mount["pages"]] == ["P900001", "P900002"]
          and mount["pages"][0]["drawings"] == 1, mount)
    brakes = next(g for s in d for g in s["groups"] if g["group"] == "42")
    check("a group keeps its name", brakes["name"] == "BRAKES", brakes)

    w201 = car("201")
    d = app.get(f"/api/parts/diagrams/{w201['doc_id']}",
                params={"variant": variant(w201, "190 E 2.3 16"), "engine": "102"}).json()
    check("a 190 E 2.3-16: chassis and M102 only, never the M103",
          [s["section"] for s in d] == ["Chassis 201", "Engine 102"], [s["section"] for s in d])
    d = app.get(f"/api/parts/diagrams/{w201['doc_id']}").json()
    check("the whole W201: both engines, labelled",
          [s["section"] for s in d] == ["Chassis 201", "Engine 102", "Engine 103"], [s["section"] for s in d])

    page = app.get(f"/api/page/{w210['doc_id']}/P900003").json()
    check("a subgroup's page opens from the car, as a parts page with its drawings",
          page.get("doc_type") == "parts" and len(page.get("images", [])) == 2, page.get("images"))
    img = app.get(f"/api/image/{w210['doc_id']}/{page['images'][0]}")
    check("its drawing is served", img.status_code == 200 and img.content[:8] == b"\x89PNG\r\n\x1a\n",
          img.status_code)

    rows = app.get("/api/search", params={"doc_id": w210["doc_id"], "q": "oil filter", "tab": "parts",
                                          "variant": variant(w210, "E 320 4MATIC"), "engine": "112"}).json()
    check("Parts tab rows carry their page's drawings",
          rows and all(r.get("drawings") for r in rows) and rows[0]["page_no"] == "P900006",
          [(r["page_no"], r.get("drawings")) for r in rows])
    check("... and their part lines keep the callout", rows and rows[0]["lines"][0].startswith("010 |"),
          rows[0]["lines"] if rows else None)
    check("an unknown car is 404", app.get("/api/parts/diagrams/nope").status_code == 404)
    print(f"\n{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
