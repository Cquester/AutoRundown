"""Ask, end to end, against the fake Anthropic API -- FREE, nothing leaves the PC.

    python tests/test_fake_api.py

Builds the sample LEMON vehicle into a temporary index, starts
tools/fake_anthropic.py on a free port and runs query.ask() through the real
SDK: planner, retrieval, the structured answer, _verify_citations, the spend
log. The fake's "fake:..." words drive the rarer paths (an invented citation,
an uncited procedure, an API error). ANTHROPIC_BASE_URL points at the fake
before anything is imported, so a real key in the environment can't spend.
"""
from __future__ import annotations

from _paths import ROOT
import os
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_fake_api_"))
os.environ["AUTORUNDOWN_HOME"] = str(TMP / "home")   # before importing autorundown

import fake_anthropic  # noqa: E402  (tools/, via _paths)

server = fake_anthropic.serve(0)                     # any free port
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ["ANTHROPIC_BASE_URL"] = f"http://127.0.0.1:{server.server_address[1]}"
KEY = "sk-fake-test-not-a-real-key"

from autorundown import index, query  # noqa: E402

fails = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f" -- {detail}" if detail and not ok else ""))
    fails += not ok


lemon, db = TMP / "lemon", TMP / "index.db"
env = {**os.environ, "PYTHONUTF8": "1"}
subprocess.run([sys.executable, "tools/make_sample_lemon.py", "--out", str(lemon)],
               cwd=ROOT, env=env, check=True, capture_output=True)
subprocess.run([sys.executable, "ingest.py", "--archive", str(lemon), "--index", str(db),
                "--importer", "lemon", "--only", "2015-ford-f-150"],
               cwd=ROOT, env=env, check=True, capture_output=True)
conn = index.connect(db)
vehicle = index.vehicles(conn)[0]


def ask(question: str, model: str = "claude-haiku-4-5") -> dict:
    return query.ask(conn, vehicle, question, KEY, model=model, source="eval").as_dict()


# 1. a value question: the page it opens is one retrieval returned, and every
#    value shown is printed on a cited page
d = ask("brake caliper bolt torque")
pages = {p["page_no"] for p in d["passages"]}
check("answer found", d["found"], f"{d}")
check("document page is a retrieved page", d["document_page"] in map(str, pages), d["document_page"])
check("document page is cited", d["document_page"] in d["source_pages"], f"{d['source_pages']}")
check("marked as fake", d["answer"].startswith("[fake API]"), d["answer"])
check("nothing dropped", not d["dropped_citations"], f"{d['dropped_citations']}")

# 2. both kinds of model the app offers go through: adaptive thinking and
#    effort are sent only where the real API accepts them (the fake 400s otherwise)
for m in query.MODELS:
    try:
        ask("front brake caliper removal", model=m)
        check(f"request shape accepted for {m}", True)
    except query.ApiUnavailable as e:
        check(f"request shape accepted for {m}", False, str(e))

# 3. an invented citation is dropped by _verify_citations, the rest kept
d = ask("brake caliper bolt torque fake:badcite")
check("invented spec dropped", all(s["name"] != "Invented bolt" for s in d["specs"]), f"{d['specs']}")
check("drop reported", any("999999" in x for x in d["dropped_citations"]), f"{d['dropped_citations']}")

# 4. a procedure citing only an invented page is withheld
d = ask("replace the brake pads fake:steps")
check("uncited procedure withheld", not d["found"] and not d["steps"] and not d["document_page"], f"{d}")

# 5. not found, refusal and API errors reach the user as such
d = ask("brake torque fake:notfound")
check("not found", not d["found"] and "Labour times tab" not in d["caveats"], d["caveats"])
d = ask("labour time to replace the brake pads fake:notfound")
check("a labour question not found points to the Labour times tab",
      not d["found"] and "Labour times tab" in d["caveats"], d["caveats"])
check("refusal is not an answer", not ask("brake torque fake:refusal")["found"])
for word, text in (("fake:error", "529"), ("fake:nocredit", "Out of API credit")):
    try:
        ask(f"brake torque {word}")
        check(f"{word} raises", False, "no error")
    except query.ApiUnavailable as e:
        check(f"{word} raises", text in str(e), str(e))

# 6. every call was logged as spend, tagged eval
n = conn.execute("SELECT COUNT(*) FROM usage_log WHERE source = 'eval'").fetchone()[0]
check("usage logged", n >= 6, f"{n} rows")

server.shutdown()
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
