"""AI chat follow-ups -- FREE, nothing leaves the PC, ~5 s.

    python tests/test_chat.py

The user's request (2026-10-25): the first answer stays a brief and the
manual page; then the mechanic reports what they tested ("tests 1-4 done,
got this -- is that the culprit?") and the reply comes only from the manual.

Part 1 runs query.ask() through the real provider code against
tools/fake_anthropic.py on the sample LEMON F-150: a follow-up carries the
pages the chat already cited, sends the conversation and the follow-up
rules, and its citations are checked like any answer's.
Part 2 drives the FastAPI app with the AI call stubbed (as test_history.py):
a chat is one history entry; a follow-up takes its car and earlier turns from
that entry, never from the request; nobody can follow up a chat they can't
see; a chat has at most query.MAX_FOLLOWUPS follow-ups.
"""
from __future__ import annotations

from _paths import ROOT
import json
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_chat_"))
os.environ["AUTORUNDOWN_HOME"] = str(TMP / "home")   # before importing autorundown

import fake_anthropic  # noqa: E402  (tools/, via _paths)

fake = fake_anthropic.serve(0)
threading.Thread(target=fake.serve_forever, daemon=True).start()
os.environ["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{fake.server_address[1]}"
KEY = "sk-fake-test-not-a-real-key"

from fastapi.testclient import TestClient  # noqa: E402

from autorundown import config, index, providers, query, server  # noqa: E402

fails = 0


def check(name: str, ok: bool, detail: object = "") -> None:
    global fails
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f" -- {detail}" if detail != "" and not ok else ""))
    fails += not ok


# -- part 1: the follow-up as the model sees it --------------------------------
lemon, db = TMP / "lemon", TMP / "index.db"
env = {**os.environ, "PYTHONUTF8": "1"}
subprocess.run([sys.executable, "tools/make_sample_lemon.py", "--out", str(lemon)],
               cwd=ROOT, env=env, check=True, capture_output=True)
subprocess.run([sys.executable, "ingest.py", "--archive", str(lemon), "--index", str(db),
                "--importer", "lemon", "--only", "2015-ford-f-150"],
               cwd=ROOT, env=env, check=True, capture_output=True)
conn = index.connect(db)
vehicle = index.vehicles(conn)[0]

sent: list[tuple[str, str, str]] = []      # (kind, system, user) of every call
_real_call = providers.call


def recording_call(provider, key, model, kind, system, user, *a, **k):
    sent.append((kind, system, user))
    return _real_call(provider, key, model, kind, system, user, *a, **k)


providers.call = recording_call


def ask(question: str, earlier=None) -> dict:
    return query.ask(conn, vehicle, question, KEY, model="claude-haiku-4-5", source="eval",
                     earlier=earlier).as_dict()


print("-- a first answer is as before --")
first = ask("brake caliper bolt torque")
check("first answer found, with a page", first["found"] and first["document_page"], first)
check("first answer uses the normal rules, no conversation",
      sent[-1][1] == query.ANSWER_SYSTEM and "Conversation so far" not in sent[-1][2])

print("-- a follow-up --")
earlier = query.earlier_turns({"question": "brake caliper bolt torque", "result": first})
check("earlier_turns: the first turn with its question",
      len(earlier) == 1 and earlier[0]["question"] == "brake caliper bolt torque")
sent.clear()
d = ask("I tightened it, the pads still squeal, what next", earlier)
plan, answer = sent[0], sent[-1]
check("the planner sees the conversation", "Mechanic: brake caliper bolt torque" in plan[2], plan[2][:300])
check("the answer call uses the follow-up rules", answer[1] == query.FOLLOWUP_SYSTEM)
check("  ... which say earlier answers are not sources and nothing comes from outside the manual",
      "NOT sources" in answer[1] and "Never diagnose from general knowledge" in answer[1])
check("the conversation comes before the new question",
      answer[2].index("Conversation so far") < answer[2].index("\nQuestion: I tightened it"))
check("  ... and no conversation line reads as the question",
      [l for l in answer[2].splitlines() if l.startswith("Question:")]
      == ["Question: I tightened it, the pads still squeal, what next"])
check("the page the chat cited is sent again, first and marked",
      d["passages"][0]["page_no"] == first["document_page"]
      and f"[page {first['document_page']}]" in answer[2]
      and "cited earlier in this conversation" in answer[2], [p["page_no"] for p in d["passages"]][:4])
check("a follow-up is answered from the passages", d["found"] and d["document_page"], d)

print("-- a follow-up's citations are checked like any answer's --")
d = ask("caliper bolt torque again fake:badcite", earlier)
check("invented citation dropped", any("999999" in x for x in d["dropped_citations"]), d["dropped_citations"])
d = ask("so what is the culprit fake:steps", earlier)
check("an uncited reply is withheld", not d["found"] and not d["document_page"], d)

print("-- earlier answers are context, never a source --")
forged = [{**earlier[0], "document_page": "999999", "source_pages": ["999999"]}]
d = ask("caliper bolt torque", forged)
check("a page only an earlier answer names is not sent (not in the index)",
      "999999" not in [p["page_no"] for p in d["passages"]])
providers.call = _real_call

# -- part 2: the server keeps a chat as one history entry ----------------------
VARIANTS = [
    {"name": "E 320", "baumuster": ["211.065"], "engine_families": {"112": "M112 · V6 petrol"}},
    {"name": "E 500", "baumuster": ["211.083"], "engine_families": {"113": "M113 · V8 petrol"}},
]
idx = TMP / "chat.db"
c2 = index.connect(idx)
for doc in ("w211", "w210"):
    c2.execute("INSERT INTO vehicles(doc_id, source_name, make, model, year_range, variants) "
               "VALUES (?,'mercedes-wis','Mercedes-Benz','E-Class','2003-2009',?)",
               (doc, json.dumps(VARIANTS)))
c2.commit()
c2.close()
APP = server.create_app(str(idx), TMP / "auth.db")
c0 = config.load()   # a phone not signed in is part of these checks: an open
c0.require_signin = False   # network (sign-in is on by default since 2026-10-29)
c0.save()
calls: list[dict] = []


class _Stub:
    def __init__(self, n):
        self.n = n

    def as_dict(self) -> dict:
        return {"found": True, "answer": f"Reply {self.n}.", "specs": [], "document_page": "123",
                "source_pages": ["123"], "queries": ["x"],
                "usage": {"model": "claude-haiku-4-5", "calls": 2, "cost_usd": 0.01}}


def fake_ask(conn, vehicle, question, key, **kw):
    calls.append({"question": question, **kw})
    return _Stub(len(calls))


query.ask = fake_ask
config.pick_key = lambda *a, **k: config.KeyChoice("dummy", "company")


def client(addr: str, host: str = "localhost:8756") -> TestClient:
    return TestClient(APP, base_url=f"http://{host}", client=(addr, 50000))


def post(c: TestClient, path: str, body: dict | None = None):
    return c.post(path, json=body or {}, headers={"Origin": str(c.base_url).rstrip("/")})


pc = client("127.0.0.1")
print("-- a chat is one history entry --")
r = post(pc, "/api/ask", {"doc_id": "w211", "question": "bed lamp not working",
                          "variant": 0, "engine": "112", "year": 2004}).json()
chat = r.get("chat_id")
check("the first answer names its chat", isinstance(chat, int), r)
check("  ... and starts with no earlier turns", calls[-1]["earlier"] is None)
r = post(pc, "/api/ask", {"doc_id": "w211", "question": "did test A1-A4, all OK", "chat": chat,
                          "variant": 1, "engine": "113", "year": 2009}).json()
check("a follow-up answers in the same chat", r.get("chat_id") == chat and r["answer"] == "Reply 2.", r)
kw = calls[-1]
check("  ... with the first turn as context", [t["question"] for t in kw["earlier"]] == ["bed lamp not working"]
      and kw["earlier"][0]["answer"] == "Reply 1.", kw["earlier"])
check("  ... for the chat's car, not the one the request named",
      (kw["variant"], kw["engine"], kw["year"]) == (0, "112", 2004), (kw["variant"], kw["engine"], kw["year"]))
post(pc, "/api/ask", {"doc_id": "w211", "question": "A5 shows 0 V", "chat": chat})
check("a third turn sees both before it",
      [t["question"] for t in calls[-1]["earlier"]] == ["bed lamp not working", "did test A1-A4, all OK"])
rows = pc.get("/api/history").json()
check("History lists the chat once, with its follow-ups and their cost",
      len(rows) == 1 and rows[0]["question"] == "bed lamp not working" and rows[0]["followups"] == 2
      and abs(rows[0]["cost_usd"] - 0.03) < 1e-9, rows)
e = pc.get(f"/api/history/{chat}").json()
check("opening it returns every turn", e["result"]["answer"] == "Reply 1."
      and [t["question"] for t in e["result"]["turns"]] == ["did test A1-A4, all OK", "A5 shows 0 V"]
      and [t["answer"] for t in e["result"]["turns"]] == ["Reply 2.", "Reply 3."], e["result"])

print("-- who may follow up --")
n = len(calls)
phone = client("192.168.1.40", "192.168.1.20:8756")
check("a phone can't follow up the PC's chat",
      post(phone, "/api/ask", {"doc_id": "w211", "question": "x", "chat": chat}).status_code == 404)
check("a chat is about its own car only",
      post(pc, "/api/ask", {"doc_id": "w210", "question": "x", "chat": chat}).status_code == 404)
check("an unknown chat is refused",
      post(pc, "/api/ask", {"doc_id": "w211", "question": "x", "chat": 99999}).status_code == 404)
check("  ... none of them reached the AI", len(calls) == n)

print("-- a chat has a limit --")
for i in range(query.MAX_FOLLOWUPS - 2):
    post(pc, "/api/ask", {"doc_id": "w211", "question": f"more {i}", "chat": chat})
r = post(pc, "/api/ask", {"doc_id": "w211", "question": "one too many", "chat": chat})
check(f"follow-up {query.MAX_FOLLOWUPS + 1} is refused with a reason",
      r.status_code == 400 and "start a new one" in r.text, r.text)
check("  ... without reaching the AI", calls[-1]["question"] != "one too many")

fake.shutdown()
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
