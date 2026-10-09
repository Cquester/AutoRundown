"""Removing manuals from the index -- FREE, no API calls, ~20 s.

    python test_remove_manuals.py

Builds an index with two sample LEMON cars, one folder import and the sample
Mercedes WIS corpus, then checks: the listing groups them the way the tabs
remove them; a LEMON car is removed alone (its pages, chunks and search hits
gone, the other car still found); a WIS entry can't be removed on its own;
"remove all Mercedes WIS" takes every WIS entry and nothing else; the spend
log survives every removal; a phone on the LAN can't list or remove; and
--compact shrinks the file. Uses a throwaway AUTORUNDOWN_HOME.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_remove_"))
os.environ["AUTORUNDOWN_HOME"] = str(TMP / "home")  # before importing autorundown

from fastapi.testclient import TestClient  # noqa: E402

import ingest  # noqa: E402
import make_sample_lemon  # noqa: E402
import remove_manuals  # noqa: E402
from autorundown import server  # noqa: E402

ROOT = Path(__file__).resolve().parent
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


db = TMP / "index.db"
lemon = make_sample_lemon.make(TMP / "lemon")
ingest.main(["--archive", str(lemon), "--index", str(db), "--importer", "lemon",
             "--only", "2015-ford-f-150", "--only", "2008-toyota-camry"])
docs = TMP / "docs" / "shop-notes"
docs.mkdir(parents=True)
(docs / "brakes.txt").write_text("Caliper bracket bolts 95 Nm. Bleed screw 9 Nm.", encoding="utf-8")
ingest.main(["--archive", str(TMP / "docs"), "--index", str(db), "--importer", "folder"])
wis = ROOT / "sample_corpus" / "wis_en"
if not (wis / "manifest.json").is_file():
    subprocess.run([sys.executable, str(ROOT / "make_sample_corpus.py")], check=True, cwd=ROOT)
ingest.main(["--archive", str(wis), "--index", str(db)])
con = sqlite3.connect(db)
con.execute("INSERT INTO usage_log (ts, source, model, question, calls, input_tokens, output_tokens, cost_usd) "
            "VALUES (datetime('now'), 'web', 'claude-haiku-4-5', 'a paid question', 1, 10, 10, 0.01)")
con.commit()
con.close()

APP = server.create_app(str(db))
pc = TestClient(APP, base_url="http://localhost:8756", client=("127.0.0.1", 50000))
phone = TestClient(APP, base_url="http://192.168.1.10:8756", client=("192.168.1.50", 50000))
same = {"Origin": "http://localhost:8756"}


def wait_job() -> dict:
    for _ in range(600):
        s = pc.get("/api/import").json()
        if s["state"] != "running":
            return s
        time.sleep(0.2)
    return s


print("listing")
m = pc.get("/api/import/manuals").json()
lem = {r["label"]: r for r in m["lemon"]}
check(len(m["lemon"]) == 2, f"two LEMON cars: {sorted(lem)}")
check(len(m["folder"]) == 1, f"one folder import: {[r['label'] for r in m['folder']]}")
# every WIS bucket, however many the sample has (the invented fallback
# catalogue gives 17, the real one 400+)
n_wis = q(db, "SELECT COUNT(*) FROM vehicles WHERE source_name LIKE 'mercedes-wis%'")[0][0]
check(m["wis"]["entries"] == n_wis > 0 and m["wis"]["pages"] > 0,
      f"WIS grouped: {m['wis']['entries']} entries of {n_wis}")
check(phone.get("/api/import/manuals").status_code == 403, "a phone can't list")

ford = next(r for r in m["lemon"] if "Ford" in r["label"])
camry = next(r for r in m["lemon"] if "Camry" in r["label"])
wis_one = q(db, "SELECT doc_id FROM vehicles WHERE source_name LIKE 'mercedes-wis%' LIMIT 1")[0][0]
before_hits = pc.get("/api/search", params={"doc_id": camry["doc_id"], "q": "oil"}).json()

print("remove one LEMON car")
body = {"path": "", "mode": "remove", "vehicles": [ford["doc_id"]]}
check(phone.post("/api/import", json=body, headers={"Origin": "http://192.168.1.10:8756"}).status_code == 403,
      "a phone can't remove")
check(pc.post("/api/import", json={**body, "vehicles": [wis_one]}, headers=same).status_code == 400,
      "a single WIS entry is refused")
check(pc.post("/api/import", json={**body, "vehicles": []}, headers=same).status_code == 400, "nothing picked is refused")
check(pc.post("/api/import", json=body, headers=same).status_code == 200, "the PC starts it")
s = wait_job()
check(s["state"] == "done", f"job finished: {s['state']}  " + " | ".join(s.get("log", [])[-3:]))
check(not q(db, "SELECT 1 FROM vehicles WHERE doc_id=?", ford["doc_id"]), "the Ford is gone from vehicles")
check(not q(db, "SELECT 1 FROM pages WHERE doc_id=?", ford["doc_id"])
      and not q(db, "SELECT 1 FROM chunks WHERE doc_id=?", ford["doc_id"]), "its pages and chunks are gone")
check(not q(db, "SELECT 1 FROM chunks_fts WHERE chunks_fts MATCH 'torque' AND rowid NOT IN (SELECT rowid FROM chunks)"),
      "the search index has no leftovers")
check(q(db, "SELECT complete FROM vehicles WHERE doc_id=?", camry["doc_id"]) == [(1,)], "the Camry is still there")
after_hits = pc.get("/api/search", params={"doc_id": camry["doc_id"], "q": "oil"}).json()
check(len(after_hits) == len(before_hits) and len(after_hits) > 0, f"and still found by search ({len(after_hits)} hits)")
check(q(db, "SELECT COUNT(*) FROM usage_log")[0][0] == 1, "the spend log is kept")

print("remove all Mercedes WIS")
wis_n = q(db, "SELECT COUNT(*) FROM vehicles WHERE source_name LIKE 'mercedes-wis%'")[0][0]
check(pc.post("/api/import", json={"path": "", "mode": "remove_wis"}, headers=same).status_code == 200, "started")
s = wait_job()
check(s["state"] == "done", f"job finished: {s['state']}")
check(any(f"{wis_n} manual(s)" in line for line in s["log"]), f"it removed all {wis_n} WIS entries")
check(q(db, "SELECT COUNT(*) FROM vehicles WHERE source_name LIKE 'mercedes-wis%'")[0][0] == 0, "no WIS left")
check(q(db, "SELECT COUNT(*) FROM pages WHERE doc_id NOT IN (SELECT doc_id FROM vehicles)")[0][0] == 0,
      "no orphan pages")
left = {r[0] for r in q(db, "SELECT source_name FROM vehicles")}
check(left == {"lemon", "folder"}, f"LEMON and folder manuals untouched: {sorted(left)}")
check(q(db, "SELECT COUNT(*) FROM usage_log")[0][0] == 1, "the spend log is kept")
check(pc.post("/api/import", json={"path": "", "mode": "remove_wis"}, headers=same).status_code == 400,
      "asking again when there is none is refused")

print("command line")
check(remove_manuals.main(["--index", str(db), "--source", "lemon"]) == 1, "--source is only for WIS")
check(remove_manuals.main(["--index", str(db), "--doc-id", "no-such-car"]) == 1, "an unknown doc_id fails")
size = db.stat().st_size
folder_id = m["folder"][0]["doc_id"]
check(remove_manuals.main(["--index", str(db), "--doc-id", folder_id, "--compact"]) == 0, "remove the folder import, compacted")
check(db.stat().st_size < size, f"the file shrank: {size // 1024} KB -> {db.stat().st_size // 1024} KB")
check([r[0] for r in q(db, "SELECT doc_id FROM vehicles")] == [camry["doc_id"]], "only the Camry is left")

print(f"\n{fails} failure(s)")
raise SystemExit(1 if fails else 0)
