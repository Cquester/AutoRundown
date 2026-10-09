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

import anthropic
from pydantic import BaseModel, Field

from .search import multi_search, scope_ids

# Prices are USD per million tokens. `adaptive`/`effort` track which request
# parameters each model accepts -- passing adaptive thinking or an effort
# setting to a model that predates them is a 400, not a silent no-op.
MODELS: dict[str, dict] = {
    "claude-opus-5": {
        "label": "Opus 5 — most capable",
        "price_in": 5.0, "price_out": 25.0,
        "adaptive": True, "effort": True,
    },
    "claude-sonnet-5": {
        "label": "Sonnet 5 — balanced",
        "price_in": 3.0, "price_out": 15.0,
        "adaptive": True, "effort": True,
    },
    "claude-haiku-4-5": {
        "label": "Haiku 4.5 — cheapest",
        "price_in": 1.0, "price_out": 5.0,
        "adaptive": False, "effort": False,
    },
}
DEFAULT_MODEL = "claude-opus-5"


def model_spec(model: str) -> dict:
    return MODELS.get(model, MODELS[DEFAULT_MODEL])


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
    answer: str = Field(description="Direct answer in plain language. Empty if not found.")
    specs: list[SpecItem] = Field(
        default_factory=list,
        description="Only actual values read off the page. Never a "
                    "cross-reference like 'see Fig 15'."
    )
    steps: list[str] = Field(
        default_factory=list,
        description="Ordered procedure steps, when the question asks how to "
                    "do something. One action per entry, in order. Empty if "
                    "the question only asked for a value."
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
- If the passages do not contain the answer, set found=false and leave answer
  empty. Saying "not in the archive" is a correct and useful response. Do not
  substitute general automotive knowledge.
- Prefer torque-specification tables over prose mentions when both appear.
- Keep `answer` to a couple of sentences. The other fields carry the content;
  do not restate them at length.

Putting things in the right field:
- `specs` is ONLY for values you can read off the page -- a torque, a
  capacity, a clearance. A cross-reference is not a value: never write a spec
  whose value is "see Fig 15" or "refer to the illustration". If the manual
  answers with a picture, use `figures` instead.
- `figures` is for diagrams needed to act on the answer: tightening
  sequences, component locations, routing. Copy the filename exactly from a
  [diagram: ...] marker in the passages. If a tightening sequence is asked
  for and the manual gives it as a figure, that figure is the answer -- put
  it here and say so plainly in `answer`.
- `steps` is for ordered procedures, when the question asks how to do
  something. One action per entry, in the manual's order, keeping any torque
  value inside the step it belongs to. Leave empty when only a value was
  asked for."""


@dataclass
class Usage:
    model: str = DEFAULT_MODEL
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def add(self, response) -> None:
        self.input_tokens += response.usage.input_tokens
        self.output_tokens += response.usage.output_tokens
        self.calls += 1

    @property
    def cost_usd(self) -> float:
        spec = model_spec(self.model)
        return (self.input_tokens / 1e6 * spec["price_in"]
                + self.output_tokens / 1e6 * spec["price_out"])

    def as_dict(self) -> dict:
        return {"model": self.model,
                "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens,
                "calls": self.calls,
                "cost_usd": round(self.cost_usd, 5)}


@dataclass
class Result:
    answer: AnswerModel
    passages: list[dict] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    dropped_citations: list[str] = field(default_factory=list)
    # specs the model gave for another engine / model of the series
    other_vehicle_specs: list[dict] = field(default_factory=list)

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
            "figures": [f.model_dump() for f in self.answer.figures],
            "source_pages": self.answer.source_pages,
            "page_titles": self.page_titles,
            "diagrams": self.answer.diagrams,
            "caveats": self.answer.caveats,
            "queries": self.queries,
            "dropped_citations": self.dropped_citations,
            "other_vehicle_specs": self.other_vehicle_specs,
            "usage": self.usage.as_dict(),
            "passages": [
                {"page_no": p["page_no"], "title": p["title"],
                 "section_path": p["section_path"],
                 "applicability": p.get("applicability", "this_vehicle")}
                for p in self.passages
            ],
        }


class NoApiKey(RuntimeError):
    pass


class ApiUnavailable(RuntimeError):
    """An API call failed for a reason the user can act on."""


def _friendly_api_error(exc: Exception) -> ApiUnavailable:
    """Turn SDK exceptions into something readable above a fender.

    Ordered most specific first; the balance case is special-cased because it
    is by far the most common first-run failure and the fix is not obvious
    (API credit is billed separately from a Claude subscription).
    """
    if isinstance(exc, anthropic.AuthenticationError):
        return ApiUnavailable(
            "The API key was rejected. Check it in Settings, or create a new "
            "one at https://console.anthropic.com -> API Keys.")
    if isinstance(exc, anthropic.RateLimitError):
        return ApiUnavailable("Rate limited by the API. Wait a moment and retry.")
    if isinstance(exc, anthropic.BadRequestError):
        detail = str(exc)
        if "credit balance" in detail.lower():
            return ApiUnavailable(
                "Out of API credit. The key is valid, but the account has no "
                "API balance. Note that API credit is separate from a Claude "
                "Pro/Max subscription -- top up at "
                "https://console.anthropic.com/settings/billing.")
        return ApiUnavailable(f"The API rejected the request: {detail}")
    if isinstance(exc, anthropic.APIConnectionError):
        return ApiUnavailable(
            "Could not reach the Anthropic API. Check the network connection.")
    if isinstance(exc, anthropic.APIStatusError):
        return ApiUnavailable(f"API error {exc.status_code}: {exc}")
    return ApiUnavailable(f"Unexpected API failure: {exc}")


def _client(api_key: str) -> anthropic.Anthropic:
    if not api_key:
        raise NoApiKey(
            "No Anthropic API key configured. Set it on the Settings page or "
            "export ANTHROPIC_API_KEY."
        )
    return anthropic.Anthropic(api_key=api_key)


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


def plan_queries(client, question: str, vehicle: dict, usage: Usage,
                 model: str = DEFAULT_MODEL) -> list[str]:
    spec = model_spec(model)
    kwargs: dict = {}
    if spec["effort"]:
        # planning is a light task; keep it cheap where the model allows it
        kwargs["output_config"] = {"effort": "low"}
    try:
        response = client.messages.parse(
            model=model,
            max_tokens=1000,
            system=PLANNER_SYSTEM,
            messages=[{
                "role": "user",
                "content": f"Vehicle: {_vehicle_label(vehicle)}\nQuestion: {question}",
            }],
            output_format=SearchPlan,
            **kwargs,
        )
    except anthropic.APIError as exc:
        raise _friendly_api_error(exc) from None
    usage.add(response)
    plan = response.parsed_output
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


def format_passages(passages: list[dict], max_chars: int = 60000) -> str:
    """Render retrieved chunks for the model, tagged with their page numbers."""
    blocks, total = [], 0
    for p in passages:
        header = (f"[page {p['page_no']}] {p['title']}\n"
                  f"section: {p['section_path']}")
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

    # a claimed answer with no surviving citation is not trustworthy
    if (answer.found and not answer.source_pages and not answer.specs
            and not answer.figures and not answer.steps):
        answer.found = False
        note = ("Answer withheld: the model did not cite a page from the "
                "retrieved passages.")
        answer.caveats = f"{answer.caveats} {note}".strip()
        answer.answer = ""
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


def ask(conn: sqlite3.Connection, vehicle: dict, question: str, api_key: str,
        per_query: int = 8, cap: int = 18,
        include_other_variants: bool = False,
        model: str = DEFAULT_MODEL, source: str = "cli",
        variant: int | None = None, engine: str | None = None,
        year: int | None = None, user_id: int | None = None,
        user_name: str | None = None) -> Result:
    """Answer one question, recording what it cost.

    `source` tags the spend as web/cli/eval so a bill can be attributed later;
    `user_id`/`user_name` tag it with the signed-in mechanic, if any.
    """
    usage = Usage(model=model)
    try:
        return _ask(conn, vehicle, question, api_key, per_query, cap,
                    include_other_variants, model, usage, variant, engine, year)
    finally:
        # log even when the run failed partway -- a failed second call still
        # billed for the first one
        if usage.calls:
            from .index import log_usage
            log_usage(conn, source, vehicle["doc_id"], question, usage,
                      user_id, user_name)


def _ask(conn: sqlite3.Connection, vehicle: dict, question: str, api_key: str,
         per_query: int, cap: int, include_other_variants: bool,
         model: str, usage: Usage, variant: int | None = None,
         engine: str | None = None, year: int | None = None) -> Result:
    client = _client(api_key)
    # tell the model which car within the generation, not just "W212 E-Class"
    vehicle = narrowed(vehicle, variant, engine, year)
    spec = model_spec(model)

    queries = plan_queries(client, question, vehicle, usage, model)
    # the manuals' own wording first: the planner wrote "valve cover ..." for
    # a 190E 2.3-16 and WIS only says "cylinder head cover" (synonyms.py)
    from .synonyms import translate
    queries = list(dict.fromkeys([a for a in (translate(q) for q in queries) if a] + queries))
    scope = scope_ids(vehicle, variant, engine)
    passages = multi_search(conn, scope, queries,
                            per_query=per_query, cap=cap,
                            include_other_variants=include_other_variants)

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
    answer_kwargs: dict = {}
    if spec["adaptive"]:
        answer_kwargs["thinking"] = {"type": "adaptive"}
    try:
        response = client.messages.parse(
            model=model,
            max_tokens=8000,
            system=ANSWER_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=AnswerModel,
            **answer_kwargs,
        )
    except anthropic.APIError as exc:
        raise _friendly_api_error(exc) from None
    usage.add(response)

    if response.stop_reason == "refusal":
        return Result(
            answer=AnswerModel(found=False, answer="",
                               caveats="The request was declined by the model."),
            passages=passages, queries=queries, usage=usage,
        )

    answer = response.parsed_output or AnswerModel(
        found=False, answer="", caveats="The model returned no parsable answer.")
    dropped = _verify_citations(answer, passages)
    other = _drop_other_vehicles(answer, vehicle_codes(vehicle, scope))

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
                  usage=usage, dropped_citations=dropped, other_vehicle_specs=other)
