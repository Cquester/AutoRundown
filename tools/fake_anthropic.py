"""A stand-in for the AI APIs, for test boxes and dev.py -- FREE.

    python tools/fake_anthropic.py [--port 8758]

Fakes every provider's wire format on one port (autorundown/providers.py):

    /v1/messages                               Anthropic Messages API
    /v1beta/models/<model>:generateContent     Google Gemini
    /xai/v1/chat/completions                   xAI (OpenAI-compatible)
    /deepseek/chat/completions                 DeepSeek (OpenAI-compatible)

Then point the app at it (dev.py --fake-api does it; `base_urls(port)`):

    ANTHROPIC_BASE_URL=http://127.0.0.1:8758  GEMINI_BASE_URL=http://127.0.0.1:8758
    XAI_BASE_URL=http://127.0.0.1:8758/xai/v1 DEEPSEEK_BASE_URL=http://127.0.0.1:8758/deepseek

The app's own code path runs unchanged -- the real SDK, `messages.parse`,
providers.py's HTTPS calls, `_verify_citations`, the spend log, the UI --
only the reply is made up here. Nothing leaves the machine and nothing is
billed. Every answer starts with "[fake API]" so a screenshot of one can't
be mistaken for a real answer. Each provider's request is checked the way
that API would (its key header; Gemini's JSON-schema subset; xAI's strict
json_schema; DeepSeek's JSON mode, which needs the word "json" in the prompt).

The answer is built from the passages the app sent, the way a well-behaved
model would answer: the best-matching page as `document_page`, and the
"<number> Nm / lb-ft" values printed on it as `specs`. Values are copied
verbatim from the page, never invented.

Words in the question pick other replies, to exercise the paths a real
model only takes now and then:

    fake:notfound   found=false
    fake:badcite    cites a page retrieval never returned (citation check)
    fake:steps      a procedure whose only citation is invented (withheld)
    fake:refusal    stop_reason "refusal"
    fake:error      HTTP 529 overloaded
    fake:nocredit   HTTP 400 "credit balance is too low"
    fake:slow       waits 8 s before answering
    fake:onepart    cites a parts page but gives only one of its current part numbers
    fake:otherengine adds a value for engine M103 on a cited page (other-vehicle check)
    fake:malformed  the planner gets prose, the answer JSON of the wrong shape

All of them work on every provider; a refusal, an overload and "no credit"
are sent the way that provider sends them.

The Anthropic request is checked like the real API checks it: `thinking` or
an `effort` on a model that doesn't take them is a 400 (query.MODELS).
"""
from __future__ import annotations

import argparse
import json
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# models that accept adaptive thinking / effort -- mirrors query.MODELS
NEWER = ("claude-opus-5", "claude-sonnet-5")

PAGE_RE = re.compile(r"^\[page ([^\]]+)\] (.*)$", re.M)
VALUE_RE = re.compile(
    r"(\d+(?:[.,]\d+)?(?:\s*(?:-|to)\s*\d+(?:[.,]\d+)?)?\s*"
    r"(?:Nm|N·m|lb[.\s-]*ft|lb[.\s-]*in|ft[.\s-]*lb))", re.I)
STOP = set("a an and are can do does for from how i in is it my of on or the to "
           "what when where which with you your".split())


def words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in STOP and len(w) > 1}


def passages(prompt: str) -> list[dict]:
    """The [page N] blocks query.format_passages wrote."""
    marks = list(PAGE_RE.finditer(prompt))
    out = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(prompt)
        out.append({"page": m.group(1).strip(), "title": m.group(2).strip(),
                    "text": prompt[m.end():end]})
    return out


def field(prompt: str, name: str) -> str:
    m = re.search(rf"^{name}: (.*)$", prompt, re.M)
    return m.group(1).strip() if m else ""


def plan(prompt: str) -> dict:
    q = field(prompt, "Question")
    w = [x for x in re.findall(r"[A-Za-z0-9.]+", q) if x.lower() not in STOP
         and not x.lower().startswith("fake")]
    queries = [" ".join(w[:4])] if w else []
    if "torque" not in q.lower() and w:
        queries.append(" ".join(w[:3] + ["torque"]))
    return {"queries": queries or [q]}


def specs_on(p: dict) -> list[dict]:
    out, seen = [], set()
    for line in p["text"].splitlines():
        for m in VALUE_RE.finditer(line):
            value = m.group(1).strip()
            name = re.sub(r"[\s:|.,-]+$", "", line[:m.start()]).strip(" |") or p["title"]
            name = re.sub(r"^(tighten|torque)\s+(the\s+)?", "", name, flags=re.I)
            name = re.sub(r"\s+(to|at|of|is|=)$", "", name, flags=re.I)[-80:]
            key = (name.lower(), value)
            if key in seen:
                continue
            seen.add(key)
            out.append({"name": name[:1].upper() + name[1:], "value": value,
                        "applicability": "", "source_pages": [p["page"]]})
    return out[:6]


def answer(prompt: str) -> dict:
    q = field(prompt, "Question")
    ps = passages(prompt)
    if "fake:notfound" in q or not ps:
        return {"found": False, "answer": "", "caveats": "[fake API] Not in these passages."}
    if "fake:steps" in q:
        return {"found": True, "answer": "[fake API] A procedure citing an invented page.",
                "steps": ["Remove the wheel.", "Remove the caliper."],
                "source_pages": ["999999"], "document_page": "999999"}
    if "fake:onepart" in q:
        # what a real answer did (2026-10-15): cite a parts page and give only
        # one of the current part numbers that fit, leaving the others out
        parts = [p for p in ps if p["title"].startswith("(parts catalogue)")]
        for p in parts:
            for line in p["text"].splitlines():
                m = re.match(r"^[^|]*\|\s*([A-Z] [0-9 ]+?)\s*\(([A-Z][0-9]+)[^)]*\)\s*\|\s*([^|]*)\|\s*([^|]*)\|(.*)$", line)
                if m and "replaced by" not in m.group(5):
                    return {"found": True, "answer": f"[fake API] The part is {m.group(1)}.",
                            "document_page": p["page"], "source_pages": [p["page"]],
                            "specs": [{"name": m.group(3).strip(), "value": m.group(1).strip(),
                                       "applicability": m.group(4).strip(), "source_pages": [p["page"]]}],
                            "steps": [], "figures": [], "caveats": ""}
        return {"found": False, "answer": "", "caveats": "[fake API] No parts page."}
    qw = words(q)
    best = max(ps, key=lambda p: (len(qw & words(p["title"])) * 3
                                  + len(qw & words(p["text"])) + bool(specs_on(p))))
    specs = specs_on(best)
    if "fake:badcite" in q:
        specs.append({"name": "Invented bolt", "value": "999 Nm", "applicability": "",
                      "source_pages": ["999999"]})
    if "fake:otherengine" in q:
        # a row for another engine, on a real page: what _drop_other_vehicles removes
        specs.append({"name": "Other engine's bolt", "value": "10 Nm",
                      "applicability": "M103 (103.942) only", "source_pages": [best["page"]]})
    what = "the values it uses are below" if specs else "it prints no torque values"
    return {
        "found": True,
        "answer": f"[fake API] The manual covers this on \"{best['title']}\", "
                  f"shown beside this answer; {what}.",
        "document_page": best["page"],
        "specs": specs,
        "steps": [],
        "figures": [],
        "source_pages": [best["page"]],
        "caveats": "",
    }


PLANNER_START = "You turn a mechanic's question"
# what Gemini's responseJsonSchema accepts (ai.google.dev/api/generate-content)
GEMINI_SCHEMA_KEYS = {"$id", "$defs", "$ref", "$anchor", "type", "format", "title",
                      "description", "enum", "items", "prefixItems", "minItems",
                      "maxItems", "minimum", "maximum", "anyOf", "oneOf", "properties",
                      "additionalProperties", "required", "propertyOrdering"}
# every request the fake answered, newest last: tests read the wire format here
REQUESTS: list[dict] = []
# the newer model each provider's model list offers (Shop setup's "Check for
# new models"): invented, so nothing real is ever named as available
FAKE_NEW = {"anthropic": "claude-fake-6", "gemini": "gemini-fake-5-flash",
            "xai": "grok-fake-5", "deepseek": "deepseek-fake-r3"}


def base_urls(port: int) -> dict:
    """The environment that points every provider of the app at this fake."""
    root = f"http://127.0.0.1:{port}"
    return {"ANTHROPIC_BASE_URL": root, "GEMINI_BASE_URL": root,
            "XAI_BASE_URL": f"{root}/xai/v1", "DEEPSEEK_BASE_URL": f"{root}/deepseek"}


def gemini_schema_problem(node, path="schema") -> str:
    """A keyword outside Gemini's supported subset, or ""."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k not in GEMINI_SCHEMA_KEYS:
                return f"{path}: unsupported keyword {k!r}"
            if k in ("properties", "$defs"):
                for name, sub in v.items():
                    bad = gemini_schema_problem(sub, f"{path}.{k}.{name}")
                    if bad:
                        return bad
            elif isinstance(v, (dict, list)):
                bad = gemini_schema_problem(v, f"{path}.{k}")
                if bad:
                    return bad
    elif isinstance(node, list):
        for i, v in enumerate(node):
            bad = gemini_schema_problem(v, f"{path}[{i}]")
            if bad:
                return bad
    return ""


def reply_text(system: str, prompt: str) -> tuple[str, bool]:
    """(the model's text, declined?) -- the same for every provider."""
    q = field(prompt, "Question")
    planning = system.startswith(PLANNER_START)
    if "fake:malformed" in q:
        # prose where JSON was asked for; then JSON of the wrong shape
        return (("Sure! Here are some queries: brake torque" if planning
                 else json.dumps({"found": "maybe", "specs": "Caliper bolt 999 Nm",
                                  "source_pages": 7})), False)
    body = plan(prompt) if planning else answer(prompt)
    return json.dumps(body), ("fake:refusal" in q and not planning)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # one line per call, no headers
        print(f"fake_anthropic: {fmt % args}", flush=True)

    def reply(self, status: int, body) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("request-id", "req_fake")
        self.end_headers()
        self.wfile.write(data)

    def error(self, status: int, kind: str, msg: str) -> None:
        self.reply(status, {"type": "error", "error": {"type": kind, "message": msg}})

    # each provider's model list: the app's own models, one invented newer
    # model (FAKE_NEW), and models that are not for text, which the app must
    # leave out (embedding, image)
    def do_GET(self):
        path = self.path.split("?")[0]
        REQUESTS.append({"path": path, "body": None})
        key = (self.headers.get("x-api-key") or self.headers.get("x-goog-api-key")
               or (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip())
        if not key:
            return self.error(401, "authentication_error", "fake API: no key")
        if path == "/v1/models":
            ids = ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5", FAKE_NEW["anthropic"]]
            return self.reply(200, {"data": [{"type": "model", "id": i, "display_name": i.title(),
                                              "created_at": "2026-10-01T00:00:00Z"} for i in ids],
                                    "has_more": False, "first_id": ids[0], "last_id": ids[-1]})
        if path == "/v1beta/models":
            gen = ["gemini-4-argon", "gemini-3.8-flash", "gemini-3.5-flash-lite",
                   FAKE_NEW["gemini"], "gemini-4-flash-image"]
            return self.reply(200, {"models": [
                {"name": f"models/{i}", "displayName": i, "supportedGenerationMethods":
                 ["generateContent", "countTokens"]} for i in gen]
                + [{"name": "models/gemini-embedding-002", "supportedGenerationMethods": ["embedContent"]}]})
        if path in ("/xai/v1/models", "/deepseek/models"):
            pid = "xai" if path.startswith("/xai") else "deepseek"
            ids = {"xai": ["grok-4.7", "grok-4.3", "grok-imagine-image"],
                   "deepseek": ["deepseek-v4-pro", "deepseek-flash"]}[pid] + [FAKE_NEW[pid]]
            return self.reply(200, {"object": "list", "data": [{"id": i, "object": "model"} for i in ids]})
        return self.error(404, "not_found_error", f"fake API: no route {self.path}")

    def do_POST(self):
        path = self.path.split("?")[0]
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        # header names only (never a key's value), so tests can see where the key went
        REQUESTS.append({"path": path, "body": req,
                         "headers": sorted(k.lower() for k in self.headers.keys())})
        if path == "/v1/messages":
            return self.anthropic(req)
        m = re.fullmatch(r"/v1beta/models/([^/:]+):generateContent", path)
        if m:
            return self.gemini(m.group(1), req)
        if path == "/xai/v1/chat/completions":
            return self.openai_compat("xai", req)
        if path == "/deepseek/chat/completions":
            return self.openai_compat("deepseek", req)
        return self.error(404, "not_found_error", f"fake API: no route {self.path}")

    @staticmethod
    def rare(q: str) -> str | None:
        """The words that make every provider fail its own way."""
        if "fake:slow" in q:
            time.sleep(8)
        for word in ("fake:error", "fake:nocredit"):
            if word in q:
                return word
        return None

    # -- Anthropic ------------------------------------------------------------
    def anthropic(self, req: dict):
        model = req.get("model", "")
        # SDK 1.x sends x-api-key; 2.x sends Authorization: Bearer (and a
        # newer anthropic-version). The API takes either.
        bearer = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        if not (self.headers.get("x-api-key") or bearer):
            return self.error(401, "authentication_error",
                              "x-api-key or Authorization: Bearer header is required")
        old = not model.startswith(NEWER)
        if old and req.get("thinking", {}).get("type") == "adaptive":
            return self.error(400, "invalid_request_error",
                              f"adaptive thinking is not supported on {model}")
        if old and (req.get("output_config") or {}).get("effort"):
            return self.error(400, "invalid_request_error", f"effort is not supported on {model}")
        if not (req.get("output_config") or {}).get("format"):
            return self.error(400, "invalid_request_error", "fake API: expected a structured-output request")

        system = req.get("system") or ""
        system = system if isinstance(system, str) else " ".join(b.get("text", "") for b in system)
        msg = req["messages"][-1]["content"]
        prompt = msg if isinstance(msg, str) else " ".join(b.get("text", "") for b in msg)
        rare = self.rare(field(prompt, "Question"))
        if rare == "fake:error":
            return self.error(529, "overloaded_error", "Overloaded (fake)")
        if rare == "fake:nocredit":
            return self.error(400, "invalid_request_error",
                              "Your credit balance is too low to access the Anthropic API.")
        text, declined = reply_text(system, prompt)
        self.reply(200, {
            "id": "msg_fake", "type": "message", "role": "assistant", "model": model,
            "content": [{"type": "text", "text": text}],
            "stop_reason": "refusal" if declined else "end_turn", "stop_sequence": None,
            # rough token counts, so the spend log and caps have numbers to show
            "usage": {"input_tokens": len(prompt) // 4 + len(system) // 4,
                      "output_tokens": len(text) // 4},
        })

    # -- Google Gemini: generateContent ----------------------------------------
    def gemini_error(self, code: int, status: str, msg: str):
        self.reply(code, {"error": {"code": code, "message": msg, "status": status}})

    def gemini(self, model: str, req: dict):
        if not self.headers.get("x-goog-api-key"):
            return self.gemini_error(403, "PERMISSION_DENIED",
                                     "Method doesn't allow unregistered callers (fake)")
        cfg = req.get("generationConfig") or {}
        if cfg.get("responseMimeType") != "application/json" or not isinstance(
                cfg.get("responseJsonSchema"), dict):
            return self.gemini_error(400, "INVALID_ARGUMENT",
                                     "fake API: expected responseMimeType application/json "
                                     "and a responseJsonSchema")
        bad = gemini_schema_problem(cfg["responseJsonSchema"])
        if bad:
            return self.gemini_error(400, "INVALID_ARGUMENT", f"fake API: {bad}")
        system = " ".join(p.get("text", "") for p in
                          (req.get("systemInstruction") or {}).get("parts", []))
        prompt = " ".join(p.get("text", "") for p in (req.get("contents") or [{}])[-1].get("parts", []))
        rare = self.rare(field(prompt, "Question"))
        if rare == "fake:error":
            return self.gemini_error(503, "UNAVAILABLE", "The model is overloaded (fake).")
        if rare == "fake:nocredit":
            return self.gemini_error(429, "RESOURCE_EXHAUSTED",
                                     "You exceeded your current quota (fake).")
        text, declined = reply_text(system, prompt)
        usage = {"promptTokenCount": (len(prompt) + len(system)) // 4,
                 "candidatesTokenCount": len(text) // 4, "thoughtsTokenCount": 25}
        usage["totalTokenCount"] = sum(usage.values())
        cand = ({"content": {"role": "model", "parts": []}, "finishReason": "SAFETY", "index": 0}
                if declined else
                {"content": {"role": "model", "parts": [{"text": text}]},
                 "finishReason": "STOP", "index": 0})
        self.reply(200, {"candidates": [cand], "usageMetadata": usage, "modelVersion": model})

    # -- xAI and DeepSeek: chat completions -------------------------------------
    def openai_compat(self, provider: str, req: dict):
        auth = self.headers.get("Authorization") or ""
        if not auth.startswith("Bearer ") or len(auth) < 12:
            return self.reply(401, {"error": {"message": "Incorrect API key provided (fake)",
                                              "type": "authentication_error"}})
        msgs = req.get("messages") or []
        system = " ".join(m.get("content", "") for m in msgs if m.get("role") == "system")
        prompt = next((m.get("content", "") for m in reversed(msgs) if m.get("role") == "user"), "")
        rf = req.get("response_format") or {}
        if provider == "xai":
            js = rf.get("json_schema") or {}
            if rf.get("type") != "json_schema" or not isinstance(js.get("schema"), dict) \
                    or not js.get("name") or js.get("strict") is not True:
                return self.reply(400, {"code": "Client specified an invalid argument",
                                        "error": "fake API: expected a strict json_schema response_format"})
        elif rf.get("type") != "json_object" or "json" not in (system + prompt).lower():
            return self.reply(400, {"error": {
                "message": "fake API: expected response_format json_object and the word "
                           "'json' in the prompt", "type": "invalid_request_error"}})
        rare = self.rare(field(prompt, "Question"))
        if rare == "fake:error":
            return self.reply(503, {"error": {"message": "Service unavailable (fake)",
                                              "type": "server_error"}})
        if rare == "fake:nocredit":
            if provider == "deepseek":
                return self.reply(402, {"error": {"message": "Insufficient Balance (fake)",
                                                  "type": "unknown_error"}})
            return self.reply(403, {"code": "The caller does not have permission",
                                    "error": "Your team has run out of credits (fake)."})
        text, declined = reply_text(system, prompt)
        p_tok, c_tok = (len(prompt) + len(system)) // 4, len(text) // 4
        usage = {"prompt_tokens": p_tok, "completion_tokens": c_tok}
        if provider == "xai":   # reasoning beside completion, as xAI's sample shows it
            usage["completion_tokens_details"] = {"reasoning_tokens": 40}
            usage["total_tokens"] = p_tok + c_tok + 40
        else:
            usage["total_tokens"] = p_tok + c_tok
        if declined:
            message = ({"role": "assistant", "content": None,
                        "refusal": "I can't help with that (fake)."} if provider == "xai"
                       else {"role": "assistant", "content": ""})
            finish = "stop" if provider == "xai" else "content_filter"
        else:
            message, finish = {"role": "assistant", "content": text}, "stop"
        self.reply(200, {"id": "chatcmpl-fake", "object": "chat.completion",
                         "model": req.get("model", ""),
                         "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                         "usage": usage})


def serve(port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8758)
    port = ap.parse_args().port
    print(f"fake AI APIs (Anthropic, Gemini, xAI, DeepSeek) on http://127.0.0.1:{port}",
          flush=True)
    serve(port).serve_forever()


if __name__ == "__main__":
    main()
