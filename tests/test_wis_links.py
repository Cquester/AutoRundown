"""WIS cross-references open the right page -- FREE, no API calls, ~5 s.

    python tests/test_wis_links.py

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

from _paths import ROOT  # noqa: F401  (puts the repo on sys.path)
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_wislinks_"))
os.environ["AUTORUNDOWN_HOME"] = str(TMP / "home")  # before importing autorundown

from fastapi.testclient import TestClient  # noqa: E402

import ingest  # noqa: E402
import test_records  # noqa: E402
from autorundown import index, search, server, wis_links  # noqa: E402

fails = 0


def check(label, got, want):
    global fails
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + f"{label}: {got!r}" + ("" if ok else f" (want {want!r})"))
    fails += not ok


def main() -> int:
    try:
        cdir = test_records.build_corpus(TMP / "archive")
        # a damage-code picture menu (DokID 99xxxxxx, filed under no car) and
        # an ordinary unfiled document sharing its word
        with (cdir / "records.jsonl").open("a", encoding="utf-8", newline="\n") as f:
            for r in (test_records.rec(99026283, "Connecting rod, piston", "argt0 ;; 03305 quuxrod"),
                      test_records.rec(1002350, "Notes on quuxrod bolts", "quuxrod bolts text")):
                f.write(json.dumps(r) + "\n")
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

        with closing(sqlite3.connect(cdir / wis_links.NAME)) as c, c:
            c.execute("CREATE TABLE docnr(docnr TEXT, dok_nr_id INTEGER, dok_id INTEGER)")
            c.executemany("INSERT INTO docnr VALUES (?,?,?)", [
                ("ar4210p0100aa", 1, 1002297),     # filed under 176 and 246
                ("ar4210p0100ab", 2, 1002304),     # 246 only
                ("ar4210p010001a", 3, 1002306),    # a sub-document: never
            ])

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

        print("searching by document number (numbers.py)")
        with closing(sqlite3.connect(cdir / wis_links.NAME)) as c, c:
            c.executemany("INSERT INTO docnr VALUES (?,?,?)", [
                ("ra88001760220z", 4, 1002297),    # old job 88-0220 for model 176
                ("ra88002460220z", 5, 1002304),    # ... for model 246
                ("ra88002460221z", 6, 1002306),    # another job: never
            ])
        check("an old job number: every model's document, no other job",
              wis_links.old_job_candidates(cdir, "88-0220"), [1002297, 1002304])

        def numbered(q):
            rows = app.get("/api/search", params={"doc_id": c176, "q": q}).json()
            return [(r["page_no"], r.get("number"), r.get("outside")) for r in rows if r.get("number")]
        check("a typed document number opens its page first, the car's own version first",
              numbered("AR42.10-P-0100A"),
              [("1002297", "AR42.10-P-0100A", False), ("1002304", "AR42.10-P-0100A", True)])
        check("typed in lower case too", numbered("ar42.10-p-0100a")[:1],
              [("1002297", "AR42.10-P-0100A", False)])
        check("an old job number the same way", numbered("88-0220"),
              [("1002297", "88-0220", False), ("1002304", "88-0220", True)])
        rows = app.get("/api/search", params={"doc_id": c176, "q": "88-0220"}).json()
        check("each page once", len({r["page_no"] for r in rows}), len(rows))
        check("an unknown number adds nothing", numbered("88-9999"), [])
        check("only on the manual's tab",
              [r for r in app.get("/api/search", params={"doc_id": c176, "q": "88-0220",
                                                          "tab": "parts"}).json() if r.get("number")], [])

        print("wiring diagrams, oil")
        c201 = ids["201"]
        check("no diagram data: the page says so", page.get("diagram"), False)
        with closing(sqlite3.connect(cdir / wis_links.NAME)) as c, c:
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

        print("labour times")
        check("no labour tables yet: no books, no 500",
              app.get(f"/api/wis/labour/{c201}?variant={names.index('190 E 2.3 16')}").json()["books"], [])
        with closing(sqlite3.connect(cdir / wis_links.NAME)) as c, c:
            c.executescript("""
            CREATE TABLE labour_car(baumuster TEXT, engine TEXT, sparte TEXT, pl TEXT, family TEXT, type TEXT);
            CREATE TABLE labour_book(sparte TEXT, pl TEXT, family TEXT, type TEXT, family_name TEXT, type_name TEXT);
            CREATE TABLE labour_group(kg TEXT, name TEXT);
            CREATE TABLE labour_op(sparte TEXT, kg TEXT, op TEXT, entry TEXT, category TEXT, text TEXT);
            CREATE TABLE labour_time(sparte TEXT, pl TEXT, family TEXT, kg TEXT, op TEXT, typenart TEXT, raw INTEGER);
            CREATE TABLE labour_formula(region TEXT, sparte TEXT, pl TEXT, family TEXT, kg TEXT, category TEXT, expr TEXT);
            -- 190 E 2.3 16: one book; 190 E 2.6: two (a build-date split)
            INSERT INTO labour_car VALUES ('201.034', '102.983', '00', '00', '06', '41'),
                                          ('201.029', '103.942', '00', '00', '06', '51'),
                                          ('201.029', '103.942', '00', '00', '07', '51');
            INSERT INTO labour_book VALUES ('00','00','06','41','201','190 E 2.3-16'),
                                           ('00','00','06','51','201','190 E 2.6'),
                                           ('00','00','07','51','201 (as of 06/91)','190 E 2.6');
            INSERT INTO labour_group VALUES ('15', 'Electrical components, engine'), ('42', 'Brakes');
            INSERT INTO labour_op VALUES ('00','15','5760','5700','G','Voltage regulator replace'),
                                         ('00','42','1350','1300','G','Bleed hydraulic brake system'),
                                         ('00','42','1351','1300','Z','Extra work for: bleeding'),
                                         ('00','15','5770','5700','V','Not for this type');
            -- exact type 41, its group 40, and 00 never overlap per operation
            INSERT INTO labour_time VALUES ('00','00','06','15','5760','41',31),
                                           ('00','00','06','15','5760','51',99),
                                           ('00','00','06','42','1350','40',13),
                                           ('00','00','06','42','1351','00',16),
                                           ('00','00','06','15','5770','41',0);
            INSERT INTO labour_formula VALUES
              ('10','00','00','06','15','G','(0.9883**x*0.3+1.21)*x'),
              ('10','00','00','06','42','G','(0.9883**x*0.3+1.21)*x'),
              ('10','00','00','06','15','V','__import__("os").system("echo pwned")'),
              ('10','00','00','06','16','G','1.2 * (x');
            INSERT INTO labour_op VALUES ('00','16','1000','1000','G','Malformed formula op');
            INSERT INTO labour_time VALUES ('00','00','06','16','1000','00',10);
            """)

        print("manual contents (the Manual page tab before a question)")
        url = f"/api/manual/contents/{c176}"

        def walk(path=()):
            n = app.get(url, params={"path": list(path)}).json()
            pages = [p["page_no"] for p in n["pages"]]
            for c in n["children"]:
                pages += walk((*n["path"], c["name"]))
            return pages
        top = app.get(url).json()
        groups = [c["name"] for c in top["children"]]
        check("WIS documents by function group, named from the labour book",
              "42 Brakes" in groups, True)
        check("documents with no number come last, under Other documents",
              groups[-1], "Other documents")
        check("a group holds its subgroups",
              [c["name"] for c in app.get(url, params={"path": ["42 Brakes"]}).json()["children"]],
              ["42.10"])
        leaf = app.get(url, params={"path": ["42 Brakes", "42.10"]}).json()
        check("a subgroup lists its documents, with what they are",
              [(p["page_no"], p["kind"]) for p in leaf["pages"]], [("1002297", "Repair")])
        every = walk()
        check("every document once, and the counts add up",
              (len(every) == len(set(every)), len(every)), (True, top["count"]))
        check("another model's document is not in this car's contents", "1002304" in every, False)
        check("an unknown section is 404",
              app.get(url, params={"path": ["99 Nothing"]}).status_code, 404)
        # WIS's own names (Gruppen, pulled with the vehicle tables): the car's
        # name wins over another vehicle type's, a subgroup only it names
        # falls back to it, and a group WIS doesn't name keeps the labour book's
        with closing(sqlite3.connect(cdir / wis_links.NAME)) as c, c:
            c.executescript("""
            CREATE TABLE wis_group(vehicle_type INTEGER, fgr INTEGER, fug INTEGER, name TEXT);
            INSERT INTO wis_group VALUES (1, 42, -1, 'Brakes, mechanical, hydraulic'),
                                         (1, 42, 10, 'Disc brake'), (5, 42, 10, 'Truck brake'),
                                         (5, 0, 0, 'General');
            """)
        names2 = wis_links.group_names(cdir)
        check("group names: the car's, else another type's, else the labour book's",
              (names2["42"], names2["42.10"], names2["00.00"], names2["15"]),
              ("Brakes, mechanical, hydraulic", "Disc brake", "General", "Electrical components, engine"))
        sub = app.get(url, params={"path": ["42 Brakes, mechanical, hydraulic"]}).json()
        check("subgroups are named (a new sidecar rebuilds the contents)",
              [c["name"] for c in sub["children"]], ["42.10 Disc brake"])
        # kinds that number their own items after the group digits: a
        # workshop-handbook item sits in its group with no made-up subgroup
        # ("wh0051" is item 0051, not 00.51); a diagnosis manual's digits are
        # a chapter, not a group, so it goes under Other documents
        loose = app.get(url, params={"path": ["Other documents"]}).json()
        loose = [p["page_no"] for c in loose["children"]
                 for p in app.get(url, params={"path": ["Other documents", c["name"]]}).json()["pages"]]
        before = app.get(url).json()["count"]
        with closing(sqlite3.connect(cdir / wis_links.NAME)) as c, c:
            c.executemany("INSERT INTO docnr VALUES (?,?,?)",
                          [("wh0051pkw0000x", 8, int(loose[0])), ("dh0302pf10021x", 9, int(loose[1]))])
        top = app.get(url).json()
        g00 = app.get(url, params={"path": ["00"]}).json()
        check("own-numbered kinds: in their group, no subgroup made of their item number",
              ([p["page_no"] for p in g00["pages"]], [c["name"] for c in g00["children"]]),
              ([loose[0]], []))
        check("a diagnosis manual's chapter number is not a group",
              ("03" in " ".join(c["name"][:2] for c in top["children"]),
               [p["page_no"] for p in app.get(url, params={"path": ["Other documents", "DH documents"]})
                .json()["pages"]]), (False, [loose[1]]))
        check("moving documents between sections keeps the count",
              (len(walk()), top["count"]), (before, before))

        print("damage-code menus: off by default, a Shop setup setting shows them")

        def found(q):
            r = app.get("/api/search", params={"doc_id": c176, "q": q}).json()
            return sorted({x["page_no"] for x in (r["results"] if isinstance(r, dict) else r)})
        check("off: search leaves the menu out, keeps the ordinary unfiled document",
              (app.get("/api/settings").json()["show_damage_codes"], found("quuxrod")),
              (False, ["1002350"]))
        check("off: not in the contents", "99026283" in walk(), False)
        check("the setting saves",
              app.post("/api/settings", json={"show_damage_codes": True}).json(), {"ok": True})
        check("on: search finds it", found("quuxrod"), ["1002350", "99026283"])
        check("on: in the contents", "99026283" in walk(), True)
        app.post("/api/settings", json={"show_damage_codes": False})
        check("off again: gone", (found("quuxrod"), search.show_damage_codes), (["1002350"], False))
        v23, v26 = names.index("190 E 2.3 16"), names.index("190 E 2.6")
        check("labour times need the exact model",
              app.get(f"/api/wis/labour/{c201}").json()["need"], "variant")
        r = app.get(f"/api/wis/labour/{c201}?variant={v23}").json()
        check("one book: chosen without asking", r["book"], "00-00-06-41")
        check("exact type, else its group; formula applied (44 / 19 min as in WIS)",
              [(i["op"], i["minutes"], i["hours"]) for i in r["items"]],
              [("15-5760", 44, 0.7), ("42-1350", 19, 0.3)])
        check("extra work (no formula), raw zeros and a non-arithmetic formula are left out",
              [i["op"] for i in r["items"] if i["op"] in ("42-1351", "15-5770")], [])
        check("search narrows by every word",
              [i["op"] for i in app.get(f"/api/wis/labour/{c201}?variant={v23}&q=bleed brake").json()["items"]],
              ["42-1350"])
        r = app.get(f"/api/wis/labour/{c201}?variant={v26}").json()
        check("two books: both offered, none picked, no times",
              ([b["family"] for b in r["books"]], r["book"], r["items"], r["need"]),
              (["201", "201 (as of 06/91)"], None, [], "book"))
        r = app.get(f"/api/wis/labour/{c201}?variant={v26}&book=00-00-07-51").json()
        check("a chosen book is used", r["book"], "00-00-07-51")
        check("an unknown book is not", app.get(
            f"/api/wis/labour/{c201}?variant={v26}&book=00-00-99-99").json()["book"], None)

        # a search shows the job itself, not every operation naming its words
        def op(o, text):
            return {"group": "", "kg": o[:2], "op": o, "text": text, "minutes": 1, "hours": 0.0}
        book = [op("00-2080", "Oil change service: Engine oil and filter change Perform"),
                op("00-6020", "Lubricant level in manual transmission Correct (with additional oil level checks/oil changes)"),
                op("00-6450", "Lubricant change in manual transmission Perform (with additional oil changes)"),
                op("00-6470", "Fluid change with filter service in automatic transmission Perform (with additional oil changes)"),
                op("02-2538", "Starter wiring harness replace Work Instructions 54/20/10 - I Invoice No. 54 900 12"),
                op("15-7100", "Starter Remove/install"),
                op("15-7170", "Starter Exchange"),
                op("15-7175", "Starter Exchange (with transm. removed)"),
                op("18-3122", "Oil filter housing with heat exchanger Remove/install"),
                op("27-1280", "Selector lever linkage rod and starter lockout switch Check, adjust if necessary"),
                op("27-2028", "Remove/install automatic transmission On veh. with transmission 722.6"),
                op("27-2029", "Remove/install automatic transmission On veh. with transmission 722.9")]
        rank = lambda q: wis_links._rank_labour(book, [q.split()], 200)
        r = rank("starter")
        check("the job first: the plain operations are the best, add-ons and campaigns after",
              ([i["op"] for i in r["items"][:r["best"]]], [i["op"] for i in r["items"][r["best"]:]]),
              (["15-7100", "15-7170"], ["15-7175", "27-1280", "02-2538"]))
        r = rank("oil change")
        check("a word matches the words it starts, not one it is inside of (exchanger)",
              [i["op"] for i in r["items"][:r["best"]]], ["00-2080"])
        check("... and the operations that only mention it come after",
              "18-3122" in [i["op"] for i in r["items"]], False)
        r = rank("transmission fluid change")
        check("an automatic gearbox's job brings the manual one's: the car may have either",
              [i["op"] for i in r["items"][:r["best"]]], ["00-6470", "00-6450"])
        r = rank("transmission removal")
        check("one job per automatic (722.6, 722.9): both; 'removal' names no component",
              [i["op"] for i in r["items"][:r["best"]]], ["27-2028", "27-2029"])
        check("words that name no job: nothing", rank("how to replace")["total"], 0)
        r = app.get(f"/api/wis/labour/{c201}?variant={v23}&q=brake").json()
        check("the route says how many are the job itself", (r["best"], r["total"]), (1, 1))
        try:
            wis_links._formula('__import__("os").system("x")')
            check("a formula that is not arithmetic is refused", False, True)
        except ValueError:
            check("a formula that is not arithmetic is refused", True, True)
        # the server must not hold the sidecar open: a re-extraction's
        # parse_links.py replaces it while the app runs (WinError 5, 2026-10-23)
        side = cdir / wis_links.NAME
        new = side.with_suffix(".tmp")
        shutil.copyfile(side, new)
        try:
            os.replace(new, side)
            check("the sidecar can be replaced while the app runs", True, True)
        except OSError as e:
            check("the sidecar can be replaced while the app runs", str(e), True)
        check("lookups read the replaced sidecar",
              wis_links.candidates(cdir, "AR42.10-P-0100A"), [1002297, 1002304])
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
