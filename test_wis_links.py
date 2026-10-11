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
    finally:
        wis_links._open.clear()
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
