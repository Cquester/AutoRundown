"""The MCP server for Claude (mcp_server.py) -- FREE, nothing leaves the PC.

    python tests/test_mcp_server.py

1. Against a recording fake app: every tool sends GET only, to the
   allow-list; an id can't reach another route ("../settings"); a search
   hit's bucket is never handed back as a car; a VIN gives its exact
   Baumuster's engine; the app being down or saying 404 comes back as
   words; a non-loopback AUTORUNDOWN_URL is refused.
2. End to end: the real serve.py on sample WIS + LEMON data at a free port,
   and an MCP client starting mcp_server.py over stdio as Claude does
   (without PYTHONUTF8, as Claude Desktop starts it). No tool result
   carries a folder of this PC's (file_path).
Needs `pip install mcp` (requirements-dev.txt).
"""
from __future__ import annotations

from _paths import ROOT
import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(ROOT))
import mcp_server as m  # noqa: E402

fails = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f" -- {detail}" if detail and not ok else ""))
    fails += not ok


# --- 1. a recording fake app ------------------------------------------------------
seen: list[tuple[str, str]] = []
# a US VIN with its right check digit, and the same with a wrong one
GOOD = next(v for d in "0123456789X" if m.vin_check_ok(v := f"WDBJF65J{d}YB000000"))
BAD = GOOD[:8] + ("1" if GOOD[8] != "1" else "2") + GOOD[9:]
VEH = [
    {"doc_id": "car-1", "make": "Mercedes-Benz", "model": "W210 E-Class",
     "year_range": "1995-2003", "model_code": "210", "doc_type": "manual",
     "general_doc_id": "pool-210", "file_path": "C:\\secret\\wis", "variants": json.dumps([
         {"name": "E 320 Sedan", "engines": ["104.995", "112.941"],
          "baumuster": ["210.055", "210.065"],
          "engine_by_bm": {"210.055": ["104.995"], "210.065": ["112.941"]},
          "vin9": [GOOD[:9]], "vin9_bm": {GOOD[:9]: "210.065"}}])},
    {"doc_id": "pool-210", "make": "Mercedes-Benz", "model": "No model stated (en)",
     "year_range": "", "doc_type": "manual", "variants": "[]"},
    {"doc_id": "car-2", "make": "Mercedes-Benz", "model": "W201 190", "year_range": "1982-1993",
     "model_code": "201", "doc_type": "manual", "variants": json.dumps([
         {"name": "190 E", "engines": ["102.962"], "baumuster": ["201.112"]}])},
    {"doc_id": "f150", "make": "Ford", "model": "F-150", "year_range": "2015",
     "doc_type": "manual", "variants": "[]"},
    {"doc_id": "g", "make": "Mercedes-Benz", "model": "W447 V-Class", "year_range": "2014+",
     "doc_type": "manual", "variants": "[]"},
]


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _any(self):
        seen.append((self.command, self.path))
        path = self.path.split("?")[0]
        if "999" in path:
            body, code = {"detail": "page not found"}, 404
        elif path == "/api/vehicles":
            body, code = VEH, 200
        elif path == "/api/search" or path == "/api/history":
            body, code = [{"page_no": "1", "doc_id": "pool-210", "title": "T", "file_path": "C:\\x"},
                          {"page_no": "2", "doc_id": "car-1", "title": "U"}], 200
        elif path.startswith("/api/page/"):
            body, code = {"page_no": "1", "title": "T", "text": "See [[ref:12::Brake pads]] first.",
                          "file_path": "C:\\x"}, 200
        else:
            body, code = {"ok": True, "file_path": "C:\\x"}, 200
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = do_POST = do_PUT = do_DELETE = _any


fake = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=fake.serve_forever, daemon=True).start()
c = m.AppClient(f"http://127.0.0.1:{fake.server_address[1]}")

calls = [
    (m.find_vehicle, ("W210",)), (m.find_vehicle, ("1999 E 320",)),
    (m.search_manual, ("car-1", "oil drain plug")),
    (m.search_manual, ("car-1", "Drehmoment 30 N·m °", 0, None, "torque")),
    (m.read_page, ("car-1", "1000070")), (m.manual_contents, ("car-1", 0, None, ["05 Engine"])),
    (m.job_values, ("car-1", "torque", ["1", "2"], 0)), (m.fluids, ("car-1", 0)),
    (m.labour_times, ("car-1", 0)), (m.labour_times, ("car-1", 0, None, "oil change")),
    (m.service_schedule, ("car-1", 0)), (m.bulletins, ("car-1",)),
    (m.history, ()), (m.history_entry, (3,)),
]
for fn, args in calls:
    try:
        r = fn(c, *args)
        check(f"{fn.__name__}{args[:2]}: no file_path", "file_path" not in json.dumps(r), json.dumps(r))
    except m.AppError as e:
        check(f"{fn.__name__}{args[:2]}", False, str(e))
check("only GET sent", all(meth == "GET" for meth, _ in seen), f"{seen}")
check("only allowed routes", all(m._allowed(p.split("?")[0]) for _, p in seen),
      f"{[p for _, p in seen if not m._allowed(p.split('?')[0])]}")
check("never /api/ask", not any("/api/ask" in p for _, p in seen))
check("non-ASCII query sent encoded", any("Drehmoment" in p and "%C2%B0" in p for _, p in seen),
      f"{[p for _, p in seen if 'Drehmoment' in p]}")
check("fluids without a variant asks for one", "note" in m.fluids(c, "car-1"))

hits = m.search_manual(c, "car-1", "x")
check("a hit's bucket is filed_under, never a doc_id",
      all("doc_id" not in h for h in hits) and hits[0].get("filed_under") == "pool-210"
      and "filed_under" not in hits[1], f"{hits}")
check("LEMON link marks become their label",
      m.read_page(c, "car-1", "1")["text"] == "See Brake pads first.")

n = len(seen)
for bad in ("../settings", "..", ".", "", "x/../../api/settings", "a%2F..", "a\\b", "a?b=1", "a#b"):
    for fn, args in ((m.read_page, ("car-1", bad)), (m.read_page, (bad, "1")),
                     (m.job_values, (bad, "torque", ["1"])), (m.history_entry, (bad,))):
        try:
            fn(c, *args)
            check(f"{fn.__name__} refuses id {bad!r}", False)
        except m.AppError:
            pass
check("no tricky id reached the app", len(seen) == n, f"{seen[n:]}")
try:
    c.get("/api/settings")
    check("a route off the list is refused", False)
except m.AppError:
    check("a route off the list is refused", True)


def found(q: str) -> list[str]:
    return [x["doc_id"] for x in m.find_vehicle(c, q)["cars"]]


check("the general pool is never a car", "pool-210" not in found("") + found("no model stated"))
check("'E320' finds 'E 320'", found("E320") == ["car-1"], f"{found('E320')}")
check("'F150' finds 'F-150'", found("F150") == ["f150"], f"{found('F150')}")
check("a single-year manual: 2015 yes, 2014 no", found("2015 F-150") == ["f150"]
      and not found("2014 F-150"))
check("'2014+' runs on", found("2020 V-Class") == ["g"], f"{found('2020 V-Class')}")
check("an engine 'M112' is the engine, not Baumuster 201.112", found("M112") == ["car-1"],
      f"{found('M112')}")
check("year outside the range finds nothing", not found("2010 W210"))

r = m.find_vehicle(c, GOOD)
v = (r.get("cars") or [{}])[0].get("variants", [{}])[0]
check("VIN -> its Baumuster's engine only", v.get("baumuster") == ["210.065"]
      and v.get("engine") == "112", f"{r}")
check("VIN -> market us", r["cars"][0].get("market") == "us", f"{r}")
r = m.find_vehicle(c, "WDB2100551A000000")
v = (r.get("cars") or [{}])[0].get("variants", [{}])[0]
check("FIN -> its Baumuster's engine, market row", v.get("engine") == "104"
      and r["cars"][0].get("market") == "row", f"{r}")
check("VIN with I/O/Q refused", "error" in m.find_vehicle(c, "WDBJF65JOYB000000"))
check("VIN with a bad check digit not decoded", "error" in m.find_vehicle(c, BAD))
check("a whole variant with no VIN: all its engines",
      m.find_vehicle(c, "W210")["cars"][0]["variants"][0]["engine"] == "104,112")

try:
    m.read_page(c, "car-1", "999")
    check("404 comes back as words", False)
except m.AppError as e:
    check("404 comes back as words", "page not found" in str(e), str(e))
for bad in ("http://192.168.1.5:8756", "http://example.com", "file:///etc/passwd",
            "http://127.0.0.1.example.com:8756"):
    try:
        m.check_url(bad)
        check(f"refuses {bad}", False)
    except SystemExit:
        check(f"refuses {bad}", True)
fake.shutdown()
try:
    m.find_vehicle(c, "x")
    check("app down says so", False)
except m.AppError as e:
    check("app down says so", "isn't running" in str(e), str(e))


# --- 2. end to end: serve.py + an MCP client over stdio ------------------------------
TMP = Path(tempfile.mkdtemp(prefix="ar_mcp_"))
env = {**os.environ, "PYTHONUTF8": "1", "AUTORUNDOWN_HOME": str(TMP / "home")}
for k in [k for k in env if k.endswith(("_API_KEY", "_BASE_URL"))]:
    env.pop(k)
corpus, lemon, db = ROOT / "sample_corpus", TMP / "lemon", TMP / "index.db"
for cmd in (["tools/make_sample_corpus.py"],          # writes sample_corpus/ (gitignored)
            ["tools/make_sample_lemon.py", "--out", str(lemon)],
            ["ingest.py", "--archive", str(corpus), "--index", str(db)],
            ["ingest.py", "--archive", str(lemon), "--index", str(db), "--importer", "lemon",
             "--only", "2015-ford-f-150"]):
    subprocess.run([sys.executable, *cmd], cwd=ROOT, env=env, check=True, capture_output=True)
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
url = f"http://127.0.0.1:{port}"
app = subprocess.Popen([sys.executable, "serve.py", "--index", str(db), "--host", "127.0.0.1",
                        "--port", str(port)], cwd=ROOT, env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def drive() -> None:
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    # as Claude Desktop starts it: no PYTHONUTF8
    child = {k: v for k, v in env.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "mcp_server.py")],
                                   env={**child, "AUTORUNDOWN_URL": url})
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            check("instructions carry the citation rules",
                  "cite" in (init.instructions or ""), init.instructions or "")
            tools = {t.name: t for t in (await session.list_tools()).tools}
            check("11 tools", len(tools) == 11, f"{sorted(tools)}")
            check("all read-only", all(t.annotations and t.annotations.read_only_hint
                                       for t in tools.values()))
            check("no ask tool", not any("ask" in n for n in tools), f"{sorted(tools)}")

            async def call(name: str, **args):
                r = await session.call_tool(name, args)
                text = "".join(getattr(x, "text", "") for x in r.content)
                check(f"{name}: no file_path", "file_path" not in text, text[:200])
                return json.loads(text)

            cars = (await call("find_vehicle", query="W210"))["cars"]
            check("find_vehicle finds the W210", bool(cars), f"{cars}")
            doc, var = cars[0]["doc_id"], cars[0]["variants"][0]
            hits = await call("search_manual", doc_id=doc, query="engine oil",
                              variant=var["variant"], engine=var["engine"])
            check("search_manual gives hits", bool(hits), f"{hits}")
            page = await call("read_page", doc_id=doc, page_no=hits[0]["page_no"])
            check("read_page gives the text", len(page.get("text", "")) > 20, f"{page}")
            vals = await call("job_values", doc_id=doc, kind="torque",
                              pages=[hits[0]["page_no"]], variant=var["variant"])
            check("job_values gives the job's torques",
                  bool(vals) and all("Nm" in x["value"] for x in vals), f"{vals}")
            # a page kept in the series' general pool opens through the car
            tq = await call("search_manual", doc_id=doc, query="torque", tab="torque",
                            variant=var["variant"])
            pool = next((h for h in tq if h.get("filed_under")), None)
            check("a general-pool hit is marked filed_under", pool is not None, f"{tq}")
            if pool:
                pg = await call("read_page", doc_id=doc, page_no=pool["page_no"])
                check("...and opens with the car's doc_id", "text" in pg and "error" not in pg, f"{pg}")
            deg = await call("search_manual", doc_id=doc, query="Ölwanne 90° Drehmoment")
            check("a non-ASCII query is no error", isinstance(deg, list), f"{deg}")
            lab = await call("labour_times", doc_id=doc, variant=var["variant"])
            check("labour_times browses the book", "groups" in lab or "books" in lab, f"{lab}")
            gone = await call("read_page", doc_id=doc, page_no="99999999")
            check("a missing page is an error, not a crash", "error" in gone, f"{gone}")
            for bad in ("../settings", "..%2Fsettings"):
                r = await call("read_page", doc_id=bad, page_no="1")
                check(f"id {bad!r} refused end to end", "not a valid id" in r.get("error", ""), f"{r}")
            # a LEMON car
            f150 = (await call("find_vehicle", query="F150 2015"))["cars"]
            check("F150 finds the LEMON F-150", len(f150) == 1, f"{f150}")
            if f150:
                lh = await call("search_manual", doc_id=f150[0]["doc_id"], query="brake caliper")
                check("LEMON search gives hits", bool(lh), f"{lh}")
                if lh:
                    lp = await call("read_page", doc_id=f150[0]["doc_id"], page_no=lh[0]["page_no"])
                    check("LEMON page text, no [[ref marks",
                          bool(lp.get("text")) and "[[ref:" not in lp["text"], f"{lp}")


try:
    for _ in range(80):
        try:
            urllib.request.urlopen(url + "/api/stats", timeout=2)
            break
        except OSError:
            time.sleep(0.25)
    asyncio.run(drive())
finally:
    app.terminate()
    app.wait(10)

print("all passed" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
