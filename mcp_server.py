"""AutoRundown for Claude: a read-only MCP server (stdio), local only.

    claude mcp add autorundown -- python <repo>/mcp_server.py

Claude (Desktop or Code, on this PC) starts this; each tool asks the
*running* app's free routes on loopback (AUTORUNDOWN_URL, default
http://127.0.0.1:8756), so Claude gets what the web page shows: the vehicle
filter, WIS Validity lines, the car's column of a torque table, labour
ranking. Nothing here reads the index itself.

The app treats a loopback request as admin, so this sends GET only, to the
allow-list in ALLOWED (never /api/ask: no spend on the shop's keys; no
import, setting, key, account or VM route). A non-loopback address is
refused. The app's citation check (_verify_citations) runs only inside Ask,
so INSTRUCTIONS give Claude the same rules to follow. Needs `pip install
mcp` (requirements-dev.txt); not in the MSI.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_URL = "http://127.0.0.1:8756"
LOOPBACK = {"127.0.0.1", "localhost", "::1"}
# GET only, and only these routes (prefix + "/" or exact)
ALLOWED = ("/api/vehicles", "/api/search", "/api/page", "/api/manual/contents",
           "/api/values", "/api/fluids", "/api/wis/oil", "/api/wis/labour",
           "/api/wis/service", "/api/wis/bulletins", "/api/history")
MAX_CARS = 20
MAX_TEXT = 40_000      # a page's text sent to Claude, characters

INSTRUCTIONS = """\
AutoRundown is this shop's own repair-manual library (Mercedes WIS/EPC,
LEMON manuals, folders). Every tool is free and read-only.

How to use it: find_vehicle first (it gives doc_id and the variant index);
then search_manual / read_page / job_values / fluids / labour_times with that
doc_id and, when known, the variant -- values differ per engine and model.

Rules, the same ones the app's own answers follow:
- Answer only from pages these tools returned, and cite each value's page
  (doc_id + page_no). If the manual doesn't say, say "not found in the
  manual" -- never fill in a value from general knowledge.
- Give only the chosen car's values; a table row for another engine or
  model is not this car's.
- Never convert units; give the value as printed. Different units for the
  same fastener are one spec written twice, not a conflict.
- A part number must be one printed on a page you cite.
"""


class AppError(Exception):
    """What to tell Claude: the app is down, or said no (its own words)."""


def check_url(url: str) -> str:
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or (u.hostname or "") not in LOOPBACK:
        raise SystemExit(f"AUTORUNDOWN_URL must be this PC's own address "
                         f"(127.0.0.1 / localhost), not {url!r}")
    return url.rstrip("/")


def _allowed(path: str) -> bool:
    return any(path == a or path.startswith(a + "/") for a in ALLOWED)


def _strip_paths(x: Any) -> Any:
    """No folder of this PC's in a result."""
    if isinstance(x, dict):
        return {k: _strip_paths(v) for k, v in x.items() if k != "file_path"}
    if isinstance(x, list):
        return [_strip_paths(v) for v in x]
    return x


class AppClient:
    def __init__(self, base_url: str, timeout: float = 60):
        self.base = check_url(base_url)
        self.timeout = timeout

    def get(self, route: str, **params: Any) -> Any:
        if not _allowed(route):
            raise AppError(f"not a route this server may use: {route}")
        q = [(k, str(v).lower() if isinstance(v, bool) else str(v))
             for k, vals in params.items() if vals is not None
             for v in (vals if isinstance(vals, (list, tuple)) else [vals])]
        url = self.base + urllib.parse.quote(route) + ("?" + urllib.parse.urlencode(q) if q else "")
        req = urllib.request.Request(url, method="GET", headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return _strip_paths(json.loads(r.read().decode("utf-8")))
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode("utf-8")).get("detail")
            except (ValueError, AttributeError):
                detail = None
            raise AppError(f"AutoRundown said {e.code}: {detail or e.reason}") from None
        except (urllib.error.URLError, OSError):
            raise AppError(f"AutoRundown isn't running at {self.base} -- "
                           "start it from the Start menu.") from None


# --- finding a car -----------------------------------------------------------

_VIN_VALUES = dict(zip("ABCDEFGHJKLMNPRSTUVWXYZ", [1, 2, 3, 4, 5, 6, 7, 8, 1, 2, 3, 4, 5,
                                                   7, 9, 2, 3, 4, 5, 6, 7, 8, 9]))
_VIN_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def vin_check_ok(vin: str) -> bool:
    """ISO 3779 check digit (position 9), as the page's vinCheck."""
    total = sum((int(c) if c.isdigit() else _VIN_VALUES.get(c, -999)) * w
                for c, w in zip(vin, _VIN_WEIGHTS))
    if total < 0:
        return False
    d = total % 11
    return vin[8] == ("X" if d == 10 else str(d))


def _variants(v: dict) -> list[dict]:
    try:
        return json.loads(v.get("variants") or "[]")
    except ValueError:
        return []


def _car(v: dict, only: list[int] | None = None, note: str = "") -> dict:
    vs = _variants(v)
    idx = only if only is not None else range(len(vs))
    out = {"doc_id": v["doc_id"], "make": v.get("make"), "model": v.get("model"),
           "years": v.get("year_range"), "source": v.get("source_name"),
           "pages": v.get("indexed_pages"),
           "variants": [{"variant": n, "name": vs[n].get("name"),
                         "engines": vs[n].get("engines") or [],
                         "baumuster": vs[n].get("baumuster") or []} for n in idx]}
    if note:
        out["note"] = note
    return out


def _by_vin(cars: list[dict], vin: str) -> dict:
    if any(c in vin for c in "IOQ"):
        return {"error": "A VIN never has I, O or Q -- re-check it (1 / 0?)."}
    # a Mercedes FIN: WDB 210065 1A...: positions 4-9 are the Baumuster
    if vin[:2] == "WD" and vin[3:9].isdigit():
        bm = f"{vin[3:6]}.{vin[6:9]}"
        for v in cars:
            hits = [n for n, y in enumerate(_variants(v)) if bm in (y.get("baumuster") or [])]
            if hits:
                return {"cars": [_car(v, hits, f"Baumuster {bm} from the FIN")]}
        return {"cars": [], "note": f"No car in the library has Baumuster {bm}."}
    if not vin_check_ok(vin):
        return {"error": "The VIN's check digit doesn't match -- re-check it, "
                         "or search by model name and year."}
    vin9 = vin[:9]
    for v in cars:
        hits = [n for n, y in enumerate(_variants(v)) if vin9 in (y.get("vin9") or [])]
        if hits:
            return {"cars": [_car(v, hits, f"VIN prefix {vin9}")]}
    return {"cars": [], "note": "That VIN isn't in the library's VIN tables -- "
                                "search by model name and year."}


def find_vehicle(client: AppClient, query: str) -> dict:
    cars = [v for v in client.get("/api/vehicles") if v.get("doc_type") != "parts"
            or not v.get("joined_into")]
    q = query.strip()
    compact = re.sub(r"\s+", "", q).upper()
    if len(compact) == 17 and compact.isalnum():
        return _by_vin(cars, compact)
    years = {int(y) for y in re.findall(r"\b(19[5-9]\d|20[0-4]\d)\b", q)}
    words = [w.lower() for w in re.findall(r"[\w.\-]+", re.sub(r"\b(19|20)\d\d\b", " ", q))]
    out = []
    for v in cars:
        if years:
            m = re.match(r"(\d{4})\D+(\d{4})", v.get("year_range") or "")
            if m and not any(int(m[1]) <= y <= int(m[2]) for y in years):
                continue
        head = " ".join(str(v.get(k) or "") for k in ("make", "model", "model_code", "doc_id")).lower()
        vs = _variants(v)
        if not words:
            out.append(_car(v))
            continue
        if all(w in head for w in words):
            out.append(_car(v))
            continue
        hits = [n for n, y in enumerate(vs)
                if all(w in head + " " + " ".join([y.get("name") or ""] + (y.get("baumuster") or [])
                                                  + (y.get("engines") or [])).lower() for w in words)]
        if hits:
            out.append(_car(v, hits))
    note = "" if out else "No car matches -- try fewer words (e.g. the model code 'W210', or 'E 320')."
    res: dict = {"cars": out[:MAX_CARS]}
    if len(out) > MAX_CARS:
        note = f"{len(out)} cars match; the first {MAX_CARS} shown -- add a year or model."
    if note:
        res["note"] = note
    return res


# --- the rest: one route each --------------------------------------------------

def search_manual(client: AppClient, doc_id: str, query: str, variant: int | None = None,
                  engine: str | None = None, tab: str = "workshop", limit: int = 10) -> list[dict]:
    rows = client.get("/api/search", doc_id=doc_id, q=query, variant=variant, engine=engine,
                      tab=tab, limit=max(1, min(limit, 30)))
    keep = ("page_no", "doc_id", "title", "section_path", "applicability", "snippet",
            "lines", "rows", "number", "outside", "query")
    return [{k: r[k] for k in keep if r.get(k) not in (None, "", [])} for r in rows]


def read_page(client: AppClient, doc_id: str, page_no: str) -> dict:
    p = client.get(f"/api/page/{doc_id}/{page_no}")
    text = p.get("text") or ""
    out = {k: p[k] for k in ("doc_id", "page_no", "title", "section_path", "applicability",
                             "outside_scope", "doc_info", "links", "xrefs", "diagram")
           if p.get(k) not in (None, "", [], False)}
    out["text"] = text[:MAX_TEXT] + ("\n[... page cut short]" if len(text) > MAX_TEXT else "")
    if p.get("images"):
        out["figures"] = len(p["images"])
    return out


def manual_contents(client: AppClient, doc_id: str, variant: int | None = None,
                    engine: str | None = None, path: list[str] | None = None) -> dict:
    return client.get(f"/api/manual/contents/{doc_id}", variant=variant, engine=engine,
                      path=path or [])


def job_values(client: AppClient, doc_id: str, kind: str, pages: list[str],
               variant: int | None = None, engine: str | None = None) -> list[dict]:
    return client.get(f"/api/values/{doc_id}", kind=kind, pages=pages,
                      variant=variant, engine=engine)


def fluids(client: AppClient, doc_id: str, variant: int | None = None,
           engine: str | None = None) -> dict:
    if variant is None and not engine:
        return {"note": "Pick a variant (find_vehicle) or engine first -- a series' engines "
                        "take different amounts."}
    out = {"fluids": client.get(f"/api/fluids/{doc_id}", variant=variant, engine=engine)}
    try:
        out["engine_oil"] = client.get(f"/api/wis/oil/{doc_id}", variant=variant, engine=engine)
    except AppError:
        pass   # not a WIS car
    if not out["fluids"]:
        out["capacity_pages"] = client.get(f"/api/fluids/{doc_id}/pages",
                                           variant=variant, engine=engine)
    return out


def labour_times(client: AppClient, doc_id: str, variant: int | None = None,
                 engine: str | None = None, query: str = "", book: str | None = None,
                 group: str | None = None) -> dict:
    return client.get(f"/api/wis/labour/{doc_id}", variant=variant, engine=engine, q=query,
                      book=book, browse=not query, group=group)


def service_schedule(client: AppClient, doc_id: str, variant: int | None = None,
                     engine: str | None = None) -> dict:
    return client.get(f"/api/wis/service/{doc_id}", variant=variant, engine=engine)


def bulletins(client: AppClient, doc_id: str, variant: int | None = None,
              engine: str | None = None) -> list[dict]:
    return client.get(f"/api/wis/bulletins/{doc_id}", variant=variant, engine=engine)


def history(client: AppClient, limit: int = 20) -> list[dict]:
    return client.get("/api/history", limit=max(1, min(limit, 200)))


def history_entry(client: AppClient, entry_id: int) -> dict:
    return client.get(f"/api/history/{int(entry_id)}")


# --- MCP wiring -------------------------------------------------------------------

def build_server(client: AppClient):
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations

    srv = MCPServer("autorundown", instructions=INSTRUCTIONS)
    ro = ToolAnnotations(read_only_hint=True, open_world_hint=False)

    def tool(fn):
        def run(*a, **k) -> str:
            # one JSON text block per call (a list would come back as one
            # block per item)
            try:
                out = fn(client, *a, **k)
            except AppError as e:
                out = {"error": str(e)}
            return json.dumps(out, ensure_ascii=False, separators=(",", ":"))
        run.__name__, run.__doc__ = fn.__name__, fn.__doc__
        import inspect
        sig = inspect.signature(fn)
        run.__signature__ = sig.replace(parameters=list(sig.parameters.values())[1:],
                                        return_annotation=str)
        run.__annotations__ = {**{k: v for k, v in fn.__annotations__.items()
                                  if k not in ("client", "return")}, "return": str}
        srv.tool(annotations=ro, structured_output=False)(run)
        return fn

    find_vehicle.__doc__ = (
        "Find a car in the library by model, model code, variant, engine, year, VIN or FIN "
        "(e.g. '2001 E 320', 'W210', 'M112', 'F-150 2015'). Returns doc_id and each "
        "variant's index, engines and Baumuster: pass doc_id and variant to the other tools.")
    search_manual.__doc__ = (
        "Keyword search in one car's manuals (the app's own search, filtered to the car). "
        "tab: 'workshop' (manual pages), 'parts' (parts catalogue lines), 'torque' or "
        "'capacity' (pages printing such values, with those lines). Use the manual's words; "
        "shop words are translated too. Returns page_no + title per hit.")
    read_page.__doc__ = ("A manual page's full text, its links and cross-references "
                         "(page numbers you can read next). Cite doc_id + page_no.")
    manual_contents.__doc__ = ("The car's manual as a table of contents, one level at a time: "
                               "pass the section names so far in `path` to go down.")
    job_values.__doc__ = ("The torque (kind='torque') or capacity (kind='capacity') values of a "
                          "job, read from the given pages and the procedures they link to, in "
                          "this car's column only. pages: page_no values from search/read_page.")
    fluids.__doc__ = "The car's fluids and engine oil capacity, for a chosen variant or engine."
    labour_times.__doc__ = (
        "Flat-rate labour times (WIS; LEMON manuals' own tables). With `query`: the job's "
        "times. Without: the book by group (pass `group` to open one). A variant is needed; "
        "if the car is in several books, they come back -- pass `book`.")
    service_schedule.__doc__ = "The car's ASSYST service schedule (WIS; needs a variant or engine)."
    bulletins.__doc__ = "The car's service bulletins (WIS), newest first, with what each replaces."
    history.__doc__ = "Questions asked in the app before, newest first (answers are kept whole)."
    history_entry.__doc__ = "One stored question and its answer, by id from `history`."

    for fn in (find_vehicle, search_manual, read_page, manual_contents, job_values, fluids,
               labour_times, service_schedule, bulletins, history, history_entry):
        tool(fn)
    return srv


def main() -> None:
    client = AppClient(os.environ.get("AUTORUNDOWN_URL", DEFAULT_URL))
    build_server(client).run("stdio")


if __name__ == "__main__":
    sys.exit(main())
