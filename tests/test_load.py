"""Many mechanics at once (agent review, 2026-10-28).

- Slow AI answers no longer take the threads every other route needs: Ask
  runs on its own few threads (server.ASK_THREADS), and past a short queue
  (server.ASK_QUEUE) answers 503 "try again" at once.
- The vehicle list is read once per change of the table (index.vehicles).
- Labour times read the labour book once per sidecar file (wis_links).

python tests/test_load.py   (~10 s, sample data, no network, no API)
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo on sys.path)
import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ["AUTORUNDOWN_HOME"] = tempfile.mkdtemp(prefix="ar_load_")  # before importing autorundown

from fastapi.testclient import TestClient  # noqa: E402

import ingest  # noqa: E402
from autorundown import index, query, server, wis_links  # noqa: E402
from tools import make_sample_lemon  # noqa: E402

fails = 0


def check(what: str, cond: bool, detail="") -> None:
    global fails
    print(("ok   " if cond else "FAIL ") + what + ("" if cond else f"  -- {detail}"))
    fails += not cond


TMP = Path(tempfile.mkdtemp(prefix="ar_load_idx_"))
make_sample_lemon.make(TMP / "lemon")
with open(os.devnull, "w") as quiet:
    out, sys.stdout = sys.stdout, quiet
    try:
        rc = ingest.main(["--archive", str(TMP / "lemon"), "--index", str(TMP / "index.db")])
    finally:
        sys.stdout = out
check("sample index built", rc == 0, rc)

print("-- the vehicle list is kept until the table changes --")
conn = index.connect(TMP / "index.db")
first = index.vehicles(conn)
first[0]["make"] = "changed by a caller"
check("each call gets its own dicts", index.vehicles(conn)[0]["make"] != "changed by a caller")
conn.execute("UPDATE vehicles SET file_path = file_path || 'x' WHERE doc_id = ?", (first[0]["doc_id"],))
conn.commit()
check("a repath is seen at once",
      next(v for v in index.vehicles(conn) if v["doc_id"] == first[0]["doc_id"])["file_path"].endswith("x"))
conn.execute("UPDATE vehicles SET file_path = substr(file_path, 1, length(file_path) - 1)")
conn.commit()
conn.close()

print("-- the labour book is read once per sidecar file --")
side = TMP / "corpus"
side.mkdir()
with sqlite3.connect(side / wis_links.NAME) as c:
    c.execute("CREATE TABLE labour_group(kg TEXT, name TEXT)")
    c.execute("INSERT INTO labour_group VALUES ('20', 'Cooling')")
calls = []
real_rows = wis_links._rows
wis_links._rows = lambda *a, **k: calls.append(a) or real_rows(*a, **k)
try:
    for _ in range(3):
        rows = wis_links._static_rows(side, "SELECT kg, name FROM labour_group")
    check("three lookups, one read", rows == [("20", "Cooling")] and len(calls) == 1, calls)
    time.sleep(0.05)
    with sqlite3.connect(side / wis_links.NAME) as c:
        c.execute("UPDATE labour_group SET name = 'Engine cooling'")
    os.utime(side / wis_links.NAME, None)
    rows = wis_links._static_rows(side, "SELECT kg, name FROM labour_group")
    check("a re-extracted sidecar is read again", rows == [("20", "Engine cooling")], rows)
finally:
    wis_links._rows = real_rows

print("-- slow answers keep their own threads --")
server.ASK_THREADS, server.ASK_QUEUE = 2, 4
app = server.create_app(str(TMP / "index.db"))
# one event loop for every request, as under uvicorn (a TestClient used
# without `with` starts a loop per request)
pc = TestClient(app, base_url="http://localhost:8756", client=("127.0.0.1", 50000)).__enter__()
doc = pc.get("/api/vehicles").json()[0]["doc_id"]
release = threading.Event()


def slow_ask(*a, **k):
    release.wait(20)
    raise query.ApiUnavailable("fake: stopped by the test")


query.ask = slow_ask
os.environ["ANTHROPIC_API_KEY"] = "sk-test-not-real"
codes: list[int] = []
threads = [threading.Thread(target=lambda: codes.append(
    pc.post("/api/ask", json={"doc_id": doc, "question": "oil capacity"}).status_code)) for _ in range(8)]
for t in threads:
    t.start()
time.sleep(1.5)
t0 = time.time()
r = pc.get("/api/status")
quick = time.time() - t0
r2 = pc.get("/api/search", params={"doc_id": doc, "q": "oil"})
check("status and search answer while answers wait", r.status_code == 200 and r2.status_code == 200
      and quick < 2, f"{r.status_code} {r2.status_code} {quick:.2f}s")
release.set()
for t in threads:
    t.join(30)
check("past the queue: 503 at once, the rest answered (400: the test's stopped AI)",
      sorted(codes) == [400] * 4 + [503] * 4, codes)

print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
