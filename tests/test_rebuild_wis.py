"""Rebuilding the Mercedes WIS part keeps everything else -- FREE, ~10 s.

    python tests/test_rebuild_wis.py

rebuild_wis.py used to restore index.before-wis.db and re-ingest WIS; that
backup predates the EPC parts catalogue and every manual imported after it,
so a WIS-only rebuild silently dropped them (2026-10-16). It now removes WIS
in place. This builds the situation that lost data -- a pre-WIS backup with
one LEMON car, then WIS, EPC, a second LEMON car, a folder import and paid
questions added after it -- rebuilds WIS from a changed corpus (from the
command line and as Shop setup's import job) and checks: EPC, both LEMON
cars, the folder import and the spend log are untouched and still found;
WIS matches the new corpus; both full-text indexes pass FTS5's integrity
check; the backup file is not touched; a non-WIS corpus is refused before
anything is removed. Uses a throwaway AUTORUNDOWN_HOME; never the real index.
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
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_rebuildwis_"))
os.environ["AUTORUNDOWN_HOME"] = str(TMP / "home")  # before importing autorundown

from fastapi.testclient import TestClient  # noqa: E402

import ingest  # noqa: E402
import make_sample_lemon  # noqa: E402
import rebuild_wis  # noqa: E402
import test_epc_join  # noqa: E402  (its small EPC corpus)
import test_records  # noqa: E402  (its synthetic WIS corpus)
from autorundown import importer_jobs, server  # noqa: E402

os.environ["AUTORUNDOWN_HOME"] = str(TMP / "home")  # test_epc_join set its own

fails = 0


def check(cond: bool, what: str) -> None:
    global fails
    print(("  ok   " if cond else "  FAIL ") + what)
    fails += not cond


def q(db: Path, sql: str, *a):
    con = sqlite3.connect(db)
    try:
        return con.execute(sql, a).fetchall()
    finally:
        con.close()


def quiet(fn, *a):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a)


def others(db: Path) -> dict:
    """Every non-WIS vehicle with its pages and chunks, as the user would miss them."""
    return {d: (s, q(db, "SELECT COUNT(*) FROM pages WHERE doc_id=?", d)[0][0],
                q(db, "SELECT COUNT(*) FROM chunks WHERE doc_id=?", d)[0][0])
            for d, s in q(db, "SELECT doc_id, source_name FROM vehicles "
                              "WHERE source_name NOT LIKE 'mercedes-wis%'")}


def fts_ok(db: Path) -> bool:
    con = sqlite3.connect(db)
    try:
        for t in ("chunks_fts", "parts_fts"):
            con.execute(f"INSERT INTO {t}({t}) VALUES('integrity-check')")
        return True
    except sqlite3.DatabaseError as exc:
        print(f"       {exc}")
        return False
    finally:
        con.close()


def wis_corpus(root: Path, extra: dict | None = None, drop: int | None = None) -> Path:
    cdir = test_records.build_corpus(root)
    lines = (cdir / "records.jsonl").read_text(encoding="utf-8").splitlines()
    if drop is not None:
        lines = [ln for ln in lines if json.loads(ln)["dok_id"] != drop]
    if extra:
        lines.append(json.dumps(extra))
    (cdir / "records.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    man = json.loads((cdir / "manifest.json").read_text(encoding="utf-8"))
    man["record_count"] = len(lines)
    (cdir / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    return cdir


def wis_pages(db: Path) -> set[str]:
    return {r[0] for r in q(db, "SELECT page_no FROM pages WHERE doc_id LIKE 'mercedes-wis%'")}


# -- the index as it really grew -------------------------------------------
db = TMP / "index.db"
lemon = make_sample_lemon.make(TMP / "lemon")
quiet(ingest.main, ["--archive", str(lemon), "--index", str(db), "--importer", "lemon",
                    "--only", "2015-ford-f-150"])
backup = db.with_name("index.before-wis.db")
shutil.copyfile(db, backup)                      # what the first WIS import left behind
backup_bytes = backup.read_bytes()

wis1 = wis_corpus(TMP / "wis1")
quiet(ingest.main, ["--archive", str(wis1.parent), "--index", str(db), "--importer", "records"])
test_epc_join.build_epc(TMP / "epc")
quiet(ingest.main, ["--archive", str(TMP / "epc"), "--index", str(db), "--importer", "records"])
quiet(ingest.main, ["--archive", str(lemon), "--index", str(db), "--importer", "lemon",
                    "--only", "2008-toyota-camry"])
docs = TMP / "docs" / "shop-notes"
docs.mkdir(parents=True)
(docs / "brakes.txt").write_text("Caliper bracket bolts 95 Nm. Zzshopnote bleed screw 9 Nm.", encoding="utf-8")
quiet(ingest.main, ["--archive", str(TMP / "docs"), "--index", str(db), "--importer", "folder"])
con = sqlite3.connect(db)
for i in range(3):
    con.execute("INSERT INTO usage_log (ts, source, model, question, calls, input_tokens, "
                "output_tokens, cost_usd) VALUES (datetime('now'), 'web', 'claude-haiku-4-5', ?, 1, 10, 10, 0.01)",
                (f"paid question {i}",))
con.commit()
con.close()

before = others(db)
sources = {s for s, _, _ in before.values()}
check({"lemon", "folder", "mercedes-epc"} <= sources, f"other sources in the index: {sorted(sources)}")
check(len([1 for s, _, _ in before.values() if s == "lemon"]) == 2, "two LEMON cars")
usage = q(db, "SELECT ts, question FROM usage_log ORDER BY id")
old_wis = wis_pages(db)
dropped = test_records.RECORDS[-1]["dok_id"]
check(str(dropped) in old_wis, f"WIS document {dropped} is indexed before the rebuild")

# -- refused: a corpus that is not WIS --------------------------------------
print("a non-WIS corpus is refused before anything is removed")
rc = quiet(rebuild_wis.main, ["--archive", str(TMP / "epc"), "--index", str(db)])
check(rc == 1, f"EPC corpus refused (rc {rc})")
check(wis_pages(db) == old_wis and others(db) == before, "nothing removed")
rc = quiet(rebuild_wis.main, ["--archive", str(TMP / "nowhere"), "--index", str(db)])
check(rc == 1 and wis_pages(db) == old_wis, f"a missing folder is refused (rc {rc})")

# -- rebuild from the command line with a changed corpus --------------------
print("rebuild WIS from the command line")
new_doc = test_records.rec(1009999, "Zzrebuilt note for model 201",
                           "Zzrebuilt: a document only the new corpus has.",
                           series=["201"], baumuster=["201.034"])
wis2 = wis_corpus(TMP / "wis2", extra=new_doc, drop=dropped)
rc = quiet(rebuild_wis.main, ["--archive", str(wis2.parent), "--index", str(db)])
check(rc == 0, f"rebuild exit code {rc}")
after = others(db)
check(after == before, "EPC, both LEMON cars and the folder import are unchanged"
      + ("" if after == before else f": lost {sorted(set(before) - set(after))}"))
check(q(db, "SELECT ts, question FROM usage_log ORDER BY id") == usage, "the spend log is unchanged")
now_wis = wis_pages(db)
check("1009999" in now_wis, "WIS has the new corpus's document")
check(str(dropped) not in now_wis, f"and not the document the new corpus dropped ({dropped})")
check(now_wis == (old_wis - {str(dropped)}) | {"1009999"}, "WIS pages are exactly the new corpus's")
check(fts_ok(db), "chunks_fts and parts_fts pass FTS5's integrity check")
check(q(db, "SELECT COUNT(*) FROM pages WHERE doc_id NOT IN (SELECT doc_id FROM vehicles)")[0][0] == 0,
      "no orphan pages")
check(backup.read_bytes() == backup_bytes, "index.before-wis.db is not touched")

APP = server.create_app(str(db))
pc = TestClient(APP, base_url="http://localhost:8756", client=("127.0.0.1", 50000))
same = {"Origin": "http://localhost:8756"}
folder_id = next(d for d, (s, _, _) in before.items() if s == "folder")
epc_id = next(d for d, (s, _, _) in before.items() if s == "mercedes-epc")
check(len(pc.get("/api/search", params={"doc_id": folder_id, "q": "zzshopnote"}).json()) == 1,
      "the folder import is still found by search")
wis_car = q(db, "SELECT doc_id FROM vehicles WHERE source_name LIKE 'mercedes-wis%' AND model_code='201' LIMIT 1")
if wis_car:
    hits = pc.get("/api/search", params={"doc_id": wis_car[0][0], "q": "zzrebuilt"}).json()
    check(len(hits) > 0, f"the new WIS document is found by search ({len(hits)} hits)")
    parts = pc.get("/api/search", params={"doc_id": wis_car[0][0], "q": "oil filter",
                                              "tab": "parts"}).json()
    check(parts and all(str(h.get("page_no", "")).startswith("P") for h in parts),
          "EPC parts are still joined into the WIS car")
check(q(db, "SELECT COUNT(*) FROM chunks WHERE doc_id=?", epc_id)[0][0] > 0, "EPC chunks are there")

# -- Shop setup: detection and the import job -------------------------------
print("Shop setup")
det = importer_jobs.describe(wis2.parent, db)
check(det.get("rebuildable") is True, "a WIS corpus is rebuildable when WIS is in the index")
det = importer_jobs.describe(TMP / "epc", db)
check(not det.get("rebuildable"), "an EPC corpus is not offered a WIS rebuild")
r = pc.post("/api/import", json={"path": str(wis1.parent), "mode": "rebuild_wis"}, headers=same)
check(r.status_code == 200, f"the job starts ({r.status_code})")
for _ in range(600):
    s = pc.get("/api/import").json()
    if s["state"] != "running":
        break
    time.sleep(0.2)
check(s["state"] == "done", f"job finished: {s['state']}  " + " | ".join(s.get("log", [])[-3:]))
check(others(db) == before, "other manuals unchanged after the job")
check(q(db, "SELECT ts, question FROM usage_log ORDER BY id") == usage, "the spend log is unchanged")
check(wis_pages(db) == old_wis, "WIS is back to the first corpus")
check(fts_ok(db), "full-text indexes consistent")
check(backup.read_bytes() == backup_bytes, "index.before-wis.db is not touched")

print(f"\n{fails} failure(s)")
raise SystemExit(1 if fails else 0)
