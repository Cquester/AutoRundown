"""The values the right box's Torque specs and Capacities tabs show (free).

Two lists, both for the chosen car only:

- `job_values`: the values on the pages of *this job* -- the answer's own
  pages (after an Ask) or the top search hits (after a Search), plus, for
  WIS, the procedures and value tables those pages link to ("Remove lower
  part of oil pan" -> its own page, with the oil pan bolts). Before
  2026-10-16 the tabs ran their own keyword search with "torque" or
  "capacity" added, and showed whatever matched: for "replace oil level
  sensor" on a W210, transmission pages ("torque converter"), ATF, rear
  axle and level-control fluids, and a value next to the wrong part.
- `fluids`: the car's main fluids from WIS's capacity sheets (BF..-P-1000),
  each with its capacity in this car's column and, for engine oil, the
  specification sheets the table names (MB 229.x) -- the user's "capacity
  button for the car".

WIS pages are read from their own tables (wis_tables.py); other manuals,
and WIS pages without a value table, from their text lines (search.py's
value_lines / value_row, which leave out lines naming only other engines).
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from . import query, wis_links, wis_tables
from .search import as_ids, get_page, value_lines, value_row

# linked documents that hold a job's values: repair procedures (AR),
# tightening torques (BA), capacities (BF), test values (BE); not safety
# notes (AS), sealant notes (AH) or operating-fluid sheets (BB)
LINKED_PREFIXES = ("AR", "BA", "BF", "BE")
MAX_PAGES = 6        # pages asked for
MAX_LINKED = 10      # linked pages followed, in all


table_codes = query.table_codes


def _is_wis(vehicle: dict) -> bool:
    return bool(re.match(r"mercedes-wis", vehicle.get("source_name") or ""))


def _text_rows(conn, page: dict, kind: str, other_engine) -> list[dict]:
    lines = value_lines(conn, page["page_uid"], kind, "", cap=40, other_engine=other_engine)
    return [{**value_row(l, kind, page.get("title") or ""), "condition": ""} for l in lines]


def _other_engine(vehicle: dict, scope):
    codes = query.vehicle_codes(vehicle, scope)

    def other(line: str) -> bool:
        if not codes:
            return False
        engs, models = query.mentioned_codes(line, codes["families"], codes["series"])
        return bool((codes["engines"] and engs and not engs & codes["engines"])
                    or (codes["models"] and models and not models & codes["models"]))
    return other


def _page_rows(conn, vehicle, scope, page: dict, kind: str, codes, sheet_title) -> list[dict]:
    """One page's values of `kind` for the car: its tables when WIS has them."""
    if _is_wis(vehicle) and str(page["page_no"]).isdigit():
        corpus = Path(vehicle["file_path"])
        if wis_tables.has_tables(corpus, int(page["page_no"])):
            return wis_tables.rows(corpus, int(page["page_no"]), kind, codes,
                                   sheet_title=sheet_title)
    return _text_rows(conn, page, kind, _other_engine(vehicle, scope))


def _sheet_title(conn, vehicle, ids, corpus_ids):
    corpus = Path(vehicle["file_path"])
    cache: dict[str, str] = {}

    def title(docnr: str) -> str:
        if docnr not in cache:
            pages = wis_links.pages_for(conn, corpus, docnr, ids, corpus_ids)
            t = pages[0]["title"] if pages else ""
            cache[docnr] = wis_tables.sheet_name(t) or t
        return cache[docnr]
    return title


def job_values(conn: sqlite3.Connection, vehicle: dict, scope, pages: list[str],
               kind: str, corpus_ids: list[str]) -> list[dict]:
    """[{name, value, condition, spec?, page_no, title, via}] -- `via` is the
    page that linked to it, "" for a page asked for."""
    ids = as_ids(scope)
    codes = table_codes(vehicle, scope)
    sheet = _sheet_title(conn, vehicle, ids, corpus_ids) if _is_wis(vehicle) else None
    todo: list[tuple[dict, str]] = []
    seen_pages: set[str] = set()
    for p in list(dict.fromkeys(str(p) for p in pages if p))[:MAX_PAGES]:
        page = get_page(conn, ids, p)
        if page and page["page_no"] not in seen_pages:
            seen_pages.add(page["page_no"])
            todo.append((page, ""))
    if _is_wis(vehicle):
        todo += wis_links.linked_pages(conn, Path(vehicle["file_path"]),
                                       [p for p, _ in todo], ids, corpus_ids,
                                       LINKED_PREFIXES, MAX_LINKED)
    out, seen = [], set()
    for page, via in todo:
        for r in _page_rows(conn, vehicle, scope, page, kind, codes, sheet):
            key = (r["name"].lower(), r["value"])
            if not r["value"] or key in seen:
                continue
            seen.add(key)
            out.append({**r, "page_no": page["page_no"], "title": page.get("title") or "",
                        "via": via})
    return out


# the order a mechanic reads a fluids list in; anything else after these
_FLUID_ORDER = [r"engine oil|engine lubrication", r"cool", r"automatic transmission|\batf\b",
                r"manual transmission|complete transmission|gear oil", r"transfer case",
                r"differential|rear axle|final.drive", r"front axle", r"steering", r"brake",
                r"level control|suspension", r"air condition|refrigerant", r"fuel", r"washer"]
_SKIP_SHEETS = re.compile(r"oil consumption|oil level check|mixture ratio", re.I)


def _fluid_rank(text: str) -> int:
    for i, rx in enumerate(_FLUID_ORDER):
        if re.search(rx, text, re.I):
            return i
    return len(_FLUID_ORDER)


def fluids(conn: sqlite3.Connection, vehicle: dict, scope, corpus_ids: list[str]) -> list[dict]:
    """The car's fluids from WIS's capacity sheets: [{system, name, value,
    condition, spec?, page_no}]. Only with a car or engine chosen, and only
    columns that name this car (a sheet filed under the whole generation
    lists every engine's figure side by side)."""
    if not _is_wis(vehicle):
        return []
    codes = table_codes(vehicle, scope)
    if not codes:
        return []
    corpus = Path(vehicle["file_path"])
    sheets = wis_links.capacity_sheets(corpus)
    if not sheets:
        return []
    ids = as_ids(scope)
    general = getattr(scope, "general", "")
    sheet = _sheet_title(conn, vehicle, ids, corpus_ids)
    q = (f"SELECT doc_id, page_no, title, vehicle_types FROM pages WHERE doc_id IN "
         f"({','.join('?' * len(ids))}) AND page_no IN ({','.join('?' * len(sheets))})")
    rows = conn.execute(q, [*ids, *sheets]).fetchall()
    types = {t for t in (getattr(scope, "types", "") or "").split(",") if t}
    out, seen = [], set()
    for doc_id, page_no, title, vtypes in rows:
        if _SKIP_SHEETS.search(title or ""):
            continue
        strict = doc_id == general
        if strict and types and not types & {t for t in (vtypes or "").split(",") if t}:
            continue           # a truck's or a van's sheet in the general pool
        for r in wis_tables.rows(corpus, int(page_no), "capacity", codes,
                                 sheet_title=sheet, strict=strict):
            # grease for joints and hub caps (grams) is not a fluid to check;
            # refrigerant is
            if re.search(r"\s(?:g|kg)$", r["value"]) and not re.search(
                    r"refrigerant|R134|R12\b|R1234", r["name"], re.I):
                continue
            key = (r["name"].lower(), r["value"])
            if key in seen:
                continue
            seen.add(key)
            out.append({**r, "system": title or "", "page_no": page_no})
    out.sort(key=lambda r: (_fluid_rank(f"{r['system']} {r['name']}"), r["system"]))
    return out


# a page's title says it gives a capacity: "(oil capacity)", "capacities";
# not the air conditioner's cooling or heating capacity tests
_CAPACITY_TITLE = re.compile(r"capacit", re.I)
_NOT_CAPACITY = re.compile(r"cooling capacity|refrigerat|heating capacity|capacity modified|delivery capacit|"
                           r"circuit diagram|light transporters|trucks|vans\b|unimog", re.I)
_TITLE_MODELS = re.compile(r"\bModels?\s+((?:\d{3}[\s,/and]*)+)", re.I)


def capacity_pages(conn: sqlite3.Connection, vehicle: dict, scope, limit: int = 8) -> list[dict]:
    """For a car WIS has no capacity sheet for (the W201): the pages of its
    own manual and its engines' whose title says they give a capacity
    ("0030 - Removal and installation of the engine (oil capacity) Engine
    102 KE in model 201"), as links -- their tables are often flattened
    ("5.5 / 5.0" under two names), so the value is read on the page, never
    guessed from it (2026-11-04). The general pool is left out (other
    cars' "Capacities: Passenger cars: …" sheets), and so is a page whose
    title names models that are not this car's."""
    if not _is_wis(vehicle):
        return []
    ids = [d for d in as_ids(scope) if d != getattr(scope, "general", "")]
    if not ids:
        return []
    series = str(vehicle.get("model_code") or "")
    rows = conn.execute(
        f"SELECT DISTINCT page_no, title FROM pages WHERE doc_id IN ({','.join('?' * len(ids))}) "
        f"AND title LIKE '%capacit%' AND applicability != 'other_variant' ORDER BY page_no",
        ids).fetchall()
    out, seen = [], set()
    for page_no, title in rows:
        title = re.sub(r"\s{2,}", " — ", (title or "").strip())
        if not _CAPACITY_TITLE.search(title) or _NOT_CAPACITY.search(title) or page_no in seen:
            continue
        named = {m for g in _TITLE_MODELS.findall(title) for m in re.findall(r"\d{3}", g)}
        if named and series not in named:
            continue
        seen.add(page_no)
        out.append({"page_no": str(page_no), "title": title})
    # the engine's own page (oil) first, as the fluids card leads with oil
    out.sort(key=lambda p: (0 if re.search(r"oil capacit", p["title"], re.I) else _fluid_rank(p["title"]),
                            p["page_no"]))
    return out[:limit]


# Mercedes special tools are numbered "NNN 589 NN NN 00" (589: the tools
# group). A WIS job names its tools in a "Special tools" block, inline
# ("*110589035900") and as figure names ("Figures: 112589007200a"); older
# pages (the W201's) show them as pictures only, so the figure name is the
# one key every page has. Each tool has a sheet titled by its number
# ("110 589 03 59 00 Mounting wedge"; 4,694 on the live index).
_TOOL_RE = re.compile(r"(?<![\dA-Za-z])(\d{3}) ?(589) ?(\d{2}) ?(\d{2}) ?(\d{2})(?!\d)")
_SHEET_GLOB = "[0-9][0-9][0-9] 589 [0-9][0-9] [0-9][0-9] [0-9][0-9]*"
_sheets: dict = {}


def _tool_sheets(conn: sqlite3.Connection, ids: list[str]) -> dict[str, tuple[str, str, str]]:
    """{"110589035900": (page_no, doc_id, name)} for the scope's tool sheets
    (cached per scope and page count: one title scan, ~1 s on the live
    index)."""
    n = conn.execute("SELECT MAX(rowid) FROM pages").fetchone()[0]
    key = (tuple(ids), n)
    if key not in _sheets:
        if len(_sheets) > 32:
            _sheets.clear()
        out: dict[str, tuple[str, str, str]] = {}
        for page_no, doc_id, title in conn.execute(
                f"SELECT page_no, doc_id, title FROM pages WHERE doc_id IN ({','.join('?' * len(ids))}) "
                f"AND title GLOB ?", [*ids, _SHEET_GLOB]):
            out.setdefault(title[:16].replace(" ", ""), (str(page_no), doc_id, title[16:].strip()))
        _sheets[key] = out
    return _sheets[key]


def _printed(num: str) -> str:
    return f"{num[:3]} {num[3:6]} {num[6:8]} {num[8:10]} {num[10:12]}"


def job_tools(conn: sqlite3.Connection, vehicle: dict, scope, pages: list[str],
              corpus_ids: list[str]) -> list[dict]:
    """The special tools of a job: [{number, name, page_no, doc_id, on}] --
    every tool number on the job's pages and the WIS procedures they link
    to (as job_values reads them), named from its tool sheet (page_no ""
    when the sheet isn't in scope; the name then from the page's own
    "Special tools" line, if any). WIS only. `on` is the job page naming it.
    For the Torque specs tab (agent 6's G4, 2026-11-04)."""
    if not _is_wis(vehicle):
        return []
    ids = as_ids(scope)
    todo: list[dict] = []
    seen_pages: set[str] = set()
    for p in list(dict.fromkeys(str(p) for p in pages if p))[:MAX_PAGES]:
        page = get_page(conn, ids, p)
        if page and page["page_no"] not in seen_pages:
            seen_pages.add(page["page_no"])
            todo.append(page)
    todo += [pg for pg, _ in wis_links.linked_pages(conn, Path(vehicle["file_path"]),
                                                    list(todo), ids, corpus_ids, ("AR",), MAX_LINKED)]
    sheets = _tool_sheets(conn, ids)
    out, seen = [], set()
    for page in todo:
        text = page.get("text") or ""
        for m in _TOOL_RE.finditer(text):
            num = "".join(m.groups())
            if num in seen:
                continue
            seen.add(num)
            sheet = sheets.get(num)
            name = sheet[2] if sheet else ""
            if not name:
                # only a "Special tools" line: the number starting it, a
                # short name after (in running text it is the next step)
                line = re.search(r"^" + re.escape(_printed(num)) + r"[ \t]+([A-Za-z][^\n.()]{1,58})$",
                                 text, re.M)
                name = line.group(1).strip() if line else ""
            out.append({"number": _printed(num), "name": name,
                        "page_no": sheet[0] if sheet else "", "doc_id": sheet[1] if sheet else "",
                        "on": page["page_no"]})
    return out
