"""Ask on every AI provider, end to end against the fakes -- FREE, ~20 s.

    python tests/test_providers.py

Anthropic, Google Gemini, xAI and DeepSeek (autorundown/providers.py), each
through its own wire format to tools/fake_anthropic.py: a normal answer, an
invented citation dropped, an answer whose only citation is invented
withheld, a reply that is not the schema withheld (never a crash), not
found, a refusal, an overload and "no credit" as readable errors, and every
call in the spend log with its provider, model and cost. Then the parts that
need no API: whose key pays per provider, prices by date and hour, a price
that is not known, and the Anthropic request left exactly as it was.

Every provider's address points at the fake before anything is imported,
so a real key in the environment can't spend.
"""
from __future__ import annotations

from _paths import ROOT
import json
import os
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_providers_"))
os.environ["AUTORUNDOWN_HOME"] = str(TMP / "home")   # before importing autorundown
for name in ("ANTHROPIC", "GEMINI", "XAI", "DEEPSEEK"):
    os.environ.pop(f"{name}_API_KEY", None)

import fake_anthropic  # noqa: E402  (tools/, via _paths)

server = fake_anthropic.serve(0)                     # any free port
threading.Thread(target=server.serve_forever, daemon=True).start()
os.environ.update(fake_anthropic.base_urls(server.server_address[1]))
KEY = "test-key-not-real"

from autorundown import config, index, providers, query  # noqa: E402

fails = 0


def check(name: str, ok: bool, detail: object = "") -> None:
    global fails
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f" -- {detail}" if detail != "" and not ok else ""))
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

# the cheapest model of each provider, as a shop would try first
CHEAP = {"anthropic": "claude-haiku-4-5", "gemini": "gemini-3.5-flash-lite",
         "xai": "grok-4.3", "deepseek": "deepseek-flash"}
check("every provider has a model here", set(CHEAP) == set(providers.PROVIDERS))
ERRORS = {   # what the person asking reads, per provider
    "anthropic": ("529", "Out of API credit"),
    "gemini": ("503", "quota"),
    "xai": ("503", "Out of API credit"),
    "deepseek": ("503", "Out of API credit"),
}

for pid, model in CHEAP.items():
    print(f"\n-- {pid} ({model}) --")

    def ask(question: str) -> dict:
        return query.ask(conn, vehicle, question, KEY, model=model, source="eval",
                         provider=pid).as_dict()

    before = len(fake_anthropic.REQUESTS)
    d = ask("brake caliper bolt torque")
    pages = {str(p["page_no"]) for p in d["passages"]}
    check("answer found, marked as fake", d["found"] and d["answer"].startswith("[fake API]"), d)
    check("the page it opens is a retrieved, cited page",
          d["document_page"] in pages and d["document_page"] in d["source_pages"], d["document_page"])
    check("every value cites a retrieved page",
          d["specs"] and all(set(s["source_pages"]) <= pages for s in d["specs"]), d["specs"])
    paths = [r["path"] for r in fake_anthropic.REQUESTS[before:]]
    want = {"anthropic": "/v1/messages", "gemini": f"/v1beta/models/{model}:generateContent",
            "xai": "/xai/v1/chat/completions", "deepseek": "/deepseek/chat/completions"}[pid]
    check(f"planner and answer both went to {want}", paths == [want, want], paths)
    check("usage says which provider and model",
          d["usage"]["provider"] == pid and d["usage"]["model"] == model
          and d["usage"]["calls"] == 2 and d["usage"]["cost_usd"] > 0, d["usage"])

    d = ask("brake caliper bolt torque fake:badcite")
    check("an invented citation is dropped, the rest kept",
          d["found"] and all(s["name"] != "Invented bolt" for s in d["specs"])
          and any("999999" in x for x in d["dropped_citations"]), d)

    d = ask("replace the brake pads fake:steps")
    check("an answer whose only citation is invented is withheld",
          not d["found"] and not d["steps"] and not d["document_page"] and not d["answer"]
          and "withheld" in d["caveats"], d)

    d = ask("brake caliper bolt torque fake:malformed")
    check("a reply that is not the schema is withheld, not a crash",
          not d["found"] and "no parsable answer" in d["caveats"] and not d["specs"], d)
    check("  ... and the planner's prose fell back to the question itself",
          d["queries"] == ["brake caliper bolt torque fake:malformed"], d["queries"])

    check("not found", not ask("brake torque fake:notfound")["found"])
    d = ask("brake torque fake:refusal")
    check("a refusal is not an answer", not d["found"] and "declined" in d["caveats"], d)
    for word, text in zip(("fake:error", "fake:nocredit"), ERRORS[pid]):
        try:
            ask(f"brake torque {word}")
            check(f"{word} raises", False, "no error")
        except query.ApiUnavailable as e:
            check(f"{word} reads: ...{text}...", text in str(e) and KEY not in str(e), str(e))

    try:
        query.ask(conn, vehicle, "brake torque", "", model=model, provider=pid)
        check("no key: refused before any call", False, "no error")
    except query.NoApiKey as e:
        check("no key: refused before any call", providers.PROVIDERS[pid]["short"] in str(e), str(e))

    rows = conn.execute("SELECT provider, model, cost_usd, calls FROM usage_log "
                        "WHERE provider=? AND source='eval'", (pid,)).fetchall()
    check("every question is in the spend log with provider and model",
          len(rows) >= 6 and all(r[1] == model and r[2] is not None for r in rows), rows[:3])

print("\n-- the wire formats --")
def system_of(body: dict) -> str:
    if "systemInstruction" in body:
        return body["systemInstruction"]["parts"][0]["text"]
    if "messages" in body and body["messages"][0].get("role") == "system":
        return body["messages"][0]["content"]
    return body.get("system") or ""


# the last answer (not planner) request each provider got
reqs = {r["path"]: r["body"] for r in fake_anthropic.REQUESTS
        if system_of(r["body"]).startswith(query.ANSWER_SYSTEM[:60])}
g = reqs[f"/v1beta/models/{CHEAP['gemini']}:generateContent"]
gs = json.dumps(g["generationConfig"]["responseJsonSchema"])
check("Gemini: schema inlined, no $ref, no default",
      "$ref" not in gs and '"default"' not in gs and "source_pages" in gs, gs[:200])
x = reqs["/xai/v1/chat/completions"]["response_format"]
check("xAI: strict json_schema, every property required",
      x["type"] == "json_schema" and x["json_schema"]["strict"] is True
      and set(x["json_schema"]["schema"]["required"]) == set(x["json_schema"]["schema"]["properties"]), x)
ds = reqs["/deepseek/chat/completions"]
check("DeepSeek: JSON mode, schema and example in the system prompt",
      ds["response_format"] == {"type": "json_object"}
      and "JSON schema" in ds["messages"][0]["content"]
      and ds["messages"][0]["content"].startswith(query.ANSWER_SYSTEM[:40]), ds["messages"][0]["content"][-300:])
a = reqs["/v1/messages"]
check("Anthropic: the request of before (structured output, no new fields)",
      set(a) == {"model", "max_tokens", "system", "messages", "output_config"}
      and a["max_tokens"] == 8000 and a["system"] == query.ANSWER_SYSTEM
      and "format" in a["output_config"], sorted(a))

print("\n-- validation of replies --")
check("code fences are tolerated",
      providers.parse_reply(query.SearchPlan, '```json\n{"queries": ["a b"]}\n```').queries == ["a b"])
for bad in ("", "  ", "no json here", '{"queries": "a string"}', "[1, 2]", '{"found": true'):
    check(f"{bad!r} is None, not an exception", providers.parse_reply(query.AnswerModel, bad) is None)

print("\n-- whose key pays, per provider --")
cfg = config.Config(api_key="sk-ant-company-0000AAAA", provider="gemini",
                    provider_keys={"gemini": "AIza-company-gemini-GGGG"})
k = config.pick_key(cfg, None, True)
check("the PC uses the company key of the active provider",
      (k.key, k.ref) == ("AIza-company-gemini-GGGG", "company:gemini"), k)
k = config.pick_key(cfg, None, True, "anthropic")
check("  ... and Anthropic's ref is 'company', as before providers",
      (k.key, k.ref) == ("sk-ant-company-0000AAAA", "company"), k)
check("no xAI key: an empty key (NoApiKey at ask time), never another provider's",
      config.pick_key(cfg, None, True, "xai").key == "")
cfg.key_mode = "per_mechanic"
config.set_mechanic_entry(cfg, 7, "anthropic", {"api_key": "sk-ant-sam-1111SAMK", "since": "x"})
try:
    config.pick_key(cfg, 7, False)
    check("Sam's Anthropic key does not pay for Gemini", False, "no error")
except config.NoKeyForRequest as e:
    check("Sam's Anthropic key does not pay for Gemini", "Gemini" in str(e), str(e))
config.set_mechanic_entry(cfg, 7, "gemini", {"api_key": "AIza-sam-2222SAMG", "since": "y"})
k = config.pick_key(cfg, 7, False)
check("Sam's own Gemini key does, counted as user:7:gemini",
      (k.key, k.ref) == ("AIza-sam-2222SAMG", "user:7:gemini"), k)
check("  ... his Anthropic key is untouched",
      config.pick_key(cfg, 7, False, "anthropic").key == "sk-ant-sam-1111SAMK")
config.set_mechanic_entry(cfg, 7, "gemini", None)
check("removing his Gemini key keeps the Anthropic one",
      config.mechanic_entry(cfg, 7, "anthropic").get("api_key") == "sk-ant-sam-1111SAMK"
      and not config.mechanic_entry(cfg, 7, "gemini"), cfg.mechanic_keys)
check("a model saved under another provider falls back to this one's default",
      config.active_model(config.Config(provider="xai", model="claude-opus-5")) == "grok-4.7")

print("\n-- prices --")
utc = lambda *a: datetime(*a, tzinfo=timezone.utc)  # noqa: E731
check("Gemini 4 Argon doubles on 2026-12-01 (Google's announced change)",
      providers.price("gemini-4-argon", utc(2026, 11, 30, 12)) == (2.0, 10.0)
      and providers.price("gemini-4-argon", utc(2026, 12, 1, 0)) == (4.0, 20.0))
check("DeepSeek: peak price on a weekday 07:00 UTC, half off-peak and at weekends",
      providers.price("deepseek-flash", utc(2026, 10, 14, 7)) == (0.30, 1.20)
      and providers.price("deepseek-flash", utc(2026, 10, 14, 12)) == (0.15, 0.60)
      and providers.price("deepseek-flash", utc(2026, 10, 17, 7)) == (0.15, 0.60))
providers.PROVIDERS["xai"]["models"]["grok-test"] = {"label": "t", "price_in": None, "price_out": None}
try:
    check("a price not known is None, not a guess", providers.cost("grok-test", 1000, 1000) is None)
    u = query.Usage(model="grok-test", provider="xai", input_tokens=10, output_tokens=10, calls=1)
    check("  ... and as_dict says so (null)", u.as_dict()["cost_usd"] is None)
    index.log_usage(conn, "eval", vehicle["doc_id"], "unknown price", u)
    row = conn.execute("SELECT provider, cost_usd FROM usage_log WHERE question='unknown price'").fetchone()
    check("  ... logged with cost NULL", tuple(row) == ("xai", None), tuple(row))
    check("  ... /api/models shows it as not tracked",
          any(m["id"] == "grok-test" and m["price_in"] is None for m in providers.model_list("xai")))
finally:
    del providers.PROVIDERS["xai"]["models"]["grok-test"]
check("every listed model's price has a source and the date it was checked",
      all(m["price_checked"] == providers.PRICE_CHECKED and m["price_source"].startswith("https://")
          for p in providers.PROVIDERS for m in providers.model_list(p)))

server.shutdown()
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
