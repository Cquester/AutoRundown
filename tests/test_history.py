"""History of questions and searches (history.py) -- FREE, no API calls, ~2 s.

    python tests/test_history.py

Drives the real FastAPI app in-process, as test_handoff.py does: this PC
(admin), two signed-in mechanics and a phone that is not signed in. The AI
call is replaced by a stub that returns a fixed answer, so nothing is paid;
what is checked is that the answer is stored whole, comes back as it was,
and that each person sees only what they may. Throwaway AUTORUNDOWN_HOME.
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo on sys.path)
import json
import os
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_history_"))
os.environ["AUTORUNDOWN_HOME"] = str(TMP)  # before importing autorundown

from fastapi.testclient import TestClient  # noqa: E402

from autorundown import config, index, query, server  # noqa: E402

fails = 0


def check(cond: bool, what: str) -> None:
    global fails
    print(("  ok   " if cond else "  FAIL ") + what)
    fails += not cond


def client(addr: str, host: str = "localhost:8756") -> TestClient:
    return TestClient(APP, base_url=f"http://{host}", client=(addr, 50000))


def post(c: TestClient, path: str, body: dict | None = None):
    return c.post(path, json=body or {}, headers={"Origin": str(c.base_url).rstrip("/")})


VARIANTS = [
    {"name": "E 320", "baumuster": ["211.065"], "engine_families": {"112": "M112 · V6 petrol"}},
    {"name": "E 500", "baumuster": ["211.083"], "engine_families": {"113": "M113 · V8 petrol"}},
]
idx = TMP / "index.db"
conn = index.connect(idx)
conn.execute("INSERT INTO vehicles(doc_id, source_name, make, model, year_range, variants) "
             "VALUES ('w211','mercedes-wis','Mercedes-Benz','W211 E-Class','2003-2009',?)",
             (json.dumps(VARIANTS),))
conn.commit()
conn.close()
APP = server.create_app(str(idx), TMP / "auth.db")

# the AI call: a fixed answer, nothing sent anywhere
ANSWER = {"found": True, "answer": "Tighten to 20 Nm.", "specs": [{"name": "Sensor", "value": "20 Nm"}],
          "document_page": "123", "source_pages": ["123"], "queries": ["oil level sensor"],
          "usage": {"model": "claude-haiku-4-5", "calls": 2, "cost_usd": 0.015}}
asked = []


class _Stub:
    def as_dict(self) -> dict:
        return json.loads(json.dumps(ANSWER))


def fake_ask(conn, vehicle, question, key, **kw):
    asked.append(question)
    return _Stub()


query.ask = fake_ask
config.pick_key = lambda *a, **k: config.KeyChoice("dummy", "company")

pc = client("127.0.0.1")
anon = client("192.168.1.40", "192.168.1.20:8756")


def sign_in(addr: str, uid: int) -> TestClient:
    c = client(addr, "192.168.1.20:8756")
    g = post(pc, f"/api/admin/users/{uid}/qr").json()
    r = c.get("/signin?" + g["url"].split("?", 1)[1], follow_redirects=False)
    assert r.status_code == 303 and c.cookies.get("ar_token"), r.text
    return c


def hist(c: TestClient) -> list[dict]:
    r = c.get("/api/history")
    assert r.status_code == 200, r.text
    return r.json()


print("an Ask is kept whole")
r = post(pc, "/api/ask", {"doc_id": "w211", "question": "replace oil level sensor",
                          "variant": 0, "engine": "112", "year": 2004})
check(r.status_code == 200 and r.json()["answer"] == ANSWER["answer"], "the PC asks (stubbed AI)")
rows = hist(pc)
check(len(rows) == 1 and rows[0]["kind"] == "ask" and rows[0]["question"] == "replace oil level sensor",
      "it is in the PC's history")
check(rows[0]["label"] == "2004 Mercedes-Benz E 320" and rows[0]["vname"] == "E 320"
      and rows[0]["variant"] == 0 and rows[0]["engine"] == "112" and rows[0]["year"] == 2004,
      "  ... with the car as a structured pick, named from the index")
check(rows[0]["cost_usd"] == 0.015 and rows[0]["found"] is True and rows[0]["by"] == "this PC",
      "  ... its cost, found, and who asked")
check("result" not in rows[0], "  ... the list carries no answers (they are fetched one at a time)")
e = pc.get(f"/api/history/{rows[0]['id']}").json()
check(e["result"] == ANSWER, "opening it returns the answer exactly as it was given")
check(len(asked) == 1, "  ... without asking the AI again")

print("a Search is kept as its words; a tab's search is not")
check(pc.get("/api/search", params={"doc_id": "w211", "q": "oil pan", "record": 1}).status_code == 200,
      "the question box's Search")
pc.get("/api/search", params={"doc_id": "w211", "q": "oil pan", "tab": "parts", "record": 1})
pc.get("/api/search", params={"doc_id": "w211", "q": "drain plug"})
rows = hist(pc)
check([r["kind"] for r in rows] == ["search", "ask"] and rows[0]["question"] == "oil pan",
      "only that one is kept, newest first")
check(rows[0]["label"] == "2003-2009 Mercedes-Benz W211 E-Class" and rows[0]["variant"] is None,
      "  ... for the whole series when no model was picked")

print("who sees what")
sam_id = post(pc, "/api/admin/users", {"name": "Sam"}).json()["id"]
dax_id = post(pc, "/api/admin/users", {"name": "Daxton"}).json()["id"]
sam = sign_in("192.168.1.50", sam_id)
dax = sign_in("192.168.1.60", dax_id)
check(post(sam, "/api/ask", {"doc_id": "w211", "question": "sam's question"}).status_code == 200,
      "Sam asks from his phone")
anon.get("/api/search", params={"doc_id": "w211", "q": "anon words", "record": 1})
sam_rows = hist(sam)
check([r["question"] for r in sam_rows] == ["sam's question"], "Sam sees only his own")
check(hist(dax) == [], "Daxton sees nothing of Sam's or the PC's")
check([r["question"] for r in hist(anon)] == ["anon words"],
      "a phone not signed in sees only not-signed-in entries (not the PC's)")
pc_rows = hist(pc)
check(len(pc_rows) == 4 and {r["by"] for r in pc_rows} == {"this PC", "Sam", "not signed in"},
      "the PC sees everyone's, with names")
check(dax.get(f"/api/history/{sam_rows[0]['id']}").status_code == 404,
      "Daxton can't open Sam's answer by its id")
check(anon.get(f"/api/history/{rows[-1]['id']}").status_code == 404,
      "a phone not signed in can't open the PC's answer")
check(sam.get(f"/api/history/{sam_rows[0]['id']}").json()["result"]["answer"] == ANSWER["answer"],
      "Sam can open his own")

print(f"\n{'all passed' if not fails else f'{fails} failed'}")
raise SystemExit(1 if fails else 0)
