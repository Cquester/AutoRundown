"""WIS cross-references open the right page -- FREE, no API calls, ~5 s.

    python test_wis_links.py

Builds test_records.py's small WIS corpus, adds a `wis_links.sqlite` the way
extractor/parse_links.py writes it, and drives the real server in-process
(throwaway AUTORUNDOWN_HOME). Checks the rules in autorundown/wis_links.py:

- a printed number matches stored numbers that add only variant letters
  ("AR42.10-P-0100A" = ...0100aa, ...0100ab), never a sub-document
  ("...0100-01A");
- the variant filed under the chosen car comes first and is marked in scope;
  another model's variant is listed, marked not in scope;
- a page outside the car's scope opens only with an `outside_scope` note;
- without the sidecar, nothing changes (no xrefs).
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_wislinks_"))
os.environ["AUTORUNDOWN_HOME"] = str(TMP / "home")  # before importing autorundown

from fastapi.testclient import TestClient  # noqa: E402

import ingest  # noqa: E402
import test_records  # noqa: E402
from autorundown import index, server, wis_links  # noqa: E402

fails = 0


def check(label, got, want):
    global fails
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + f"{label}: {got!r}" + ("" if ok else f" (want {want!r})"))
    fails += not ok


def main() -> int:
    try:
        cdir = test_records.build_corpus(TMP / "archive")
        idx = TMP / "index.db"
        with open(os.devnull, "w") as quiet:
            out, sys.stdout = sys.stdout, quiet
            try:
                rc = ingest.main(["--archive", str(TMP / "archive"), "--index", str(idx)])
            finally:
                sys.stdout = out
        check("ingest", rc, 0)
        conn = index.connect(idx)
        ids = {v["doc_id"].rsplit("-", 1)[-1]: v["doc_id"] for v in index.vehicles(conn)}
        conn.close()
        c176 = ids["176"]
        app = TestClient(server.create_app(str(idx), TMP / "auth.db"),
                         base_url="http://localhost:8756", client=("127.0.0.1", 50000))

        print("without wis_links.sqlite")
        page = app.get(f"/api/page/{c176}/1002296").json()
        check("no references resolved", page.get("xrefs"), [])
        check("no out-of-scope page is listed, though one opens if linked",
              bool(app.get(f"/api/page/{c176}/1002306").json().get("outside_scope")), True)

        with sqlite3.connect(cdir / wis_links.NAME) as c:
            c.execute("CREATE TABLE docnr(docnr TEXT, dok_nr_id INTEGER, dok_id INTEGER)")
            c.executemany("INSERT INTO docnr VALUES (?,?,?)", [
                ("ar4210p0100aa", 1, 1002297),     # filed under 176 and 246
                ("ar4210p0100ab", 2, 1002304),     # 246 only
                ("ar4210p010001a", 3, 1002306),    # a sub-document: never
            ])
        wis_links._open.clear()

        print("with wis_links.sqlite")
        check("prefix rule: variant letters only",
              wis_links.candidates(cdir, "AR42.10-P-0100A"), [1002297, 1002304])
        check("a number without its letter is not a sub-document",
              wis_links.candidates(cdir, "AR42.10-P-0100"), [1002297, 1002304])
        check("an unknown number matches nothing",
              wis_links.candidates(cdir, "AR99.99-P-9999A"), [])

        x = app.get(f"/api/page/{c176}/1002296").json()["xrefs"]
        check("the page's reference resolves", [r["docnr"] for r in x], ["AR42.10-P-0100A"])
        check("the car's own variant first, then the other model's",
              [(p["page_no"], p["in_scope"]) for p in x[0]["pages"]],
              [("1002297", True), ("1002304", False)])
        check("each variant says what it applies to",
              all("applies" in p for p in x[0]["pages"]), True)

        r = app.get(f"/api/page/{c176}/1002306")      # filed under 201 only
        check("that page opens", r.status_code, 200)
        check("with a note naming where it is filed",
              bool(r.json().get("outside_scope")), True)
        own = app.get(f"/api/page/{c176}/1002297").json()
        check("a page in scope carries no note", own.get("outside_scope"), None)
        check("the original layout follows the same rule (no 500)",
              app.get(f"/api/original/{c176}/1002306").status_code in (200, 404), True)
        check("an unknown page is still 404",
              app.get(f"/api/page/{c176}/9999999").status_code, 404)

        print("wiring diagrams, option codes, oil")
        c201 = ids["201"]
        check("no diagram data: the page says so", page.get("diagram"), False)
        with sqlite3.connect(cdir / wis_links.NAME) as c:
            c.executescript("""
            CREATE TABLE diagram_code(dok_id INTEGER, code TEXT, x INTEGER, y INTEGER);
            CREATE TABLE diagram_name(dok_id INTEGER, code TEXT, name TEXT);
            CREATE TABLE diagram_svgref(dok_id INTEGER, link_type INTEGER, code TEXT, docnr TEXT);
            CREATE TABLE sa_code(vehicle_type INTEGER, code TEXT, name TEXT);
            CREATE TABLE oil(series TEXT, engine TEXT, engine_type INTEGER, litres REAL,
                             unit TEXT, note TEXT);
            INSERT INTO diagram_code VALUES (1002306, 'N3/10', 100, 200), (1002306, 'N3/9', 5, 5);
            INSERT INTO diagram_name VALUES (1002306, 'N3/10', 'ME control unit'),
                                            (1002306, 'N3/9', 'CDI control unit');
            INSERT INTO diagram_svgref VALUES (1002306, 1, 'N3/10', 'ar4210p0100aa');
            INSERT INTO sa_code VALUES (1, '494', 'US version'), (8, '494', 'Bias-ply tyres'),
                                       (8, '14U', '44 standard seats');
            INSERT INTO oil VALUES ('201', '102983', 1, 5.0, 'l', ''),
                                   ('201', '103942', 1, 6.0, 'l', '');
            """)
        wis_links._open.clear()
        check("a diagram page says it has data",
              app.get(f"/api/page/{c201}/1002306").json().get("diagram"), True)
        d = app.get(f"/api/diagram/{c201}/1002306").json()
        check("codes sorted as numbers (N3/9 before N3/10), with names",
              [(c["code"], c["name"]) for c in d["codes"]],
              [("N3/9", "CDI control unit"), ("N3/10", "ME control unit")])
        check("no image frame: no positions, the list still works",
              [c["at"] for c in d["codes"]], [[], []])
        check("a code's linked document (a full number: that variant only)",
              [(p["page_no"], p["in_scope"]) for p in d["codes"][1]["pages"]],
              [("1002297", False)])
        check("a page without data: 404",
              app.get(f"/api/diagram/{c201}/1002297").status_code, 404)

        check("option codes: this car type's meaning; others only if it has none",
              [(r["code"], r["name"], r["other"]) for r in wis_links.sa_codes(cdir, "494, 14u zz", "CAR")],
              [("494", "US version", []), ("14U", "", ["44 standard seats"])])
        sa = app.get(f"/api/wis/sa/{c201}", params={"codes": "494"}).json()
        check("vehicle type unknown: every meaning, none picked",
              [(r["code"], r["name"], r["other"]) for r in sa],
              [("494", "", ["US version", "Bias-ply tyres"])])

        conn = index.connect(idx)
        names = [v["name"] for v in __import__("json").loads(
            conn.execute("SELECT variants FROM vehicles WHERE doc_id=?", (c201,)).fetchone()[0])]
        conn.close()
        v26 = names.index("190 E 2.6")
        check("oil: nothing until the engine is known",
              app.get(f"/api/wis/oil/{c201}").json(), [])
        check("oil: the variant's own engine only",
              [(r["engine"], r["litres"]) for r in app.get(f"/api/wis/oil/{c201}?variant={v26}").json()],
              [("103.942", 6.0)])
        check("oil: variant + a family it doesn't have (car box, other year): nothing",
              app.get(f"/api/wis/oil/{c201}?variant={v26}&engine=102").json(), [])
        check("oil: variant + its own family",
              [r["engine"] for r in app.get(f"/api/wis/oil/{c201}?variant={v26}&engine=103").json()],
              ["103.942"])
        check("oil: the picked engine family only",
              [(r["engine"], r["litres"]) for r in app.get(f"/api/wis/oil/{c201}?engine=102").json()],
              [("102.983", 5.0)])
    finally:
        wis_links._open.clear()
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
