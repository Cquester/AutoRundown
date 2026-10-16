"""A stand-in for the Anthropic Messages API, for test boxes and dev.py -- FREE.

    python tools/fake_anthropic.py [--port 8758]

Then point the app at it (dev.py --fake-api does both):

    ANTHROPIC_BASE_URL=http://127.0.0.1:8758  ANTHROPIC_API_KEY=sk-fake-...

The app's own code path runs unchanged -- the real SDK, `messages.parse`,
`_verify_citations`, the spend log, the UI -- only the reply is made up here.
Nothing leaves the machine and nothing is billed. Every answer starts with
"[fake API]" so a screenshot of one can't be mistaken for a real answer.

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

The request is checked like the real API checks it: `thinking` or an
`effort` on a model that doesn't take them is a 400 (query.MODELS).
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


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # one line per call, no headers
        print(f"fake_anthropic: {fmt % args}", flush=True)

    def reply(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("request-id", "req_fake")
        self.end_headers()
        self.wfile.write(data)

    def error(self, status: int, kind: str, msg: str) -> None:
        self.reply(status, {"type": "error", "error": {"type": kind, "message": msg}})

    def do_POST(self):
        if self.path.split("?")[0] != "/v1/messages":
            return self.error(404, "not_found_error", f"fake API: no route {self.path}")
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        model = req.get("model", "")
        if not self.headers.get("x-api-key"):
            return self.error(401, "authentication_error", "x-api-key header is required")
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
        q = field(prompt, "Question")
        if "fake:error" in q:
            return self.error(529, "overloaded_error", "Overloaded (fake)")
        if "fake:nocredit" in q:
            return self.error(400, "invalid_request_error",
                              "Your credit balance is too low to access the Anthropic API.")
        if "fake:slow" in q:
            time.sleep(8)

        planning = system.startswith("You turn a mechanic's question")
        stop = "refusal" if ("fake:refusal" in q and not planning) else "end_turn"
        body = plan(prompt) if planning else answer(prompt)
        text = json.dumps(body)
        self.reply(200, {
            "id": "msg_fake", "type": "message", "role": "assistant", "model": model,
            "content": [{"type": "text", "text": text}],
            "stop_reason": stop, "stop_sequence": None,
            # rough token counts, so the spend log and caps have numbers to show
            "usage": {"input_tokens": len(prompt) // 4 + len(system) // 4,
                      "output_tokens": len(text) // 4},
        })


def serve(port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=8758)
    port = ap.parse_args().port
    print(f"fake Anthropic API on http://127.0.0.1:{port}", flush=True)
    serve(port).serve_forever()


if __name__ == "__main__":
    main()
