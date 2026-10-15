"""Joining a parts catalogue (EPC) to the manual's cars (WIS) -- FREE, ~5 s.

    python tests/test_epc_join.py

Builds the synthetic WIS corpus from test_records.py plus a small EPC corpus
beside it whose dok_ids deliberately equal WIS DokIDs, ingests both, and
checks the join index.link_sources / search.scope_ids make:

- EPC pages get their own page numbers ("P1002306"), so a citation or a page
  lookup can never cross sources -- the core safety property;
- the W201 searches its EPC parts and the parts of its own engines only, and
  an engine choice narrows them; another car never reaches them;
- the EPC W201 is not listed a second time; an EPC series with no WIS car is;
- an EPC drawing is served through the WIS car the UI asks with.
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo on sys.path)
import contextlib
import io
import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_epcjoin_"))
os.environ["AUTORUNDOWN_HOME"] = str(TMP / "home")   # before importing autorundown

from fastapi.testclient import TestClient  # noqa: E402

from autorundown import index, search, server  # noqa: E402
from autorundown.query import AnswerModel, _verify_citations, format_passages  # noqa: E402
import ingest  # noqa: E402
import test_records  # noqa: E402  (its synthetic WIS corpus)

fails = 0


def check(name: str, ok: bool, detail: object = "") -> None:
    global fails
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f" -- {detail}" if detail != "" and not ok else ""))
    fails += not ok


def epc(dok_id, title, lines, series=(), engines=(), images=()):
    role = "ENGINE " if engines else "MODEL "
    codes = list(engines or series)
    return {
        "dok_id": dok_id, "lang": "en", "title": title, "body_format": "sids",
        "body_text": "\n".join([title, "", "Callout | Part number | Name | Fits | Notes", *lines]),
        "model_series": [] if engines else list(series), "baumuster": [],
        "engine_series": list(engines),
        "applicability_notes": [role + c for c in codes],
        "cross_refs": [], "image_refs": [], "figures": [], "image_files": list(images),
        "source_type": "mercedes-epc",
    }


EPC_RECORDS = [
    # same number as the WIS W201 document 1002306 on purpose
    epc(1002306, "Parts: model 201 (EPC catalog 040), group 18 LUBRICATION / subgroup 015 OIL FILTER",
        ["010 | A 102 180 00 09 (A1021800009, 1021800009) | OIL FILTER | qty 1, all models |"],
        series=["201"], images=["epc/B18015000001.000000.png"]),
    epc(2, "Parts: engine 102 (EPC catalog 300), group 01 / subgroup 010 CYLINDER HEAD GASKET",
        ["020 | A 102 016 13 20 (A1020161320, 1020161320) | CYLINDER HEAD GASKET | qty 1, all models |"],
        engines=["102"]),
    epc(3, "Parts: engine 103 (EPC catalog 301), group 01 / subgroup 010 CYLINDER HEAD GASKET",
        ["020 | A 103 016 03 20 (A1030160320, 1030160320) | CYLINDER HEAD GASKET | qty 1, all models |"],
        engines=["103"]),
    # a series WIS does not have: stays listed on its own
    epc(4, "Parts: model 100 (EPC catalog 090), group 42 / subgroup 005 BRAKE PADS",
        ["030 | A 000 420 21 20 (A0004202120, 0004202120) | BRAKE PADS | qty 1, all models |"],
        series=["100"]),
]


def build_epc(root: Path) -> None:
    cdir = root / "epc_en"
    (cdir / "images" / "epc").mkdir(parents=True)
    (cdir / "images" / "epc" / "B18015000001.000000.png").write_bytes(
        bytes.fromhex("89504e470d0a1a0a") + b"fake png body")
    with (cdir / "records.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for r in EPC_RECORDS:
            f.write(json.dumps(r) + "\n")
    # no page_no_prefix / doc_type: the importer's per-source default must apply
    (cdir / "manifest.json").write_text(json.dumps({
        "schema_version": "1.3", "source_type": "mercedes-epc", "source_id": "test",
        "language": {"iso": "en"}, "record_count": len(EPC_RECORDS),
        "records_file": "records.jsonl"}), encoding="utf-8")


def main() -> int:
    try:
        archive = TMP / "archive"
        test_records.build_corpus(archive)
        build_epc(archive)
        db = TMP / "index.db"
        with contextlib.redirect_stdout(io.StringIO()):
            rc = ingest.main(["--archive", str(archive), "--index", str(db), "--importer", "records"])
        check("ingest exit code", rc == 0, rc)
        conn = index.connect(db)
        conn.row_factory = sqlite3.Row

        print("\n-- EPC pages keep their own page numbers --")
        rows = conn.execute("SELECT doc_id, page_no, doc_type FROM pages WHERE doc_id LIKE 'mercedes-epc%'").fetchall()
        check("every EPC page_no is prefixed P", rows and all(r["page_no"].startswith("P") for r in rows),
              [r["page_no"] for r in rows])
        check("EPC pages are doc_type parts", all(r["doc_type"] == "parts" for r in rows))
        wis_pages = {r["page_no"] for r in conn.execute(
            "SELECT page_no FROM pages WHERE doc_id LIKE 'mercedes-wis%'")}
        check("no EPC page number equals a WIS one", not (wis_pages & {r["page_no"] for r in rows}))

        print("\n-- parts have their own full-text index --")
        def count(sql):
            return conn.execute(sql).fetchone()[0]
        n_parts = count("SELECT COUNT(*) FROM chunks WHERE doc_id LIKE 'mercedes-epc%'")
        check("EPC chunks are flagged parts",
              n_parts > 0 and count("SELECT COUNT(*) FROM chunks WHERE parts=1") == n_parts)
        check("chunks_fts holds no EPC chunk (WIS BM25 statistics untouched)",
              count("SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'filter OR gasket OR pads' "
                    "AND rowid IN (SELECT id FROM chunks WHERE parts=1)") == 0)
        check("parts_fts finds the EPC chunks",
              count("SELECT COUNT(*) FROM parts_fts WHERE parts_fts MATCH 'gasket'") == 2)
        check("parts_fts holds no WIS chunk",
              count("SELECT COUNT(*) FROM parts_fts WHERE parts_fts MATCH 'oil OR engine OR brake' "
                    "AND rowid IN (SELECT id FROM chunks WHERE parts=0)") == 0)

        print("\n-- vehicles: joined, hidden, engine joins --")
        vs = {v["doc_id"]: v for v in index.vehicles(conn)}
        w201 = next(v for v in vs.values() if v["model_code"] == "201" and v["source_name"].startswith("mercedes-wis"))
        e201 = next(v for v in vs.values() if v["model_code"] == "201" and v["source_name"] == "mercedes-epc")
        e100 = next(v for v in vs.values() if v["model_code"] == "100")
        e102 = next(v for v in vs.values() if v["model_code"] == "E102")
        e103 = next(v for v in vs.values() if v["model_code"] == "E103")
        check("EPC engine buckets survive import (no variants in that corpus)", bool(e102 and e103))
        check("W201 joins its EPC parts", w201["joined_ids"] == [e201["doc_id"]], w201["joined_ids"])
        check("EPC W201 is marked joined into the WIS W201", e201["joined_into"] == w201["doc_id"])
        check("EPC W100 (no WIS car) is not joined", e100["joined_into"] == "")
        check("W201's engines reach their EPC buckets",
              w201["engine_joins"] == {"102": [e102["doc_id"]], "103": [e103["doc_id"]]}, w201["engine_joins"])
        check("EPC vehicles are doc_type parts and Mercedes-Benz",
              e201["doc_type"] == "parts" and e201["make"] == "Mercedes-Benz", (e201["doc_type"], e201["make"]))

        print("\n-- scope: the car's parts and its own engines' only --")
        variants = json.loads(w201["variants"])
        i26 = next(i for i, x in enumerate(variants) if x["name"] == "190 E 2.6")
        all201 = search.scope_ids(w201)
        check("series scope holds EPC W201 + both engines' parts",
              {e201["doc_id"], e102["doc_id"], e103["doc_id"]} <= set(all201), list(all201))
        check("the car's own doc_id still leads", all201[0] == w201["doc_id"])
        s26 = search.scope_ids(w201, i26)
        check("190 E 2.6 (M103) gets E103 parts, not E102",
              e103["doc_id"] in s26 and e102["doc_id"] not in s26, list(s26))
        s102 = search.scope_ids(w201, engine="102")
        check("engine choice 102 narrows the parts buckets too",
              e102["doc_id"] in s102 and e103["doc_id"] not in s102, list(s102))
        others = [v for v in vs.values() if v["source_name"].startswith("mercedes-wis") and v["model_code"] != "201"]
        leaked = [v["model_code"] for v in others
                  if {e201["doc_id"], e100["doc_id"]} & set(search.scope_ids(v))]
        check("no other car reaches the W201 / W100 parts", not leaked, leaked)

        print("\n-- search and page lookup across the joined scope --")
        hits = search.search(conn, all201, "oil filter", 10)
        check("W201 search finds the EPC oil filter page",
              "P1002306" in [h["page_no"] for h in hits], [h["page_no"] for h in hits])
        p_epc = search.get_page(conn, all201, "P1002306") or {}
        p_wis = search.get_page(conn, all201, "1002306") or {}
        check("P1002306 opens the EPC page", p_epc.get("doc_id") == e201["doc_id"], p_epc.get("doc_id"))
        check("1002306 still opens the WIS page", p_wis.get("doc_id") == w201["doc_id"], p_wis.get("doc_id"))
        alone = search.search(conn, search.scope_ids(e100), "brake pads", 10)
        check("an EPC-only car (W100) searches its own parts",
              [h["page_no"] for h in alone] == ["P4"], [h["page_no"] for h in alone])

        print("\n-- citations and the answer model's view --")
        passages = [dict(h) for h in hits if h["page_no"] == "P1002306"]
        a = AnswerModel.model_validate({
            "found": True, "answer": "Oil filter A 102 180 00 09.",
            "specs": [{"name": "Oil filter", "value": "A 102 180 00 09", "applicability": "all models",
                       "source_pages": ["P1002306"]}],
            "source_pages": ["P1002306"]})
        _verify_citations(a, passages)
        check("a cited P page survives", a.found and a.specs and a.specs[0].source_pages == ["P1002306"], a)
        a = AnswerModel.model_validate({
            "found": True, "answer": "Oil filter A 102 180 00 09.",
            "specs": [{"name": "Oil filter", "value": "A 102 180 00 09", "applicability": "all models",
                       "source_pages": ["1002306"]}],
            "source_pages": ["1002306"]})
        _verify_citations(a, passages)
        check("the unprefixed number (the WIS page, not retrieved) is withheld", not a.found, a)
        check("passages say which pages are a parts catalogue",
              "[page P1002306] (parts catalogue)" in format_passages(
                  [{**p, "body": p.get("body") or ""} for p in passages]))
        conn.close()

        print("\n-- server: listing and drawings --")
        app = server.create_app(str(db), TMP / "auth.db")
        c = TestClient(app, base_url="http://localhost:8756", client=("127.0.0.1", 50000))
        listed = {v["doc_id"] for v in c.get("/api/vehicles").json()}
        check("/api/vehicles leaves out the joined EPC W201", e201["doc_id"] not in listed)
        check("/api/vehicles keeps the WIS W201 and the EPC-only W100",
              {w201["doc_id"], e100["doc_id"]} <= listed)
        r = c.get(f"/api/image/{w201['doc_id']}/epc/B18015000001.000000.png")
        check("an EPC drawing is served through the WIS car", r.status_code == 200, r.status_code)
        r = c.get(f"/api/image/{w201['doc_id']}/epc/../../wis_en/records.jsonl")
        check("no escape from the joined images folder", r.status_code == 404, r.status_code)
        r = c.get(f"/api/page/{w201['doc_id']}/P1002306")
        check("/api/page opens the EPC page through the WIS car",
              r.status_code == 200 and r.json().get("doc_id") == e201["doc_id"], r.status_code)

        print("\n-- Shop setup: EPC is its own group, extracted and replaced whole --")
        from autorundown import importer_jobs
        have = importer_jobs.manuals(db)
        check("manuals() counts EPC as one group", have["epc"]["entries"] == 4, have["epc"])
        check("EPC cars are not listed one by one in the Folder tab",
              not [r for r in have["folder"] if r["source"].startswith("mercedes-epc")])
        jobs, ran = importer_jobs.ImportJobs(), []
        jobs._run = lambda cmd: ran.append(cmd) or 0          # nothing is executed
        real_find = importer_jobs.find_extractor
        importer_jobs.find_extractor = lambda: ROOT / "extractor"
        try:
            jobs.start(TMP / "out" / "epc_extract", str(db), "vm_epc", vm="Mercedes_Test")
            for _ in range(100):
                if jobs.status()["state"] != "running":
                    break
                import time; time.sleep(0.05)
        finally:
            importer_jobs.find_extractor = real_find
        names = [Path(c[2]).name for c in ran]
        check("vm_epc runs pull, then removes the old EPC, then ingests",
              names == ["vm_pipeline.py", "remove_manuals.py", "ingest.py"], names)
        check("... the pull is the epc step for that VM",
              ran and ran[0][ran[0].index("--steps") + 1] == "epc"
              and ran[0][ran[0].index("--vm") + 1] == "Mercedes_Test", ran[:1])
        check("... the removal is the whole EPC source",
              len(ran) > 1 and ran[1][-2:] == ["--source", "mercedes-epc"], ran[1:2])
        check("... and the ingest reads epc_extract as records",
              len(ran) > 2 and ran[2][ran[2].index("--archive") + 1] == str(TMP / "out" / "epc_extract")
              and ran[2][-2:] == ["--importer", "records"], ran[2:])

        check("this repo's extractor can pull EPC",
              importer_jobs.epc_problem(ROOT / "extractor") == "",
              importer_jobs.epc_problem(ROOT / "extractor"))
        old_ext = TMP / "old_extractor"
        old_ext.mkdir()
        (old_ext / "vm_pipeline.py").write_text('STEPS = ("docs", "build")\n', encoding="utf-8")
        check("an extractor from before EPC is refused with a reason",
              "older than the parts-catalogue" in importer_jobs.epc_problem(old_ext))

        print("\n-- removing the EPC manuals keeps both full-text indexes consistent --")
        import remove_manuals
        with contextlib.redirect_stdout(io.StringIO()):
            rc_one = remove_manuals.main(["--index", str(db), "--doc-id", e201["doc_id"]])
        check("one EPC car cannot be removed on its own", rc_one == 1, rc_one)
        with contextlib.redirect_stdout(io.StringIO()):
            rc_all = remove_manuals.main(["--index", str(db), "--source", "mercedes-epc"])
        check("--source mercedes-epc removes it whole", rc_all == 0, rc_all)
        conn = index.connect(db)
        check("no EPC vehicle left, WIS untouched",
              conn.execute("SELECT COUNT(*) FROM vehicles WHERE source_name='mercedes-epc'").fetchone()[0] == 0
              and conn.execute("SELECT COUNT(*) FROM vehicles WHERE source_name LIKE 'mercedes-wis%'").fetchone()[0] > 0)
        for t in ("chunks_fts", "parts_fts"):
            try:
                conn.execute(f"INSERT INTO {t}({t}) VALUES ('integrity-check')")
                ok = True
            except sqlite3.DatabaseError as e:
                ok = str(e)
            check(f"{t} integrity-check after removal", ok is True, ok)
        check("parts_fts is empty again",
              conn.execute("SELECT COUNT(*) FROM parts_fts WHERE parts_fts MATCH 'gasket OR filter'").fetchone()[0] == 0)
        conn.close()

        print("\n-- an index from before parts_fts migrates --")
        old = TMP / "old.db"
        o = sqlite3.connect(old)
        o.executescript("""
            CREATE TABLE chunks(id INTEGER PRIMARY KEY, page_uid TEXT NOT NULL, doc_id TEXT NOT NULL,
                                ord INTEGER, title TEXT, section_path TEXT, body TEXT);
            CREATE TABLE pages(page_uid TEXT PRIMARY KEY, doc_id TEXT NOT NULL, page_no TEXT NOT NULL,
                               file_path TEXT, title TEXT, section_path TEXT, system TEXT, doc_type TEXT,
                               images TEXT, links TEXT, text TEXT, text_hash TEXT, canonical_uid TEXT,
                               applicability TEXT DEFAULT 'this_vehicle');
            CREATE VIRTUAL TABLE chunks_fts USING fts5(title, section_path, body,
                content='chunks', content_rowid='id', tokenize='porter unicode61');
            CREATE TRIGGER chunks_ai AFTER INSERT ON chunks BEGIN
                INSERT INTO chunks_fts(rowid, title, section_path, body)
                VALUES (new.id, new.title, new.section_path, new.body); END;
            CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
                INSERT INTO chunks_fts(chunks_fts, rowid, title, section_path, body)
                VALUES ('delete', old.id, old.title, old.section_path, old.body); END;
            INSERT INTO pages(page_uid, doc_id, page_no, doc_type) VALUES
                ('w:1', 'wis', '1', 'manual'), ('e:P1', 'epc', 'P1', 'parts');
            INSERT INTO chunks(page_uid, doc_id, ord, title, body) VALUES
                ('w:1', 'wis', 0, 'Oil filter change', 'oil filter torque 25 Nm'),
                ('e:P1', 'epc', 0, 'Parts', 'A 102 180 00 09 OIL FILTER');
        """)
        o.commit()
        o.close()
        m = index.connect(old)
        hits = lambda t: m.execute(f"SELECT COUNT(*) FROM {t} WHERE {t} MATCH 'filter'").fetchone()[0]
        check("old index: the parts chunk moved out of chunks_fts", hits("chunks_fts") == 1, hits("chunks_fts"))
        check("old index: ... and into parts_fts", hits("parts_fts") == 1, hits("parts_fts"))
        for t in ("chunks_fts", "parts_fts"):
            m.execute(f"INSERT INTO {t}({t}) VALUES ('integrity-check')")
        m.close()
        m = index.connect(old)            # a second start changes nothing
        check("migration runs once", hits("chunks_fts") == 1 and hits("parts_fts") == 1)
        m.close()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
