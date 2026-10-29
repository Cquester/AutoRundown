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
ah = next(r["headers"] for r in reversed(fake_anthropic.REQUESTS) if r["path"] == "/v1/messages")
check("Anthropic: the key goes in one header (Bearer from SDK 2.x, x-api-key before)",
      ("authorization" in ah) != ("x-api-key" in ah) and "anthropic-version" in ah, ah)

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

print("\n-- Shop setup over HTTP (this PC) --")
from fastapi.testclient import TestClient  # noqa: E402
from autorundown import server as app_server  # noqa: E402

app = app_server.create_app(str(db), TMP / "auth.db")
pc = TestClient(app, base_url="http://localhost:8756", client=("127.0.0.1", 50000))
phone = TestClient(app, base_url="http://192.168.1.20:8756", client=("192.168.1.50", 50000))
GKEY = "AIza-test-not-real-GGGG"
r = pc.post("/api/settings", json={"provider": "gemini"})
s = pc.get("/api/settings").json()
check("switching provider saves it and moves the model to that provider's default",
      r.status_code == 200 and s["provider"] == "gemini" and s["model"] == "gemini-3.8-flash", s.get("model"))
check("every provider is listed with its key page; no key yet for Gemini",
      [p["id"] for p in s["providers"]] == list(providers.PROVIDERS)
      and not s["has_api_key"], s["providers"])
check("an unknown provider is refused", pc.post("/api/settings", json={"provider": "openai"}).status_code == 422)
check("another provider's model is refused",
      pc.post("/api/settings", json={"model": "claude-opus-5"}).status_code == 400)
r = pc.post("/api/settings", json={"api_key": GKEY, "key_provider": "gemini"})
s = pc.get("/api/settings").json()
check("the Gemini key is saved as Gemini's, shown by its last 4 only",
      r.status_code == 200 and s["has_api_key"] and s["company_key_last4"] == "GGGG"
      and GKEY not in str(s) and config.load().provider_keys == {"gemini": GKEY}
      and config.load().api_key == "", s)
models = pc.get("/api/models").json()
check("/api/models lists Gemini's models with prices",
      [m["id"] for m in models] == list(providers.PROVIDERS["gemini"]["models"])
      and all(m["price_in"] for m in models), models)
me = phone.get("/api/me").json()
check("a phone sees the provider and model, never a key",
      me["provider"] == "gemini" and me["default_model"] == "gemini-3.8-flash"
      and GKEY not in str(me) and "GGGG" not in str(me), me)
r = phone.post("/api/ask", json={"doc_id": vehicle["doc_id"], "question": "brake caliper bolt torque",
                                 "model": "gemini-3.5-flash-lite"})
d = r.json()
check("a phone's question is answered by Gemini on the company Gemini key",
      r.status_code == 200 and d["found"] and d["usage"]["provider"] == "gemini", d)
row = conn.execute("SELECT provider, model, key_ref FROM usage_log ORDER BY id DESC").fetchone()
check("  ... logged as gemini / gemini-3.5-flash-lite / company:gemini",
      tuple(row) == ("gemini", "gemini-3.5-flash-lite", "company:gemini"), tuple(row))
r = phone.post("/api/ask", json={"doc_id": vehicle["doc_id"], "question": "q", "model": "claude-opus-5"})
check("a model of a provider with no key is refused", r.status_code == 400, r.status_code)

print("\n-- the question box's model menu: every provider with a key --")
XKEY = "xai-test-not-real-XXXX"
pc.post("/api/settings", json={"api_key": XKEY, "key_provider": "xai"})
s = pc.get("/api/settings").json()
check("a second provider's key is added without changing the default",
      s["provider"] == "gemini" and config.load().provider_keys.get("xai") == XKEY, s["provider"])
menu = [m["id"] for m in phone.get("/api/models", params={"menu": 1}).json()]
check("the menu: the default first, then the rest of Gemini's and all of xAI's; no Anthropic (no key)",
      menu == ["gemini-3.8-flash", "gemini-4-argon", "gemini-3.5-flash-lite", "grok-4.7", "grok-4.3"], menu)
check("  ... /api/models without menu is still the default provider's (Shop setup's list)",
      [m["id"] for m in pc.get("/api/models").json()] == list(providers.PROVIDERS["gemini"]["models"]))
r = phone.post("/api/ask", json={"doc_id": vehicle["doc_id"], "question": "brake caliper bolt torque",
                                 "model": "grok-4.3"})
row = conn.execute("SELECT provider, model, key_ref FROM usage_log ORDER BY id DESC").fetchone()
check("a model from the menu picks its provider: xAI answers on the xAI key",
      r.status_code == 200 and r.json()["usage"]["provider"] == "xai"
      and tuple(row) == ("xai", "grok-4.3", "company:xai"), (r.status_code, tuple(row)))
r = pc.post("/api/settings", json={"hidden_models": ["grok-4.7", "gemini-3.8-flash"]})
s = pc.get("/api/settings").json()
menu = [m["id"] for m in phone.get("/api/models", params={"menu": 1}).json()]
check("a hidden model leaves the menu; the default can't be hidden",
      r.status_code == 200 and s["hidden_models"] == ["grok-4.7"] and "grok-4.7" not in menu
      and menu[0] == "gemini-3.8-flash", (s["hidden_models"], menu))
check("  ... Shop setup lists each provider's models with hidden marked",
      [m["id"] for p in s["providers"] if p["id"] == "xai" for m in p["models"] if m["hidden"]] == ["grok-4.7"])
r = phone.post("/api/ask", json={"doc_id": vehicle["doc_id"], "question": "q", "model": "grok-4.7"})
check("a hidden model is refused", r.status_code == 400 and "turned off" in r.json()["detail"], r.json())
pc.post("/api/settings", json={"provider": "deepseek"})
rows = phone.get("/api/models", params={"menu": 1}).json()
check("the default provider has no key: another's default leads, not its first model, and says so",
      rows[0]["id"] == "gemini-3.8-flash" and rows[0].get("instead_of") == "DeepSeek"
      and not any(r.get("instead_of") for r in rows[1:]), [(r["id"], r.get("instead_of")) for r in rows[:2]])
pc.post("/api/settings", json={"provider": "gemini"})
check("  ... and with a key, nothing is said",
      not any(r.get("instead_of") for r in phone.get("/api/models", params={"menu": 1}).json()))
check("an unknown model can't be hidden",
      pc.post("/api/settings", json={"hidden_models": ["gpt-9"]}).status_code == 400)
pc.post("/api/settings", json={"hidden_models": []})

print("\n-- newer models from each provider's own list (free: a GET, no tokens) --")
check("Anthropic's list (the SDK's models.list): the new one is there",
      fake_anthropic.FAKE_NEW["anthropic"] in [m["id"] for m in providers.list_models("anthropic", KEY)])
r = pc.get("/api/settings/models/gemini").json()
check("Gemini: only what the menu lacks; image and embedding models left out",
      [m["id"] for m in r["new"]] == ["gemini-fake-5-flash"], r)
r = pc.get("/api/settings/models/xai").json()
check("xAI: the same", [m["id"] for m in r["new"]] == ["grok-fake-5"], r)
r = pc.get("/api/settings/models/anthropic")
check("no company key for the provider: a plain refusal, nothing sent",
      r.status_code == 400 and "No Anthropic API key" in r.json()["detail"], r.json())
check("a phone can't ask the provider's list", phone.get("/api/settings/models/xai").status_code == 403)
r = pc.post("/api/settings", json={"extra_models": {"xai": ["grok-fake-5"]}})
menu = phone.get("/api/models", params={"menu": 1}).json()
added = next((m for m in menu if m["id"] == "grok-fake-5"), None)
check("an added model joins the menu, its price not known (cost not tracked)",
      r.status_code == 200 and added and added["price_in"] is None and added["provider"] == "xai", added)
r = phone.post("/api/ask", json={"doc_id": vehicle["doc_id"], "question": "brake caliper bolt torque",
                                 "model": "grok-fake-5"})
row = conn.execute("SELECT provider, model, cost_usd FROM usage_log ORDER BY id DESC").fetchone()
check("  ... answers through its provider, logged with cost NULL, never a guess",
      r.status_code == 200 and tuple(row) == ("xai", "grok-fake-5", None), (r.status_code, tuple(row)))
check("  ... and the provider's list no longer offers it as new",
      pc.get("/api/settings/models/xai").json()["new"] == [])
check("another provider's built-in model can't be added",
      pc.post("/api/settings", json={"extra_models": {"xai": ["claude-opus-5"]}}).status_code == 400)
check("an unknown provider can't be given models",
      pc.post("/api/settings", json={"extra_models": {"openai": ["gpt-9"]}}).status_code == 400)
pc.post("/api/settings", json={"extra_models": {}})
r = phone.post("/api/ask", json={"doc_id": vehicle["doc_id"], "question": "q", "model": "grok-fake-5"})
check("removed: out of the menu and refused",
      r.status_code == 400 and "grok-fake-5" not in [m["id"] for m in phone.get("/api/models", params={"menu": 1}).json()])
uid = pc.post("/api/admin/users", json={"name": "Sam"}).json()["id"]
pc.post("/api/settings", json={"key_mode": "per_mechanic"})
r = pc.post(f"/api/admin/users/{uid}/key", json={"api_key": "AIza-sam-not-real-SAMG"})
cfg = config.load()
check("a mechanic's key is stored for the active provider",
      r.status_code == 200 and config.mechanic_entry(cfg, uid, "gemini")["api_key"] == "AIza-sam-not-real-SAMG"
      and not config.mechanic_entry(cfg, uid, "anthropic").get("api_key")
      and r.json()["key"]["last4"] == "SAMG", r.json())
pc.post("/api/settings", json={"provider": "anthropic"})
users = pc.get("/api/admin/users").json()["users"]
check("back on Anthropic, Sam has no key there (his Gemini key is kept)",
      not users[0]["key"]["has_key"]
      and config.mechanic_entry(config.load(), uid, "gemini").get("api_key"), users[0]["key"])

server.shutdown()
print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
