"""Claude query layer.

Two calls per question:

1. Turn the natural-language question into keyword queries the FTS index can
   actually match. This is what replaces a vector store here -- manual text is
   dense with exact terms ("coil-on-plug", "anchor plate"), and letting the
   model choose the search terms beats embedding 20k chunks.
2. Answer strictly from the retrieved passages, with a page citation on every
   figure.

Citations are then *verified against the retrieved set* rather than trusted.
A confident wrong torque value is worse than no answer, so a citation the
retrieval never returned is dropped and the answer is marked unverified.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from . import providers
from .providers import ApiUnavailable, NoApiKey  # noqa: F401  (callers catch query.X)
from .search import as_ids, multi_search, scope_ids

# Anthropic's models (providers.PROVIDERS has every provider's); kept here
# under the old name for callers and tests
MODELS: dict[str, dict] = providers.PROVIDERS["anthropic"]["models"]
DEFAULT_MODEL = providers.PROVIDERS["anthropic"]["default_model"]


def model_spec(model: str) -> dict:
    return providers.model_spec(model)


class SearchPlan(BaseModel):
    queries: list[str] = Field(
        description="2-4 keyword queries using the terminology a factory "
                    "service manual would use. No punctuation or operators."
    )


class SpecItem(BaseModel):
    name: str = Field(description="What the figure is, e.g. 'Caliper bolt torque'")
    value: str = Field(
        description="Value with the units exactly as printed in the manual. "
                    "Include every unit the manual prints and no others. "
                    "Never compute a conversion."
    )
    applicability: str = Field(
        description="Exact vehicle/engine/trim this applies to, with the codes "
                    "the page prints: 'M112 (112.941)', 'model 210.282', "
                    "'all engines'"
    )
    source_pages: list[str] = Field(
        description="Pages this same value is printed on. The first is the "
                    "primary source; add others only when they print the "
                    "identical value."
    )


class Figure(BaseModel):
    """A diagram that *is* part of the answer, not just decoration.

    Tightening sequences, component locations and routing are published as
    figures; the text only points at them. Without this the model has nowhere
    to put "the sequence is in Fig 15" except a spec field, where a
    cross-reference gets displayed as though it were a torque value.
    """
    caption: str = Field(description="What this figure shows, in your own words")
    image: str = Field(
        description="The exact filename from a [diagram: ...] marker in the "
                    "passage. Copy it verbatim; do not invent one."
    )
    page: str = Field(description="Page the figure appears on")


class AnswerModel(BaseModel):
    found: bool = Field(description="False if the archive does not contain the answer")
    answer: str = Field(description="One to three plain sentences. Empty if not found.")
    document_page: str = Field(
        default="",
        description="The one page to open next to the answer: the manual's "
                    "procedure for the job, or the spec table when only a "
                    "value was asked for. Must be a page from the passages."
    )
    specs: list[SpecItem] = Field(
        default_factory=list,
        description="Only actual values read off the page. Never a "
                    "cross-reference like 'see Fig 15'."
    )
    steps: list[str] = Field(
        default_factory=list,
        description="Leave empty. The procedure is shown from the manual "
                    "page itself (document_page), not retold."
    )
    figures: list[Figure] = Field(
        default_factory=list,
        description="Diagrams needed to act on the answer -- tightening "
                    "sequences, component locations, routing."
    )
    source_pages: list[str] = Field(default_factory=list)
    diagrams: list[str] = Field(default_factory=list,
                                description="Image filenames referenced by the cited pages")
    caveats: str = Field(default="", description="Applicability warnings or ambiguity")


PLANNER_SYSTEM = """You turn a mechanic's question into keyword queries for a \
full-text index of a factory service manual.

Rules:
- Use the vocabulary the manual uses, not the vocabulary drivers use.
  "oil change" -> "engine oil capacity", "oil filter removal".
  "brake pads" -> "brake pads removal", "front disc brake".
- Include the component name and the kind of data wanted (torque,
  specification, capacity, removal, installation, diagram).
- 2-4 queries, each 2-5 words. Keywords only: no quotes, AND/OR, or wildcards.
- Do not include the make, model, or year: the index is already scoped to one
  vehicle."""

ANSWER_SYSTEM = """You answer repair questions using ONLY the manual passages \
provided. You are used by people working on a real vehicle, where a wrong \
torque value causes a failed repair or an unsafe one.

Hard rules:
- Use only the supplied passages. Never use recalled knowledge about this
  vehicle, and never estimate or interpolate a value.
- Every spec value must carry the page it came from.
- Copy values exactly as printed, keeping every unit the manual gives and all
  stages ("Stage 1: 62 lb.in (7 Nm), Stage 2: 50 degrees").
- NEVER CONVERT UNITS. If a page prints only lb-ft, report only lb-ft. Do not
  compute the Nm equivalent, do not round, and do not write things like
  "which is approximately". A number you calculated is not a manual spec.
- Answer every part of the question. A question with two parts ("how much oil
  and what's the drain plug torque") needs both answered, or the missing part
  reported as not found. Beyond that, do not volunteer specs for nearby
  fasteners nobody asked about.
- ONE ENTRY PER DISTINCT FASTENER. This archive prints the same spec on
  several pages. If the same value for the same fastener appears more than
  once, give a single spec entry and list the extra pages in `source_pages`.
  Never emit the same value twice as separate entries.
- Only call it a conflict when the SAME UNIT shows DIFFERENT numbers (say,
  8 Nm on one page and 12 Nm on another). Then report both, say plainly that
  the manual disagrees, and name the pages in `caveats`. Never silently pick
  one.
- Different units are NOT a conflict. If one page prints 8 Nm and another
  prints 6 lb ft for the same fastener, those are the same specification
  written two ways. Give one entry, put both printed values in `value`
  ("8 Nm; 6 lb ft as printed on p2826"), cite both pages, and do not describe
  the manual as inconsistent. Still do not compute the conversion yourself or
  write "approximately" -- report only what each page prints.
- Check applicability. Passage titles often name a specific engine, trim, or
  sub-assembly (for example "2.7L EcoBoost", "Transmission 722.3"). If it
  differs from the vehicle in the request, do not present it as the answer;
  say so in `caveats`.
- ONLY THIS VEHICLE'S VALUES. The vehicle line names the exact car: its model
  codes, its engine (and exact engine code), its body when known. Pages often
  print one table for many engines or models -- a row per engine ("M112",
  "M113", "OM613", "engine 112.941") or per model code ("210.082"). Give only
  the rows for this vehicle. Leave rows for other engines, models or bodies
  out entirely: do not list them as alternatives or "for reference". A row
  that names no engine or model applies to all. If the passages only give
  the value for other engines, report that part as not found and name the
  engines they covered in `caveats`.
- SHOP WORDS ARE NOT MANUAL WORDS. Mechanics name parts in shop slang or
  another maker's terms: "cam magnet" is the camshaft (adjuster) solenoid,
  "valve cover" the cylinder head cover, "water pump" the coolant pump, "lug
  nut" the wheel bolt, "serpentine belt" the poly-V-belt. When a passage
  covers the same part under the manual's name, it IS the answer: answer
  from it and say in `answer` what the manual calls the part. A question
  worded as past work ("replaced the cam magnets") still wants that job's
  procedure and values. This is matching names, not recalled knowledge --
  every value still comes from the passages.
- If the passages do not contain the answer, set found=false and leave answer
  empty. Saying "not in the archive" is a correct and useful response. Do not
  substitute general automotive knowledge.
- Prefer torque-specification tables over prose mentions when both appear.
- PART NUMBERS come only from pages marked "(parts catalogue)". Each part
  line there is: callout | part number | name | fits | notes. "Fits" lists
  model codes ("210.065") on a car's pages and engine codes ("102.983") on
  an engine's pages. Give only the part numbers whose "fits" covers this
  vehicle's model code or its exact engine code (or says "all models"), with
  the quantity it gives. When several current numbers fit -- different
  types or versions of the part (a standard and a heavy-duty version, two
  designs) -- list every one with its name; do not pick one. A number marked
  "replaced by" is
  superseded -- give the replacement. Put each in `specs` (name = the part,
  value = the number as printed). Never take a part number from a repair
  page, and never compose one.
- A part line's notes may carry footnotes ("fn 003: up to engine 32946334
  (change date 2015-06-11)"; a long one is listed under "Footnotes:" at the
  end of the page). Most say from or up to which chassis, engine or
  transmission number (or build date) a part applies: "up to engine 32946334"
  means engines numbered up to 32946334. The car's serial numbers are not
  known here, so when parts differ only by such a footnote, give each with
  its condition in `applicability` and say in `answer` which number to check
  (the engine number on the block, or the chassis number from the VIN).
- Keep `answer` to one to three sentences. You are pointing the mechanic at
  the right manual page, not retelling it: the page in `document_page` is
  shown in full next to your answer.

Putting things in the right field:
- `specs` is ONLY for values you can read off the page -- a torque, a
  capacity, a clearance. A cross-reference is not a value: never write a spec
  whose value is "see Fig 15" or "refer to the illustration". If the manual
  answers with a picture, use `figures` instead.
- `document_page` is the single page to open beside the answer. For "how do
  I ..." it is the page with the procedure for that job; for "what is the
  torque / capacity ..." it is the page with that value's table. Pick the
  page for this vehicle, not a sibling engine's.
- For a procedure, `specs` lists the torque values (and other values) the
  procedure uses, so they can be read at a glance; the steps themselves
  stay on the page. For a value-only question, `specs` holds just that value.
- Leave `steps` empty: do not retell the procedure.
- `figures` only when a diagram on a page OTHER than `document_page` is
  needed (a tightening sequence printed elsewhere). Copy the filename exactly
  from a [diagram: ...] marker in the passages."""


@dataclass
class Usage:
    model: str = DEFAULT_MODEL
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    provider: str = "anthropic"

    def add(self, response) -> None:
        self.input_tokens += response.usage.input_tokens
        self.output_tokens += response.usage.output_tokens
        self.calls += 1

    @property
    def cost_usd(self) -> float | None:
        """None when this model's price is not known ("cost not tracked")."""
        return providers.cost(self.model, self.input_tokens, self.output_tokens)

    def as_dict(self) -> dict:
        c = self.cost_usd
        return {"model": self.model,
                "provider": self.provider,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "calls": self.calls,
                "cost_usd": round(c, 5) if c is not None else None}


@dataclass
class Result:
    answer: AnswerModel
    passages: list[dict] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    dropped_citations: list[str] = field(default_factory=list)
    # specs the model gave for another engine / model of the series
    other_vehicle_specs: list[dict] = field(default_factory=list)
    # current parts for this car the model left out, added by _add_omitted_parts
    added_parts: list[dict] = field(default_factory=list)
    added_linked: list[dict] = field(default_factory=list)

    @property
    def page_titles(self) -> dict[str, str]:
        """page number -> what that page is.

        Looked up from the retrieved passages rather than asked of the model:
        it costs no tokens and cannot be hallucinated, and a citation reading
        "Valve Cover: Installation" beats a bare page number.
        """
        return {str(p["page_no"]): p["title"] for p in self.passages}

    def as_dict(self) -> dict:
        return {
            "found": self.answer.found,
            "answer": self.answer.answer,
            "specs": [s.model_dump() for s in self.answer.specs],
            "steps": self.answer.steps,
            "document_page": self.answer.document_page,
            "figures": [f.model_dump() for f in self.answer.figures],
            "source_pages": self.answer.source_pages,
            "page_titles": self.page_titles,
            "diagrams": self.answer.diagrams,
            "caveats": self.answer.caveats,
            "queries": self.queries,
            "dropped_citations": self.dropped_citations,
            "other_vehicle_specs": self.other_vehicle_specs,
            "added_parts": self.added_parts,
            "added_linked": self.added_linked,
            "usage": self.usage.as_dict(),
            "passages": [
                {"page_no": p["page_no"], "title": p["title"],
                 "section_path": p["section_path"],
                 "applicability": p.get("applicability", "this_vehicle")}
                for p in self.passages
            ],
        }


def narrowed(vehicle: dict, variant: int | None, engine: str | None,
             year: int | None = None) -> dict:
    """The vehicle with its chosen car / engine / year spelled out, which
    _vehicle_label shows. Without it the model saw only the generation and
    answered for all ten W212 engines at once."""
    import json as _json
    try:
        variants = _json.loads(vehicle.get("variants") or "[]")
    except ValueError:
        return vehicle
    fams = [f.strip() for f in (engine or "").split(",") if f.strip()]
    labels = {}
    for x in variants:
        labels.update(x.get("engine_families") or {})
    out = dict(vehicle)
    if year:
        out["year_range"] = str(year)
    codes = ""
    if variant is not None and 0 <= variant < len(variants):
        x = variants[variant]
        # exact codes, so the model can see "except 102.983" on a page and
        # know it means this car (the 190E 2.3-16 was answered from one)
        ebm = x.get("engine_by_bm") or {}
        bms = [b for b in x.get("baumuster", [])
               if not fams or not ebm.get(b) or any(str(e)[:3] in fams for e in ebm[b])]
        engs = [e for e in x.get("engines", []) if not fams or str(e)[:3] in fams]
        out["model"] = f"{vehicle.get('model')} -- {x.get('name')} ({', '.join(bms)})"
        codes = ", ".join(engs)
        fams = fams or sorted((x.get("engine_families") or {}).keys())
    elif fams:
        cars = sorted({x["name"] for x in variants
                       if any(str(e)[:3] in fams for e in x.get("engines", []))})
        out["model"] = (f"{vehicle.get('model')} -- models: {', '.join(cars[:8])}"
                        + (" ..." if len(cars) > 8 else ""))
    if fams:
        out["engine"] = (" or ".join(labels.get(f, f"engine {f}") for f in fams)
                         + (f"; exact engine code {codes}" if codes else ""))
    return out


def _vehicle_label(vehicle: dict) -> str:
    bits = [vehicle.get("year_range"), vehicle.get("make"), vehicle.get("model")]
    label = " ".join(b for b in bits if b)
    engine = vehicle.get("engine")
    return f"{label} (engine: {engine})" if engine else label


# DeepSeek's JSON mode takes an example instead of a schema (providers.py)
PLAN_EXAMPLE = {"queries": ["engine oil capacity", "oil drain plug torque"]}
ANSWER_EXAMPLE = {
    "found": True, "answer": "One to three sentences.",
    "document_page": "<page number from the passages>",
    "specs": [{"name": "<what the value is>", "value": "<value as printed>",
               "applicability": "<engine / model codes>",
               "source_pages": ["<page number from the passages>"]}],
    "steps": [], "figures": [],
    "source_pages": ["<page number from the passages>"],
    "diagrams": [], "caveats": ""}


def plan_queries(call, question: str, vehicle: dict, usage: Usage) -> list[str]:
    """`call(kind, system, user, schema, example)` -> providers.Reply."""
    reply = call("plan", PLANNER_SYSTEM,
                 f"Vehicle: {_vehicle_label(vehicle)}\nQuestion: {question}",
                 SearchPlan, PLAN_EXAMPLE)
    usage.add(reply)
    plan = reply.parsed
    queries = [q.strip() for q in (plan.queries if plan else []) if q.strip()]
    # always keep the raw question as a fallback query
    if question.strip() not in queries:
        queries.append(question.strip())
    return queries


import re as _re

# Cross-reference markers are for the reader, not the model: showing them
# would invite citing a page whose content was never retrieved, which the
# citation check would then have to drop.
_LINK_MARK_RE = _re.compile(r"\[\[ref:\d+::([^\]]*)\]\]")


def strip_link_marks(text: str) -> str:
    return _LINK_MARK_RE.sub(r"\1", text or "")


def table_codes(vehicle: dict, scope) -> dict | None:
    """The car's codes as wis_tables.label_fits checks a table column, or
    None with neither a car nor an engine chosen (every column then)."""
    vc = vehicle_codes(vehicle, scope)
    if not vc:
        return None
    models, engines = getattr(scope, "codes", ((), ())) or ((), ())
    return {"engines": set(engines), "models": set(models),
            "series": vc["series"], "families": set(getattr(scope, "engines", ()) or ())}


# The procedures and value tables the top pages link to, added to the
# passages before the one answer call (2026-10-17, the user's request): the
# W210 oil level sensor page says "remove lower part of oil pan" and links
# that procedure, whose bolts (14 / 10 Nm) the answer never saw -- search
# only finds pages that share the question's words. No extra AI call; the
# links are WIS's own (wis_links), in the car's scope only.
LINKED_TOP = 3          # top pages whose links are followed
LINKED_MAX = 4          # linked pages added
LINKED_CHARS = 5000     # of each linked page's text
LINKED_AFTER = 6        # placed after this many passages, inside the budget
LINKED_PREFIXES = ("AR", "BA", "BF")   # procedures, torques, capacities


_STOP = {"the", "and", "for", "how", "what", "replace", "replacement", "replaced", "remove",
          "removal", "install", "installation", "torque", "torques", "specification", "spec",
          "specs", "capacity", "check", "with", "from", "engine", "vehicle"}


def _fits_car(page: dict, scope) -> bool:
    """The page filters search applies in SQL (search._filters), for a page
    reached by a link: not limited to other engines, not excepting this car's
    codes, not naming only other codes."""
    fams = tuple(getattr(scope, "engines", ()) or ())
    engines = page.get("engines") or ""
    if fams and engines and not any(f",{f}," in engines for f in fams):
        return False
    models, ecodes = getattr(scope, "codes", ((), ())) or ((), ())
    exc, only = page.get("codes_except") or "", page.get("codes_only") or ""
    for group in (models, ecodes):
        if group and exc and all(f",{c}," in exc for c in group):
            return False
    mine = (*models, *ecodes)
    if mine and only and not any(f",{c}," in only for c in mine):
        return False
    return page.get("applicability", "this_vehicle") == "this_vehicle"


def with_linked(conn: sqlite3.Connection, vehicle: dict, scope,
                passages: list[dict], queries: list[str] | None = None) -> list[dict]:
    """`passages` plus the WIS documents their top pages link to. Each added
    passage leads with its tables' values for this car (wis_tables: each
    value with its own row, the car's column) -- the page text flattens a
    table so a value sits beside the next row's name."""
    from pathlib import Path

    from . import wis_links, wis_tables
    if not _re.match(r"mercedes-wis", vehicle.get("source_name") or "") \
            or not vehicle.get("file_path"):
        return passages
    corpus = Path(vehicle["file_path"])
    # only pages about the question: a stray hit ("Remove/install rear axle
    # shaft" for an oil level sensor) linked in three exhaust procedures
    words = {w for q in (queries or []) for w in _re.findall(r"[a-z0-9]{3,}", q.lower())} - _STOP
    top, seen = [], set()
    for p in passages:
        if p.get("doc_type") == "parts" or p["page_no"] in seen:
            continue
        seen.add(p["page_no"])
        if words and not words & set(_re.findall(r"[a-z0-9]{3,}", (p.get("title") or "").lower())):
            continue
        row = conn.execute("SELECT * FROM pages WHERE page_uid=?", (p["page_uid"],)).fetchone()
        if row:
            top.append(dict(row))
        if len(top) >= LINKED_TOP:
            break
    wis_ids = [r[0] for r in conn.execute(
        "SELECT doc_id FROM vehicles WHERE file_path=?", (vehicle["file_path"],))]
    ids = as_ids(scope)
    try:
        linked = wis_links.linked_pages(conn, corpus, top, ids, wis_ids,
                                        LINKED_PREFIXES, LINKED_MAX)
    except sqlite3.Error:
        return passages
    codes = table_codes(vehicle, scope)
    have = {str(p["page_no"]) for p in passages}
    extra = []
    for page, via in linked:
        if str(page["page_no"]) in have or not _fits_car(page, scope):
            continue
        values = []
        if str(page["page_no"]).isdigit():
            for kind in ("torque", "capacity"):
                values += wis_tables.rows(corpus, int(page["page_no"]), kind, codes)
        head = ""
        if values:
            head = ("Values for this vehicle from this page's tables:\n" + "\n".join(
                f"- {v['name']}: {v['value']}" + (f" (only for: {v['condition']})" if v["condition"] else "")
                for v in values) + "\n\n")
        extra.append({**page, "body": head + (page.get("text") or "")[:LINKED_CHARS],
                      "linked_from": via, "score": 0.0,
                      "linked_values": [v for v in values if v["value"].endswith(("Nm", "°"))]})
    if not extra:
        return passages
    return passages[:LINKED_AFTER] + extra + passages[LINKED_AFTER:]


def format_passages(passages: list[dict], max_chars: int = 60000) -> str:
    """Render retrieved chunks for the model, tagged with their page numbers."""
    blocks, total = [], 0
    for p in passages:
        kind = " (parts catalogue)" if p.get("doc_type") == "parts" else ""
        header = (f"[page {p['page_no']}]{kind} {p['title']}\n"
                  f"section: {p['section_path']}")
        if p.get("linked_from"):
            header += (f"\nlinked from page {p['linked_from']}: a procedure or table "
                       "that page refers to")
        if p.get("applicability") != "this_vehicle":
            header += "\nWARNING: this page documents a different variant/trim"
        block = f"{header}\n---\n{strip_link_marks(p['body'])}"
        if total + len(block) > max_chars:
            break
        blocks.append(block)
        total += len(block)
    return "\n\n========\n\n".join(blocks)


def _verify_citations(answer: AnswerModel, passages: list[dict]) -> list[str]:
    """Drop any citation that retrieval never returned.

    The model is instructed to cite, but instruction is not proof: this checks
    each cited page against the pages actually supplied.
    """
    available = {str(p["page_no"]) for p in passages}
    dropped: list[str] = []

    kept_specs = []
    for spec in answer.specs:
        real = [p for p in map(str, spec.source_pages) if p in available]
        bogus = [p for p in map(str, spec.source_pages) if p not in available]
        for p in bogus:
            dropped.append(f"spec '{spec.name}' cited page {p}")
        if real:
            spec.source_pages = real
            kept_specs.append(spec)
    answer.specs = kept_specs

    kept_pages = []
    for page in answer.source_pages:
        if str(page) in available:
            kept_pages.append(page)
        else:
            dropped.append(f"source page {page}")
    answer.source_pages = kept_pages

    # figures are citations too: only images the retrieval actually returned
    known_images: set[str] = set()
    for p in passages:
        known_images.update(i for i in (p.get("images") or "").split("\n") if i)
    kept_figs = []
    for fig in answer.figures:
        if fig.image in known_images:
            kept_figs.append(fig)
        else:
            dropped.append(f"figure '{fig.caption}' referenced {fig.image}")
    answer.figures = kept_figs

    # the page opened beside the answer is a citation like any other; an
    # invented one falls back to the first real page the answer cites
    if answer.document_page and str(answer.document_page) not in available:
        dropped.append(f"document page {answer.document_page}")
        answer.document_page = ""
    if answer.document_page and str(answer.document_page) not in map(str, answer.source_pages):
        answer.source_pages.insert(0, answer.document_page)
    if not answer.document_page:
        first = answer.source_pages or [p for s in answer.specs for p in s.source_pages]
        answer.document_page = str(first[0]) if first else ""

    # a claimed answer with no surviving citation is not trustworthy. Steps
    # carry no citation of their own, so they never count as one: a procedure
    # whose cited pages were all invented is withheld with the rest (the
    # user's rule, 2026-10-11: only answers with sources behind them)
    if (answer.found and not answer.source_pages and not answer.specs
            and not answer.figures):
        answer.found = False
        note = ("Answer withheld: the model did not cite a page from the "
                "retrieved passages.")
        answer.caveats = f"{answer.caveats} {note}".strip()
        answer.answer = ""
        answer.steps = []
        answer.document_page = ""
    return dropped


# Engine and model codes in a spec's applicability text. "M112", "OM 613",
# "engine(s) 112, 113", and dotted codes "112.941" / "210.082" -- a dotted
# code is an engine when its family is a known engine and not this car's
# series (the W123 and the M123 share a number). Three digits, so bolt sizes
# ("M12x1.5") never match.
_ENGINE_TAG_RE = _re.compile(r"\b(?:OM|M)\s?(\d{3})\b")
_ENGINE_LIST_RE = _re.compile(
    r"\bengines?\s+(\d{3}(?:\.\d{3})?(?:\s*(?:,|/|and|or|&)\s*\d{3}(?:\.\d{3})?)*)", _re.I)
_DOTTED_RE = _re.compile(r"\b(\d{3})\.(\d{3})\b")
_EXCEPT_RE = _re.compile(r"\bexcept\b|\bexcluding\b|\bnot (?:for|on|with)\b|\bwithout\b", _re.I)


def mentioned_codes(text: str, engine_families: set[str],
                    series: set[str]) -> tuple[set[str], set[str]]:
    """(engine families, model codes of `series`) named in `text`."""
    engines = set(_ENGINE_TAG_RE.findall(text or ""))
    for m in _ENGINE_LIST_RE.finditer(text or ""):
        engines.update(c[:3] for c in _re.findall(r"\d{3}(?:\.\d{3})?", m.group(1)))
    models = set()
    for fam, rest in _DOTTED_RE.findall(text or ""):
        if fam in series:
            models.add(f"{fam}.{rest}")
        elif fam in engine_families:
            engines.add(fam)
    return engines - series, models


def vehicle_codes(vehicle: dict, scope) -> dict | None:
    """What a spec's applicability is checked against, or None to check nothing.

    Only once a car or an engine is chosen: with the whole generation picked,
    every engine's value is a fair answer.
    """
    import json as _json
    ours = set(getattr(scope, "engines", ()) or ())
    bms = set((getattr(scope, "codes", ((), ())) or ((), ()))[0])
    if not ours and not bms:
        return None
    try:
        variants = _json.loads(vehicle.get("variants") or "[]")
    except ValueError:
        variants = []
    series = {b[:3] for v in variants for b in v.get("baumuster", [])}
    families = {str(e)[:3] for v in variants for e in v.get("engines", [])}
    try:
        from .importers.records import SOURCE_ENGINES, curated_engines
        for source in SOURCE_ENGINES:
            families.update(curated_engines(source))
    except Exception:  # reference table missing: the series' own engines still count
        pass
    return {"engines": ours, "models": bms, "series": series,
            "families": {f for f in families if f.isdigit()} - series}


def _drop_other_vehicles(answer: AnswerModel, codes: dict | None) -> list[dict]:
    """Remove specs whose applicability names only other engines or models.

    The user picked a 2001 E 320 4MATIC wagon and got torque values for every
    W210 engine: a page that lists M112, M113 and OM613 rows passes the engine
    filter (it names the M112 too), and the model copied every row. The
    prompt says to keep only this car's rows; this checks it, as the citation
    check does. A spec naming at least one of the car's engines (or codes),
    or none at all, stays. Removed specs go back to the UI, collapsed, never
    silently.
    """
    if not codes:
        return []
    kept, gone = [], []
    for spec in answer.specs:
        text = f"{spec.applicability} {spec.name}"
        engines, models = mentioned_codes(text, codes["families"], codes["series"])
        why = ""
        if _EXCEPT_RE.search(text):
            pass    # "all except M113" names an engine it is NOT for: keep
        elif codes["engines"] and engines and not engines & codes["engines"]:
            why = "engine " + ", ".join(sorted(engines))
        elif codes["models"] and models and not models & codes["models"]:
            why = "model " + ", ".join(sorted(models))
        if why:
            gone.append({**spec.model_dump(), "reason": f"for {why}"})
        else:
            kept.append(spec)
    answer.specs = kept
    if gone and not kept and not answer.steps and not answer.figures:
        # every value was for another car: the prose would still quote them
        answer.found = False
        answer.answer = ""
        answer.caveats = ("The pages found give this value only for other engines "
                          "or models of this series (shown below, for reference). "
                          + answer.caveats).strip()
    elif gone:
        note = (f"{len(gone)} value{'s' if len(gone) > 1 else ''} for other engines "
                f"or models of this series removed.")
        answer.caveats = f"{answer.caveats} {note}".strip()
    return gone


_PART_LINE_RE = _re.compile(
    r"^[^|\n]*\|\s*(?P<spaced>[A-Z]\s?[0-9][0-9 ]*[0-9])\s*\((?P<compact>[A-Z][0-9]+)[^)]*\)\s*\|"
    r"\s*(?P<name>[^|]*)\|\s*(?P<fits>[^|]*)\|(?P<notes>.*)$")
_CODE_RE = _re.compile(r"\b\d{3}\.\d{3}\b")
MAX_ADDED_PARTS = 6


def _add_omitted_parts(answer: AnswerModel, passages: list[dict], scope) -> list[dict]:
    """Add current part numbers that fit this car and that the answer left out.

    Two paid answers (2026-10-15) gave only the pencil-type glow plug for a
    C 220 CDI and left out the standard one, A 001 159 66 01 ("qty 4, all
    models"), which the passage showed -- the second time despite a prompt
    line saying to list every current number. Like the citation and
    other-vehicle checks, code makes sure.

    Looks only at parts pages the answer cites (their passage is already the
    page's lines matching the question), and only once a car or engine is
    chosen. A line counts when it is current (no "replaced by") and fits this
    car: "all models", or one of its exact model or engine codes (or, with
    only an engine chosen, a code of that engine). Each one added cites the
    page it is on, a page retrieval returned, so the citation rule holds.
    """
    model_codes = set((getattr(scope, "codes", ((), ())) or ((), ()))[0])
    engine_codes = set((getattr(scope, "codes", ((), ())) or ((), ()))[1])
    families = set(getattr(scope, "engines", ()) or ())
    if not (model_codes or engine_codes or families):
        return []
    cited = {str(p) for p in answer.source_pages} | {str(answer.document_page)}
    for sp in answer.specs:
        cited.update(str(x) for x in sp.source_pages)
    said = _re.sub(r"\s+", "", " ".join(
        [answer.answer, answer.caveats] + [f"{s.name} {s.value}" for s in answer.specs])).upper()

    def fits_car(fits: str) -> bool:
        if "all models" in fits.lower():
            return True
        codes = set(_CODE_RE.findall(fits))
        if codes & (model_codes | engine_codes):
            return True
        # only an engine family chosen (no exact variant): a code of that engine
        return not (model_codes or engine_codes) and any(c[:3] in families for c in codes)

    added: list[dict] = []
    for p in passages:
        page = str(p["page_no"])
        if p.get("doc_type") != "parts" or page not in cited:
            continue
        for line in (p.get("body") or "").split("\n"):
            m = _PART_LINE_RE.match(line.strip())
            if not m or "replaced by" in m["notes"].lower() or not fits_car(m["fits"]):
                continue
            compact, digits = m["compact"].upper(), m["compact"][1:]
            if compact in said or digits in said or any(a["compact"] == compact for a in added):
                continue
            added.append({"compact": compact, "page": page, "spec": SpecItem(
                name=f"{m['name'].strip()} (also listed for this car)",
                value=f"{m['spaced'].strip()} ({compact})",
                applicability=" ".join(x for x in (m["fits"].strip(), m["notes"].strip().strip("; ")) if x),
                source_pages=[page])})
            if len(added) >= MAX_ADDED_PARTS:
                break
    if added:
        answer.specs.extend(a["spec"] for a in added)
        listed = "; ".join(f"{a['spec'].value} {a['spec'].name.replace(' (also listed for this car)', '')}"
                           f" (page {a['page']})" for a in added)
        answer.caveats = (f"{answer.caveats} The parts catalogue also lists for this car, "
                          f"not given above: {listed}. Check which one your car takes.").strip()
    return [{**a["spec"].model_dump(), "page": a["page"]} for a in added]


MAX_ADDED_LINKED = 8


def _add_linked_torques(answer: AnswerModel, passages: list[dict]) -> list[dict]:
    """Add the torques of a cited page's linked procedures that the answer
    left out -- checked after the model, like _add_omitted_parts.

    The W210 oil level sensor page links "Remove/install bottom part of oil
    pan"; with that page in the passages and a prompt line asking for its
    torques, Haiku still gave the sensor and drain screw only (2026-10-17,
    twice). The values come from the linked page's own tables in this car's
    column (wis_tables, `linked_values`), each citing that page."""
    cited = {str(answer.document_page), *map(str, answer.source_pages)}
    for s in answer.specs:
        cited.update(map(str, s.source_pages))
    cited.discard("")

    def words(t):
        return set(_re.findall(r"[a-z]{4,}", t.lower()))
    added = []
    for p in passages:
        if not p.get("linked_from") or str(p["linked_from"]) not in cited:
            continue
        for v in p.get("linked_values") or []:
            num = v["value"].split()[0]
            if any(num in s.value and len(words(v["name"]) & words(s.name)) >= 2
                   for s in answer.specs):
                continue           # given already ("Oil drain screw ... 30 Nm")
            spec = SpecItem(name=v["name"], value=v["value"],
                            applicability=v.get("condition") or "",
                            source_pages=[str(p["page_no"])])
            answer.specs.append(spec)
            added.append({**spec.model_dump(), "page": str(p["page_no"]),
                          "title": p.get("title") or ""})
            if len(added) >= MAX_ADDED_LINKED:
                break
    if added:
        pages = "; ".join(dict.fromkeys(f"{a['title']} (page {a['page']})" for a in added))
        answer.caveats = (f"{answer.caveats} Added from the procedure this job links to: "
                          f"{pages}.").strip()
    return added


def ask(conn: sqlite3.Connection, vehicle: dict, question: str, api_key: str,
        per_query: int = 8, cap: int = 18,
        include_other_variants: bool = False,
        model: str = DEFAULT_MODEL, source: str = "cli",
        variant: int | None = None, engine: str | None = None,
        year: int | None = None, user_id: int | None = None,
        user_name: str | None = None, key_ref: str | None = None,
        provider: str | None = None) -> Result:
    """Answer one question, recording what it cost.

    `provider` is the AI provider ("anthropic", "gemini", "xai", "deepseek";
    providers.py); by default the one `model` belongs to. Whichever answers,
    the reply goes through the same checks: _verify_citations,
    _drop_other_vehicles, _add_omitted_parts.

    `source` tags the spend as web/cli/eval so a bill can be attributed later;
    `user_id`/`user_name` tag it with the signed-in mechanic, if any;
    `key_ref` with whose key paid (config.pick_key: "company" | "user:<id>").
    """
    provider = provider or providers.provider_of(model) or providers.DEFAULT_PROVIDER
    usage = Usage(model=model, provider=provider)
    try:
        return _ask(conn, vehicle, question, api_key, per_query, cap,
                    include_other_variants, model, usage, variant, engine, year)
    finally:
        # log even when the run failed partway -- a failed second call still
        # billed for the first one
        if usage.calls:
            from .index import log_usage
            log_usage(conn, source, vehicle["doc_id"], question, usage,
                      user_id, user_name, key_ref)


def _ask(conn: sqlite3.Connection, vehicle: dict, question: str, api_key: str,
         per_query: int, cap: int, include_other_variants: bool,
         model: str, usage: Usage, variant: int | None = None,
         engine: str | None = None, year: int | None = None) -> Result:
    if not api_key:
        raise NoApiKey(providers.no_key_message(usage.provider))

    def call(kind, system, user, schema, example):
        return providers.call(usage.provider, api_key, model, kind, system, user,
                              schema, example)

    # tell the model which car within the generation, not just "W212 E-Class"
    vehicle = narrowed(vehicle, variant, engine, year)

    queries = plan_queries(call, question, vehicle, usage)
    # the manuals' own wording first: the planner wrote "valve cover ..." for
    # a 190E 2.3-16 and WIS only says "cylinder head cover" (synonyms.py)
    from .synonyms import translations
    queries = list(dict.fromkeys([a for q in queries for a in translations(q)] + queries))
    scope = scope_ids(vehicle, variant, engine)
    passages = multi_search(conn, scope, queries,
                            per_query=per_query, cap=cap,
                            include_other_variants=include_other_variants)
    passages = with_linked(conn, vehicle, scope, passages, [question, *queries])

    if not passages:
        return Result(
            answer=AnswerModel(
                found=False, answer="",
                caveats="Nothing in the index matched this question."),
            queries=queries, usage=usage,
        )

    prompt = (
        f"Vehicle: {_vehicle_label(vehicle)}\n"
        f"Question: {question}\n\n"
        f"Manual passages:\n\n{format_passages(passages)}"
    )
    reply = call("answer", ANSWER_SYSTEM, prompt, AnswerModel, ANSWER_EXAMPLE)
    usage.add(reply)

    if reply.refusal:
        return Result(
            answer=AnswerModel(found=False, answer="",
                               caveats="The request was declined by the model."),
            passages=passages, queries=queries, usage=usage,
        )

    answer = reply.parsed or AnswerModel(
        found=False, answer="", caveats="The model returned no parsable answer.")
    dropped = _verify_citations(answer, passages)
    other = _drop_other_vehicles(answer, vehicle_codes(vehicle, scope))
    added = _add_omitted_parts(answer, passages, scope) if answer.found else []
    linked = _add_linked_torques(answer, passages) if answer.found else []

    # surface diagrams that belong to the pages actually cited
    cited = set(answer.source_pages)
    for sp in answer.specs:
        cited.update(str(x) for x in sp.source_pages)
    diagrams: list[str] = []
    for p in passages:
        if str(p["page_no"]) in cited and p.get("images"):
            diagrams.extend(i for i in p["images"].split("\n") if i)
    answer.diagrams = list(dict.fromkeys(diagrams))[:12]

    return Result(answer=answer, passages=passages, queries=queries,
                  usage=usage, dropped_citations=dropped, other_vehicle_specs=other,
                  added_parts=added, added_linked=linked)
