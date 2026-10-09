"""Importer for a normalized import corpus (`records.jsonl` + `manifest.json`).

This is the AutoRundown side of the import spec that ships with the extractor
tooling (`extractor/IMPORT_SPEC.md`). The contract is deliberately
source-agnostic: an adapter -- today the Mercedes WIS VirtualBox extractor,
tomorrow another OEM database or a folder of PDFs -- writes one JSON record per
line plus a provenance manifest, and this importer is all AutoRundown needs to
read any of them. A new source adds an adapter over there, not code here.

Layout (a corpus directory, or several under one root):

    <corpus dir>/manifest.json     provenance + counts (spec section 4)
    <corpus dir>/records.jsonl     one normalized record per line (spec section 3)

Grouping -- `--importer records` (default) or `--importer records:model`:

    corpus  one vehicle per (source, language). Every document is reachable.
    model   one vehicle per Mercedes model family named in the applicability
            notes (MODEL 176 -> "176"), plus one bucket for documents that
            state no model at all.

`corpus` is the default because roughly half the WIS documents carry no model
statement -- those are the general procedures, and under `model` they are only
reachable by selecting the no-model bucket. Model codes go into `section_path`
either way, which BM25 weights above the body, so naming the model in a
question still pulls that model's documents up. Which default is right is a
measurement question; see the notes in CLAUDE.md.
"""
from __future__ import annotations

import collections
import csv
import functools
import json
import os
import re
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .base import Importer, PageRecord, VehicleRecord, register

SUPPORTED_SCHEMA_MAJOR = "1"

# The source_type in the manifest is a stable adapter id; the make is the only
# knowledge this side has to hold, and only for display.
SOURCE_MAKES = {"mercedes-wis-virtualbox": "Mercedes-Benz"}

# Mercedes' own vehicle type (FzgArt, via variants.jsonl) -> the Make shown in
# the picker. WIS covers cars, vans, trucks, buses and Unimogs in one corpus;
# listing 70 truck series among the cars is what made the picker unusable.
TYPE_MAKES = {
    "CAR": "Mercedes-Benz", "4xWD": "Mercedes-Benz",
    "Light transporter": "Mercedes-Benz Vans",
    "Heavy transporter": "Mercedes-Benz Vans",
    "TRUCK": "Mercedes-Benz Trucks", "BUS": "Mercedes-Benz Buses",
    "UNIMOG": "Unimog", "smart": "smart",
}
# ...and when the type is unknown, the division (ASRA SparteAbk)
DIVISION_MAKES = {"car": "Mercedes-Benz", "commercial": "Mercedes-Benz Commercial"}

# Production years, maintained BY HAND -- reference knowledge, not source
# data. The WIS database has no usable production years: scored against 46
# series with known years, its W_Baumusterzeile spans hid 6 cars (the W126
# from 1979 showed as 1983, the database's documentation epoch) and padded the
# rest by ~17 years; ASRA's "ProduktionVon" is a chassis-number range. So the
# picker's years come from this table. A series with no entry gets no year and
# is shown under every year -- never hidden. Add a row when a model is added.
SOURCE_YEARS = {"mercedes-wis-virtualbox":
                Path(__file__).parent / "data" / "mercedes_years.csv"}


def _curated_rows(source: str) -> list[dict]:
    path = SOURCE_YEARS.get(source)
    if not path or not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f) if (r.get("series") or "").strip()]


def curated_years(source: str) -> dict[str, str]:
    """series -> "1979-1991" (or "2014+" while in production), for a source."""
    out: dict[str, str] = {}
    for r in _curated_rows(source):
        a, b = (r.get("year_from") or "").strip(), (r.get("year_to") or "").strip()
        if a:
            out[r["series"].strip()] = f"{a}-{b}" if b else f"{a}+"
    return out


def curated_names(source: str) -> dict[str, str]:
    """series -> the name people know it by ("W126 S-Class"), same table.

    Some models span several series (the Vario is 667-670, the T1N Sprinter
    901-905); a shared name gets its series number, or the picker shows four
    identical "Vario" entries.
    """
    names = {r["series"].strip(): r["known_as"].strip()
             for r in _curated_rows(source) if (r.get("known_as") or "").strip()}
    shared = {n for n, k in collections.Counter(names.values()).items() if k > 1}
    return {s: f"{n} ({s})" if n in shared else n for s, n in names.items()}


SOURCE_ENGINES = {"mercedes-wis-virtualbox":
                  Path(__file__).parent / "data" / "mercedes_engines.csv"}


def curated_engines(source: str) -> dict[str, str]:
    """engine family ("272") -> "M272 · V6 petrol", for a source.

    WIS names engines only by number. The picker groups a generation's
    variants by engine -- the one thing a variant changes in what is searched
    -- and a bare "272" means nothing to most people. Reference knowledge,
    like the year table; a family with no row keeps its number.
    """
    path = SOURCE_ENGINES.get(source)
    if not path or not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        rows = csv.DictReader(line for line in f if not line.startswith("#"))
        return {r["family"].strip(): f"{r['name'].strip()} · {r['description'].strip()}"
                for r in rows if (r.get("family") or "").strip()}


SOURCE_US_YEARS = {"mercedes-wis-virtualbox":
                   Path(__file__).parent / "data" / "mercedes_us_years.csv"}


def curated_us_years(source: str) -> dict[str, tuple[str, str]]:
    """Baumuster -> (US model years "2010-2011,2015", NHTSA trims).

    Built once by tools/build_us_years.py from NHTSA vPIC and committed, so
    the site never calls NHTSA. Good for 2000s cars; loose (a few years too
    early) for 1980s ones -- extra years, never a hidden car.
    """
    path = SOURCE_US_YEARS.get(source)
    if not path or not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        return {r["baumuster"]: (r["us_years"], r.get("us_trims", ""))
                for r in csv.DictReader(f) if r.get("baumuster")}


def with_us_years(variant: dict, us: dict[str, tuple[str, str]]) -> dict:
    """Attach {Baumuster: "2010-2011"} and the NHTSA trim names, when known."""
    years = {b: us[b][0] for b in variant.get("baumuster", []) if b in us}
    if not years:
        return variant
    trims = sorted({t.strip() for b in years for t in us[b][1].split(";") if t.strip()})
    return {**variant, "us_years": years, "us_trims": trims}


def with_engine_labels(variant: dict, labels: dict[str, str]) -> dict:
    """Attach {family: label} for the variant's engines ("272": "M272 · V6 petrol")."""
    fams = {}
    for e in variant.get("engines", []):
        f = str(e)[:3]
        if f.isdigit():
            fams[f] = labels.get(f, f"Engine {f}")
    return {**variant, "engine_families": fams} if fams else variant


# display order when one sales name is split by body
BODY_ORDER = ["Sedan", "Wagon", "Coupe", "Convertible", "Roadster", "Shooting Brake"]


def split_by_body(variant: dict) -> list[dict]:
    """One variant per body style, when its Baumuster span several.

    WIS sells the W210 sedan (210.082) and wagon (210.282) under the one
    name "E 320 4MATIC", so the picker could not tell a wagon from a sedan;
    the user has a wagon. Split, the wagon is "E 320 4MATIC Wagon" with only
    its own Baumuster -- its exact codes (Scope.codes), US years and VIN
    prefixes follow, and the answer model is told the body. A Baumuster of
    unknown body goes with every split (a wider code list only filters less).
    `body_by_bm` comes from parse_vehicles.py (ASRA_Typkennzahl); without it
    nothing changes.
    """
    bodies = variant.get("body_by_bm") or {}
    bms = variant.get("baumuster") or []
    known = sorted({bodies[b] for b in bms if bodies.get(b)},
                   key=lambda b: (BODY_ORDER.index(b) if b in BODY_ORDER else 99, b))
    if len(known) < 2:
        whole = bool(bms) and all(bodies.get(b) for b in bms)
        return [{**variant, "body": known[0]} if whole and known else variant]
    unknown = [b for b in bms if not bodies.get(b)]
    ebm = variant.get("engine_by_bm") or {}
    out = []
    for body in known:
        mine = sorted([b for b in bms if bodies.get(b) == body] + unknown)
        mine_ebm = {b: e for b, e in ebm.items() if b in mine}
        # engines from the Baumuster that have them; all of them if any lacks
        engines = (sorted({e for b in mine for e in mine_ebm.get(b, [])})
                   if all(mine_ebm.get(b) for b in mine) else variant.get("engines", []))
        out.append({**variant, "name": f"{variant['name']} {body}", "body": body,
                    "baumuster": mine, "engine_by_bm": mine_ebm, "engines": engines,
                    "body_by_bm": {b: bodies[b] for b in mine if bodies.get(b)}})
    return out


PASSENGER_TYPES = {"CAR", "4xWD", "smart"}


def series_division(variants: list[dict]) -> str:
    """Majority Mercedes division of a series: "car", "commercial" or "".

    ASRA's SparteAbk decides; for the few series only AW_Fahrzeuge knows (no
    division -- the O 355 / O 457 buses of series 372), the vehicle type does.
    """
    votes = collections.Counter(v["division"] for v in variants if v.get("division"))
    if votes:
        return votes.most_common(1)[0][0]
    types = collections.Counter(
        "car" if v["vehicle_type"] in PASSENGER_TYPES else "commercial"
        for v in variants if v.get("vehicle_type"))
    return types.most_common(1)[0][0] if types else ""


def series_make(variants: list[dict], default: str) -> str:
    """The Make for a series: majority vehicle type, else majority division."""
    for key, table in (("vehicle_type", TYPE_MAKES), ("division", DIVISION_MAKES)):
        votes = collections.Counter(table[v[key]] for v in variants
                                    if v.get(key) in table)
        if votes:
            return votes.most_common(1)[0][0]
    return default

# "MODEL 901.6, 902.6" / "611.### as of 359084 in MODELS 202, 203, 210".
# Only codes *after* a MODEL/MODELS label are models -- a bare leading code
# like 611.### is an engine number.
MODEL_BLOCK_RE = re.compile(r"\bMODELS?\b[\s:]*([0-9][0-9.,\s#/]*)", re.I)
# "ENGINE 102.983", "ENGINES 111.920/940, 104.991": the same list shapes.
# Engine buckets get their own namespace ("E102") -- engine and vehicle numbers
# share one space (OM651 vs truck series 651), and a shared bucket would put a
# truck's pages in front of a car.
ENGINE_BLOCK_RE = re.compile(r"\bENGINES?\b[\s:]*([0-9][0-9.,\s#/]*)", re.I)
ENGINE_PREFIX = "E"
FAMILY_RE = re.compile(r"\b(\d{3})(?:\.\d+)?")
DOTTED_RE = re.compile(r"\d{3}\.\d")

WIRING_HINTS = ("wiring diagram", "connector", "harness", "component location",
                "pin assignment")
TSB_HINTS = ("service information", "service bulletin", "recall", "campaign",
             "overview of innovations")
SPEC_HINTS = ("specification", "capacit", "fluid", "torque", "tightening",
              "checklist", "acceptance protocol", "maintenance")


@dataclass
class Tally:
    """Where every line of a records file went.

    The point is that the arithmetic closes: `seen` == `kept` + `figure_only`
    + `malformed`, and `seen` == the manifest's `record_count`. Without this,
    "are we missing records?" has no answer, and a truncated file looks
    exactly like a smaller corpus.
    """
    seen: int = 0          # non-blank lines
    kept: int = 0          # became a PageRecord
    figure_only: int = 0   # no body text, graphics not in this corpus
    malformed: int = 0     # line was not valid JSON -- real data loss

    def line(self) -> str:
        bits = [f"{self.seen} records = {self.kept} indexed"]
        if self.figure_only:
            bits.append(f"{self.figure_only} figure-only")
        if self.malformed:
            bits.append(f"{self.malformed} MALFORMED")
        return " + ".join(bits)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


# UTF-8 text that was decoded as Windows-1252 somewhere upstream: "é" arrives
# as "Ã©", "Ü" as "Ãœ", the diameter sign "Ø" as "Ã˜". On the English WIS
# corpus 12,751 document bodies carry it -- "Ã˜ 10 mm" in a spec -- and so do
# ~1,000 vehicle names. A run is a lead byte (Â..ô) plus continuation bytes,
# each shown as its cp1252 character.
_MOJIBAKE = re.compile(
    "[Â-ô]"
    "[\u0080-¿ŒœŠšŸŽžƒˆ˜"
    "–—‘-„†-•…‰‹›€™]+")


def _unmangle(m: re.Match) -> str:
    run = m.group(0)
    try:
        # per character: cp1252 for its own glyphs ("€" -> 0x80), Latin-1 for
        # the rest. WIS has both forms -- 10,296 pages carry "–" as "â" plus
        # raw C1 controls (\x80\x93), which a plain cp1252 encode rejects.
        raw = bytes(ord(c) if ord(c) < 256 else c.encode("cp1252")[0] for c in run)
        return raw.decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return run          # not a valid UTF-8 sequence: leave it alone


def fix_mojibake(text: str) -> str:
    """Undo UTF-8-read-as-cp1252, and only that.

    A run is rewritten only if its bytes form valid UTF-8, so correctly
    encoded text ("Coupé", "Größe", "20 °C") is never touched -- a lone
    accented letter has no continuation run to match.
    """
    if not text or text.isascii():
        return text
    return _MOJIBAKE.sub(_unmangle, text)


# The catalogue comes from a 1990s database and contains at least one
# transposed year: series 124 (W124 E-Class) is stamped 1194-03-01 where 1984
# is meant. A single bad row poisons the series min/max, and an unchecked 1194
# puts 800 junk entries in the year picker. Out-of-range stamps are treated as
# unknown rather than corrected -- 1194 is obviously 1984, but guessing at
# someone's production data is not this layer's job.
YEAR_MIN, YEAR_MAX = 1900, 2035


def _year(stamp: str | None) -> str:
    """Year out of an ISO "YYYY-MM-DD" (or "YYYY-MM", or "YYYY") stamp.

    Returns "" for a stamp outside living automotive history.
    """
    m = re.match(r"\s*(\d{4})", str(stamp or ""))
    if not m:
        return ""
    return m.group(1) if YEAR_MIN <= int(m.group(1)) <= YEAR_MAX else ""


def year_range(entry: dict) -> str:
    """"1983-2000", or "2016+" while the series is still in production."""
    start, end = _year(entry.get("year_from")), _year(entry.get("year_to"))
    if not start:
        return ""
    if not end:
        return f"{start}+"
    return start if end == start else f"{start}-{end}"


# A structured model link is only used if, on documents that also state their
# model in their own text, the two mostly agree. The WIS extractor's schema
# 1.1 links fail this badly: on 97,891 such documents, 98.4% link to a
# *different* model than the text states ("Overview of innovations ...
# Sprinter", MODEL 901-904, linked to the 447 V-Class). W_Dok2Reihe.ReihenID is
# not a model reference -- it also keys Dokumente1..10, WS1..3, WC1..9, i.e.
# it's a position in the manual's navigation tree -- so the extractor's join to
# W_Baumusterzeile produced plausible-looking nonsense.
STRUCTURED_MIN_AGREEMENT = 0.5


def structured_agreement(path: Path) -> tuple[int, float]:
    """(docs carrying both, share whose structured link overlaps their text)."""
    n = ok = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            s = set(rec.get("model_series") or [])
            p = set(model_families(rec.get("applicability_notes") or []))
            if s and p:
                n += 1
                ok += bool(s & p)
    return n, (ok / n if n else 1.0)


def record_series(rec: dict, catalogue: "set[str] | dict | None" = None,
                  trust_structured: bool = True) -> list[str]:
    """The model series a document applies to.

    `trust_structured=False` ignores `model_series` entirely -- see
    STRUCTURED_MIN_AGREEMENT for why the importer may decide that.

    Schema 1.1 carries this structurally in `model_series`. When trusted,
    prefer it: the prose in
    `applicability_notes` is incomplete, and filtering on prose alone makes
    whole model ranges look absent even though their documents are indexed --
    pre-1990 cars especially, since those manuals rarely state the model in
    the body.

    When the structured field is empty the prose is still consulted. On the
    real English corpus 19,999 text documents have no W_Dok2Reihe link at all
    but name a model in their own body. An empty join is absence of evidence,
    not evidence of "applies to every model" -- and documents tied to nothing
    go to the general bucket, which is searched alongside *every* vehicle. So
    leaving them untied would put a document that says "MODEL 176" in front of
    someone who picked a W140.

    Prose is text, though, so when Mercedes' own model table (`catalogue`)
    is available a code read from prose must be a series in it. That drops
    what the regex can still mistake for a model -- "MODEL 346 332 04 16" is
    a part number -- instead of inventing a vehicle for it.
    """
    def field(name):
        return sorted({str(s).strip() for s in (rec.get(name) or [])
                       if str(s).strip() and str(s).strip() != "000"})

    # Schema 1.2 also names the engines by role (WS2/WC2), so an engine
    # number can never be mistaken for a vehicle with the same number
    # (906 = OM906 and the Sprinter): engines go to the "E" buckets.
    #
    # Only a document tied to *no* vehicle goes to its engine bucket -- by the
    # table or by its text. One that names vehicles is already under each of
    # them, and every car with that engine searches its engine bucket, so
    # filing "463 with engine 102" under E102 put G-Class oil capacities in
    # front of a 190E, and "636/639 with engine 651" Viano glow plugs in front
    # of a C 220 CDI (eval_wis.py caught both).
    #
    # When the table names an engine but no vehicle, the text still may:
    # "Capacities: Cross-country vehicles" is engine-only in WS/WC yet says
    # MODEL 460/461/463. An empty vehicle role is absence of evidence, so the
    # text's models are used before falling back to the engine bucket.
    notes = rec.get("applicability_notes") or []
    prose = model_families(notes)
    prose_engines = engine_families(notes)
    if catalogue:
        prose = [s for s in prose if s in catalogue]
        prose_engines = [e for e in prose_engines if e in catalogue]
    series = field("model_series") if trust_structured else []
    engines = field("engine_series") if trust_structured else []
    series = series or sorted(set(prose))
    if series:
        return series
    return [ENGINE_PREFIX + e for e in (engines or sorted(set(prose_engines)))]


def class_name(designations: list[str]) -> str:
    """Derive "C-Class" / "Sprinter" from the sales designations.

    This is a HEURISTIC, not data. The database has no marketing class name --
    only per-variant sales designations ("C 180", "C 63 AMG") -- so the shared
    first token is taken as the class, and a single letter is expanded. Where
    the designations disagree there is no honest answer and this returns "",
    leaving the bare model code as the label.
    """
    heads = {d.split()[0] for d in designations if d and d.split()}
    if len(heads) != 1:
        return ""
    head = heads.pop()
    return f"{head}-Class" if len(head) == 1 and head.isalpha() else head


def model_families(notes: list[str]) -> list[str]:
    """Three-digit model families named in a record's applicability notes.

    The full codes stay in the page's section path -- families are only how
    vehicles are grouped, so "MODEL 901.6" and "MODEL 901.7" land together.
    """
    found: list[str] = []
    for note in notes:
        for block in MODEL_BLOCK_RE.findall(note or ""):
            for fam in _block_families(block):
                if fam not in found:
                    found.append(fam)
    return sorted(found)


def engine_families(notes: list[str]) -> list[str]:
    """Three-digit engine series named on an ENGINE line: ["102", "111"]."""
    found: list[str] = []
    for note in notes:
        for block in ENGINE_BLOCK_RE.findall(note or ""):
            for fam in _block_families(block):
                if fam not in found:
                    found.append(fam)
    return sorted(found)


def is_engine_family(family: str) -> bool:
    return family.startswith(ENGINE_PREFIX)


def _block_families(block: str) -> list[str]:
    """Series named in one "MODEL ..." block, reading Mercedes slash lists.

    WIS writes variant lists as a dotted Baumuster followed by slash-separated
    suffixes: "639.601/603/605/701/703" is five variants of the one series 639.
    The suffixes are three digits too, so taking every 3-digit number as a
    series -- which is what this did until it met the real corpus -- turned
    them into models: "906.111 /113 /211 /213" filed a Sprinter document
    under 211 and 213, the E-Class. A wrong-vehicle document, the one failure
    the vehicle filter exists to prevent. 59,634 such suffixes occur in the
    English set.

    So within each comma-separated item: a dotted head claims every slash
    suffix after it as its own variants. A bare head is different only for a
    *pair* -- "MODELS 202/210" names two series (C-Class and E-Class). Longer
    bare chains are variant lists with the dot left out ("203 /018 /065 /218"
    is C-Class variants, and 218 would otherwise file it under the CLS), and
    short segments ("169 /09") are suffixes.
    """
    fams: list[str] = []
    for item in block.split(","):
        parts = [p.strip() for p in item.split("/")]
        fams.extend(FAMILY_RE.findall(parts[0]))
        if (len(parts) == 2 and not DOTTED_RE.search(parts[0])
                and re.fullmatch(r"\d{3}", parts[1])):
            fams.append(parts[1])
    return fams


def _classify(title: str, body_format: str) -> str:
    low = (title or "").lower()
    if any(h in low for h in WIRING_HINTS):
        return "wiring"
    if any(h in low for h in TSB_HINTS):
        return "tsb"
    if body_format == "xml" or any(h in low for h in SPEC_HINTS):
        return "spec_table"
    return "manual"


def engines_key(rec: dict) -> str:
    """Engine families a record is limited to, as ",272,642," ("" = any).

    Schema 1.2's engine role, else the text's ENGINE lines. 60,023 records
    filed under a car also name engines -- "Bolt tightening procedure for
    cylinder head cover" under the W212 is OM642 only, and showed up for an
    M272 petrol E 350 before search could filter on this.
    """
    fams = {str(s).strip()[:3] for s in (rec.get("engine_series") or [])
            if str(s).strip()[:3].isdigit() and str(s).strip() != "000"}
    if not fams:
        fams = set(engine_families(rec.get("applicability_notes") or []))
    if not fams:
        # last resort, the title: "Cylinder head cover, OM457" is a truck
        # engine's page that WIS ties to nothing, and reached an M272 E-Class
        fams = set(TITLE_ENGINE_RE.findall(rec.get("title") or ""))
    return "," + ",".join(sorted(fams)) + "," if fams else ""


# An exact model or engine code: "102.983", "212.098", with 3-digit slash
# variants ("906.111/113"); a 2-digit tail ("102.983/99") is not a code.
CODE_RE = re.compile(r"\b([1-9]\d{2})\.(\d{3})((?:\s*/\s*\d{3}\b)*)")
EXCEPT_RE = re.compile(r"\bexcept\b([^;)\n]{0,120})", re.I)


def _codes(text: str) -> set[str]:
    out = set()
    for m in CODE_RE.finditer(text or ""):
        out.add(f"{m.group(1)}.{m.group(2)}")
        for suffix in re.findall(r"\d{3}", m.group(3) or ""):
            out.add(f"{m.group(1)}.{suffix}")
    return out


def code_limits(rec: dict) -> tuple[str, str]:
    """(codes the title names as its only targets, codes it excludes).

    "except" anywhere in the title, MODEL/ENGINE lines or validity text
    excludes the codes after it (6,805 records); exact codes left in the
    title once those are removed are its targets (1,533 records), e.g.
    "Engine 102.942 multi belt drive". Codes are models or engines alike --
    the search compares both with the chosen car's.
    """
    title = rec.get("title") or ""
    hay = " ".join([title, *(rec.get("applicability_notes") or []),
                    rec.get("applicability_summary") or ""])
    excluded = set()
    for m in EXCEPT_RE.finditer(hay):
        excluded |= _codes(m.group(1))
    only = _codes(EXCEPT_RE.sub(" ", title)) - excluded
    key = lambda s: "," + ",".join(sorted(s)) + "," if s else ""
    return key(only), key(excluded)


# "OM457", "OM 642", "M272", "M 113" -- an engine code in a title. Three digits
# only, so bolt sizes ("M12", "M8x1.25") never match.
TITLE_ENGINE_RE = re.compile(r"\b(?:OM|M)\s?(\d{3})\b(?![.,]\d)")


def types_key(bits) -> str:
    """Vehicle-type bits as ",0,2," -- the delimiters make a SQL substring
    test exact, so bit 1 never matches inside 11. Empty for no bits."""
    bits = sorted({int(b) for b in (bits or [])})
    return "," + ",".join(map(str, bits)) + "," if bits else ""


# A model's vehicle types are the WIS fzg_art bits its own documents carry.
# Each series has one bit on ~100% of its documents (every car checked is bit
# 0, the G-Class bit 5, the V-Class bit 2) plus a scatter of others at 1-4%.
# Bits on under 1% are single mis-tags and are ignored; everything above is
# kept, because a general document for a type this model's own documents do
# carry may well apply to it.
TYPE_SHARE_MIN = 0.01
# A document flagged for this many types or more is flagged for *all* of them
# and says nothing about which type a model is. On the English corpus the gap
# is stark: 242,441 documents carry one bit, 64 carry six, 18,166 carry seven.
# Counting those in let a W201's handful of all-types bulletins give it every
# type, which turned its filter off.
UNIVERSAL_MIN_BITS = 7


def vehicle_types_of(hits: "collections.Counter", n_docs: int) -> str:
    if not n_docs:
        return ""
    return types_key(b for b, n in hits.items() if n / n_docs >= TYPE_SHARE_MIN)


def record_years(rec: dict) -> str:
    """The build-year window a document's models cover, as "1983-2000"."""
    return year_range({"year_from": rec.get("model_year_from"),
                       "year_to": rec.get("model_year_to")})


def applicability(rec: dict, catalogue=None, trust_structured: bool = True) -> list[str]:
    """Human-readable applicability, structured fields first.

    Becomes the page's section_path -- displayed as the breadcrumb and
    weighted above the body by BM25, so a Baumuster like "201.034" is an
    exact searchable token.
    """
    parts: list[str] = []
    fams = record_series(rec, catalogue, trust_structured)
    series = [f for f in fams if not is_engine_family(f)]
    engines = [f[len(ENGINE_PREFIX):] for f in fams if is_engine_family(f)]
    if series:
        parts.append("MODEL " + ", ".join(series))
    if engines:
        parts.append("ENGINE " + ", ".join(engines))
    # baumuster refines series to 6-digit variants; skip when it says no more
    # than the series already did
    # the Baumuster come from the same join: untrusted, so unused
    bau = sorted({str(b).strip() for b in ((rec.get("baumuster") or []) if trust_structured else [])
                  if str(b).strip() and str(b).strip() not in series})
    if bau:
        parts.append(", ".join(bau[:10]) + (" ..." if len(bau) > 10 else ""))
    # No "built <years>" here. The record's model_year_from/to come from the
    # same W_Baumusterzeile dates that were scored unreliable (they put the
    # W126's start at 1983, not 1979), and this text is what the answer model
    # reads -- a wrong build window is worse than none.
    if parts:
        if rec.get("applies_all_series"):
            parts.append("all variants")
        return parts
    notes = [n for n in (rec.get("applicability_notes") or []) if n]
    return notes or ["General"]


def sample_names(variants: list[dict], n: int = 3) -> str:
    """A few real sales names for a series label: "260 SE, 280 S, 560 SEL …".

    Ordered so the spread shows -- first, middle, last by name -- rather than
    three near-identical neighbours.
    """
    names = sorted({v["name"] for v in variants})
    if len(names) <= n:
        return ", ".join(names)
    picks = [names[round(i * (len(names) - 1) / (n - 1))] for i in range(n)]
    return ", ".join(dict.fromkeys(picks)) + " …"


def _page_text(rec: dict, catalogue=None, trust_structured: bool = True,
               images: list[str] | None = None) -> str:
    """Body plus the two things worth making searchable alongside it.

    Applicability leads so the answerer can flag a document that only applies
    to a sibling model. Cross-references trail as plain text rather than
    `links`: they are WIS document *numbers* and the corpus carries no
    number->id map, so they would render as dead links -- but as text they are
    exact BM25 tokens and the model can name them.
    """
    parts: list[str] = []
    applies = applicability(rec, catalogue, trust_structured)
    if applies != ["General"]:
        parts.append("Applies to: " + "; ".join(applies))
    # schema 1.2: Mercedes' own one-line validity text, with the conditions
    # the series list can't carry ("up to 31.12.93", "with CODE P98")
    summary = " ".join(fix_mojibake(rec.get("applicability_summary") or "").split())
    if summary:
        parts.append("Validity: " + (summary[:400] + " ..." if len(summary) > 400 else summary))
    # the prose note, when it adds something the structured fields did not
    # (engine numbers, "as of" build breaks)
    notes = [n for n in (rec.get("applicability_notes") or []) if n]
    if notes and notes != applies:
        parts.append("Noted in the manual: " + "; ".join(notes))
    # schema 1.3: the page's graphics as [diagram: ...] markers -- the same
    # convention the HTML extractor uses. The UI renders each one in place,
    # and the answer model can only cite a figure (`figures`) whose marker it
    # was shown. Placed before the body so they land in the first chunk.
    body = fix_mojibake((rec.get("body_text") or "").strip())
    # Since the extractor places figures after their step ("[diagram: ...]"
    # in the body), only the ones it could not place go up front. An inline
    # marker for a file that didn't pass record_images() is removed: the
    # answer model must never be shown a figure that resolves to nothing.
    ok = set(images or [])
    body = _DIAGRAM_RE.sub(lambda m: m.group(0) if m.group(1).strip() in ok else "", body)
    inline = {m.group(1).strip() for m in _DIAGRAM_RE.finditer(body)}
    loose = [i for i in (images or []) if i not in inline]
    if loose:
        parts.append("\n".join(f"[diagram: {i}]" for i in loose))
    if body:
        parts.append(body)
    refs = [r for r in (rec.get("cross_refs") or []) if r]
    if refs:
        parts.append("Refer to: " + ", ".join(refs))
    # schema 1.3: the labels inside vector graphics (component codes, wire
    # colours, "PE 54.21-D-2104AA" cross-references). A wiring diagram has no
    # other text, and these are exact BM25 tokens.
    labels = " ".join(fix_mojibake(rec.get("figure_text") or "").split())
    if labels:
        parts.append("Diagram labels: " + labels)
    if rec.get("image_files"):
        nrs = [g.get("graphik_nr") for g in (rec.get("figures") or []) if g.get("graphik_nr")]
        if nrs:
            parts.append("Figures: " + ", ".join(dict.fromkeys(nrs)))
    return "\n\n".join(parts)


_DIAGRAM_RE = re.compile(r"\[diagram:\s*([^\]]+)\]")


@functools.lru_cache(maxsize=None)
def _image_exists(path: str) -> bool:
    return os.path.isfile(path)


def record_images(rec: dict, corpus_dir: Path) -> list[str]:
    """`image_files` (schema 1.3) that exist under <corpus_dir>/images/.

    Checked against the disk, not trusted: `query.py` treats every entry as a
    citable figure, and a figure that resolves to no file must never pass
    `_verify_citations()`. Cached, because a document is filed under every
    model it names and each one asks again.
    """
    out = []
    for rel in rec.get("image_files") or []:
        rel = str(rel).replace("\\", "/").lstrip("/")
        if rel and ".." not in rel.split("/") and \
                _image_exists(str(corpus_dir / "images" / rel)):
            out.append(rel)
    return out


@register
class RecordsImporter(Importer):
    name = "records"

    def __init__(self, option: str = "") -> None:
        # passenger (default): per model series, passenger vehicles only
        # model: per model series, everything incl. trucks/buses/Unimog
        # corpus: the whole source as one vehicle
        self.group = option or "passenger"
        if self.group not in ("corpus", "model", "passenger"):
            raise ValueError(
                f"unknown grouping {option!r}; use 'records' (passenger), "
                f"'records:model' (all vehicles) or 'records:corpus'")
        # doc_id -> (records.jsonl path, byte offsets or None for the whole file)
        self._plan: dict[str, tuple[Path, array | None]] = {}
        # records.jsonl path -> the manifest's record_count, for the audit
        self._expected: dict[Path, int] = {}
        # series in the corpus's models.jsonl; empty when there is none
        self._catalogue: set[str] = set()
        # whether this corpus's model_series links are trusted (see
        # STRUCTURED_MIN_AGREEMENT); decided per corpus in discover()
        self._trust = True
        # series -> types_key of the vehicle types its documents carry
        self._series_types: dict[str, str] = {}
        self.skipped_empty = 0

    # -- corpus discovery ------------------------------------------------

    @classmethod
    def _corpus_dirs(cls, root: Path, max_depth: int = 3) -> list[Path]:
        found: list[Path] = []

        def scan(d: Path, depth: int) -> None:
            if (d / "manifest.json").is_file() and (d / "records.jsonl").is_file():
                found.append(d)
                return
            if depth >= max_depth:
                return
            try:
                entries = sorted(e for e in d.iterdir() if e.is_dir())
            except (PermissionError, OSError):
                return
            for e in entries:
                scan(e, depth + 1)

        scan(root, 0)
        return found

    @classmethod
    def detect(cls, root: Path) -> bool:
        return bool(cls._corpus_dirs(root))

    @staticmethod
    def _manifest(cdir: Path) -> dict | None:
        try:
            m = json.loads((cdir / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        version = str(m.get("schema_version", ""))
        if version.split(".")[0] != SUPPORTED_SCHEMA_MAJOR:
            print(f"  ! {cdir.name}: manifest schema_version {version!r} is not "
                  f"{SUPPORTED_SCHEMA_MAJOR}.x -- skipping")
            return None
        return m

    @staticmethod
    def _vehicle_index(cdir: Path) -> dict[str, dict]:
        """`models.jsonl` (IMPORT_SPEC 3a) keyed by series, or {} if absent.

        One line per Baureihe: `reihe`, `year_from`, `year_to`, `baumuster`.
        Optional by design -- without it vehicles keep their bare "MODEL 201"
        label and no year, and everything else still works.
        """
        path = cdir / "models.jsonl"
        if not path.is_file():
            return {}
        out: dict[str, dict] = {}
        rejected: list[str] = []
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    series = str(entry.get("reihe") or "").strip()
                    if not series:
                        continue
                    out[series] = entry
                    # a stamp the catalogue has but we refuse to believe
                    for key in ("year_from", "year_to"):
                        if entry.get(key) and not _year(entry[key]):
                            rejected.append(f"{series} {key}={entry[key]}")
        except (OSError, ValueError) as exc:
            print(f"  ! {path.name} could not be read ({exc}); "
                  f"falling back to bare model codes")
            return {}
        if rejected:
            print(f"  ! {len(rejected)} implausible production date(s) in "
                  f"{path.name}, treated as unknown: {', '.join(rejected[:4])}"
                  + (" ..." if len(rejected) > 4 else ""))
        no_years = sum(1 for e in out.values() if not year_range(e))
        print(f"  {len(out)} model series in the catalogue, "
              f"{len(out) - no_years} with a production span")
        return out

    def discover(self, root: Path) -> Iterator[VehicleRecord]:
        used: set[str] = set()
        self.skipped_empty = 0

        def claim(base: str, suffix: str = "") -> str:
            doc_id = f"{base}-{suffix}" if suffix else base
            stem, n = doc_id, 2
            while doc_id in used:      # two corpora, same source+language
                doc_id = f"{stem}-{n}"
                n += 1
            used.add(doc_id)
            return doc_id

        for cdir in self._corpus_dirs(root):
            manifest = self._manifest(cdir)
            if manifest is None:
                continue
            source = manifest.get("source_type") or "unknown-source"
            lang = (manifest.get("language") or {}).get("iso") or "und"
            make = SOURCE_MAKES.get(source, "")
            base = f"{_slug(source)}-{_slug(lang)}"
            try:
                self._expected[cdir / "records.jsonl"] = int(
                    manifest["record_count"])
            except (KeyError, TypeError, ValueError):
                print(f"  ! {cdir.name}: manifest has no usable record_count; "
                      f"cannot verify the file was read whole")

            if self.group == "corpus":
                doc_id = claim(base)
                self._plan[doc_id] = (cdir / "records.jsonl", None)
                yield VehicleRecord(
                    doc_id=doc_id,
                    source_name=source,
                    file_path=str(cdir),
                    make=make,
                    model=f"All models ({lang})",
                    doc_type="manual",
                    # the manifest's claim, which counts the figure-only stubs
                    # that pages() drops; mark_complete() records the truth
                    page_count=int(manifest.get("record_count") or 0),
                )
                continue

            known = self._vehicle_index(cdir)
            self._catalogue = set(known)
            n_both, agree = structured_agreement(cdir / "records.jsonl")
            self._trust = agree >= STRUCTURED_MIN_AGREEMENT
            if not self._trust:
                print(f"  ! structured model links IGNORED: on {n_both} documents that "
                      f"also state their model in text, only {agree:.1%} agree. Filing "
                      f"by each document's own MODEL line instead; documents that "
                      f"state none go to the general pool, not to a wrong car.")
            else:
                print(f"  structured model links agree with document text on "
                      f"{agree:.1%} of {n_both} documents -- trusted")
            buckets = self._scan_models(cdir / "records.jsonl")
            # claimed up front: every model vehicle has to name it, and it is
            # yielded last
            general_id = (claim(base, "general")
                          if any(fam == "" for fam, _ in buckets) else "")

            variants = self._variants(cdir, manifest)
            vin_codes = self._vin_codes(cdir, manifest)
            vin9_by_bm = self._vin2fin(cdir, manifest)
            years = curated_years(source)
            known_as = curated_names(source)
            engine_labels = curated_engines(source)
            us_years = curated_us_years(source)
            # every series' doc_id, known before any is yielded, so a variant
            # can point at its engine's bucket
            ids = {fam: claim(base, fam.lower()) for fam, _ in buckets if fam}
            # A series with sales-named variants is a vehicle. Engine numbers
            # share the same 3-digit space and WIS files both under the one
            # number, so an engine's bucket is only safe to borrow when no
            # vehicle uses that number -- see _with_engine_ids.
            vehicle_series = set(variants)
            all_engines = {e[:3] for vs in variants.values() for v in vs
                           for e in v.get("engines", [])}
            engine_series = all_engines - vehicle_series
            # a variant's engine -> its bucket: the prose ENGINE bucket ("E102")
            # when there is one, else (a trusted structured corpus) a plain
            # engine-only series
            engine_ids = {}
            for e3 in all_engines:
                if ENGINE_PREFIX + e3 in ids:
                    engine_ids[e3] = ids[ENGINE_PREFIX + e3]
                elif e3 in engine_series and e3 in ids:
                    engine_ids[e3] = ids[e3]

            # Passenger focus: Mercedes' own passenger-car division (ASRA
            # SparteAbk 00) -- cars, G-Class, smart, and the passenger vans
            # (Vito/Viano, V-Class). Its commercial division (Sprinter, trucks,
            # buses, Unimog) is left out of the index, and so are engine
            # buckets only commercial vehicles use.
            skip: set[str] = set()
            if self.group == "passenger":
                skip = {s for s, vs in variants.items() if series_division(vs) == "commercial"}
                car_engines = {e[:3] for s, vs in variants.items() if s not in skip
                               for v in vs for e in v.get("engines", [])}
                skip |= engine_series - car_engines
                skip |= {f for f in ids if is_engine_family(f)
                         and f[len(ENGINE_PREFIX):] not in car_engines}
                print(f"  passenger vehicles only: leaving out "
                      f"{sum(1 for s in skip if s in ids)} commercial series and "
                      f"commercial-only engine buckets")

            for family, offsets in buckets:
                if family in skip:
                    continue
                doc_id = general_id if not family else ids[family]
                self._plan[doc_id] = (cdir / "records.jsonl", offsets)
                entry = known.get(family, {})
                mine = [with_us_years(with_engine_labels(
                            self._with_vin9(self._with_engine_ids(v, engine_ids), vin9_by_bm),
                            engine_labels), us_years)
                        for v in variants.get(family, [])] if family else []
                cls = class_name([v["name"] for v in mine])
                yield VehicleRecord(
                    doc_id=doc_id,
                    source_name=source,
                    file_path=str(cdir),
                    make=series_make(mine, entry.get("make") or make),
                    model=(f"No model stated ({lang})" if not family
                           # the name from the curated table, when there is one
                           else known_as[family] if family in known_as
                           else f"{cls} ({family})" if cls
                           # names with no shared first word ("260 SE",
                           # "560 SEL") give no class -- show a few instead
                           # of a bare number nobody recognises
                           else f"MODEL {family}: {sample_names(mine)}" if mine
                           # an engine series is a component, not a car --
                           # say so, since it still appears in the picker
                           else f"Engine {family[len(ENGINE_PREFIX):]}"
                           if is_engine_family(family)
                           else f"Engine {family}" if family in engine_series
                           else f"MODEL {family}"),
                    # the curated table when the source has one (a missing
                    # series gets no year: shown under every year); else the
                    # catalogue's span
                    year_range=(years.get(family, "") if years
                                else year_range(entry)),
                    doc_type="manual",
                    page_count=len(offsets),
                    # the general bucket is nobody's companion, least of all
                    # its own
                    general_doc_id="" if not family else general_id,
                    model_code=family,
                    vehicle_types=self._series_types.get(family, ""),
                    variants=mine,
                    vin_codes=vin_codes.get(family, "") if family else "",
                )

    @staticmethod
    def _variants(cdir: Path, manifest: dict) -> dict[str, list[dict]]:
        """`variants.jsonl` (IMPORT_SPEC 3b) as series -> [{name, baumuster}].

        The specific models a series covers, by sales name ("190 E 2.3 16").
        Optional: without it the picker lists each series as one entry.
        """
        path = cdir / (manifest.get("variants_file") or "variants.jsonl")
        if not path.is_file():
            return {}
        out: dict[str, list[dict]] = {}
        try:
            with path.open(encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    v = json.loads(line)
                    series, name = str(v.get("model_series") or ""), (v.get("name") or "").strip()
                    if series and name:
                        out.setdefault(series, []).extend(split_by_body(
                            {"name": fix_mojibake(name),
                             "baumuster": v.get("baumuster") or [],
                             "engines": v.get("engines") or [],
                             "engine_by_bm": v.get("engine_by_bm") or {},
                             "body_by_bm": v.get("body_by_bm") or {},
                             "vehicle_type": v.get("vehicle_type") or "",
                             "division": v.get("division") or ""}))
        except (OSError, ValueError) as exc:
            print(f"  ! {path.name} could not be read ({exc}); "
                  f"the picker will list series only")
            return {}
        print(f"  {sum(map(len, out.values()))} named models across {len(out)} series "
              f"from {path.name}")
        return out

    @staticmethod
    def _with_engine_ids(variant: dict, engine_ids: dict[str, str]) -> dict:
        """Attach the doc_ids of the engine buckets this variant's engines use.

        WIS files engine documents under the *engine's* series, not the car's:
        all 1,124 documents of the M102 engine sit under series 102, and none
        is also filed under the W201. So the head-gasket check for 102.983 --
        the 190E 2.3-16's own engine -- was unreachable from the car. A
        variant's engines (Mercedes' ASRA/AW tables) say which bucket to add.

        `engine_ids` holds only engine-*only* numbers. Engine and vehicle
        numbers share one space: 906 is the OM906 truck engine and the Sprinter,
        651 the OM651 diesel and a truck series, 123 the M123 engine and the
        W123 car -- and WIS files both under the one number, so borrowing those
        buckets would put a van's or truck's documents in front of a car. Such
        engines get no link: less recall, no wrong vehicle.
        """
        linked = []
        for e in variant.get("engines", []):
            eid = engine_ids.get(e[:3])
            if eid and eid not in linked:
                linked.append(eid)
        return {**variant, "engine_ids": linked}

    @staticmethod
    def _vin2fin(cdir: Path, manifest: dict) -> dict[str, list[str]]:
        """`vin2fin.json` (AW_VIN2FIN) inverted to Baumuster -> [VIN prefixes].

        Mercedes' own US VIN decoder: the first 9 VIN characters (check digit
        included) name the exact Baumuster, so a US VIN can pick the variant,
        not just the series. Optional; without it US VINs resolve to a series.
        """
        path = cdir / (manifest.get("vin2fin_file") or "vin2fin.json")
        if not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"  ! {path.name} could not be read ({exc}); US VINs pin the series only")
            return {}
        out: dict[str, list[str]] = {}
        for vin9, bm in raw.items():
            out.setdefault(str(bm), []).append(str(vin9).upper())
        print(f"  {len(raw)} US VIN prefixes -> {len(out)} Baumuster from {path.name}")
        return out

    @staticmethod
    def _with_vin9(variant: dict, vin9_by_bm: dict[str, list[str]]) -> dict:
        """Attach the US VIN prefixes that decode to this variant's Baumuster."""
        vins = sorted({v for bm in variant.get("baumuster", []) for v in vin9_by_bm.get(bm, [])})
        return {**variant, "vin9": vins} if vins else variant

    @staticmethod
    def _vin_codes(cdir: Path, manifest: dict) -> dict[str, str]:
        """`vin_us.json` inverted to series -> ",WDB:D,WDD:D,".

        A US-market VIN has a coded letter at position 4 where a European FIN
        has the Baumuster; Mercedes maps (WMI, letter) to a series.
        """
        path = cdir / (manifest.get("vin_us_file") or "vin_us.json")
        if not path.is_file():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"  ! {path.name} could not be read ({exc}); US VINs will not resolve")
            return {}
        codes: dict[str, list[str]] = {}
        for wmi, letters in raw.items():
            for letter, series in letters.items():
                codes.setdefault(str(series), []).append(f"{wmi}:{letter}")
        return {s: "," + ",".join(sorted(c)) + "," for s, c in codes.items()}

    def _scan_models(self, path: Path) -> list[tuple[str, array]]:
        """One pass over the corpus, bucketing byte offsets by model family.

        Offsets rather than records: the file runs to hundreds of megabytes and
        `pages()` is called once per vehicle, so the alternative is re-reading
        the whole corpus once per model.
        """
        buckets: dict[str, array] = {}
        # per series: type-bit counts over (documents naming only that series,
        # documents naming it among others), and how many of each
        type_hits: dict[str, tuple[collections.Counter, collections.Counter]] = {}
        informative: dict[str, list[int]] = {}
        tally = Tally()
        with path.open("rb") as f:
            while True:
                offset = f.tell()
                line = f.readline()
                if not line:
                    break
                if not line.strip():
                    continue
                tally.seen += 1
                try:
                    rec = json.loads(line)
                except ValueError:
                    tally.malformed += 1
                    continue
                if (not (rec.get("body_text") or "").strip()
                        and not record_images(rec, path.parent)):
                    tally.figure_only += 1
                    continue  # see _page()
                tally.kept += 1
                bits = set(rec.get("fzg_art_indices") or [])
                families = record_series(rec, self._catalogue, self._trust) or [""]
                for family in families:
                    buckets.setdefault(family, array("q")).append(offset)
                    if bits and len(bits) < UNIVERSAL_MIN_BITS:
                        # 0 = names only this series, 1 = names several
                        # (vehicles and engines counted apart: "201 with
                        # engine 102" still names only one vehicle)
                        same = [g for g in families
                                if is_engine_family(g) == is_engine_family(family)]
                        k = 0 if len(same) == 1 else 1
                        type_hits.setdefault(family, (collections.Counter(),
                                                      collections.Counter()))[k].update(bits)
                        informative.setdefault(family, [0, 0])[k] += 1
        self.skipped_empty += tally.figure_only
        self._audit(path, tally)
        self._series_types = {}
        for fam, (only, shared) in type_hits.items():
            if not fam:
                continue
            n_only, n_shared = informative[fam]
            # A document naming several series carries the type bits of all
            # of them ("MODEL 201, 463, 906" is flagged car *and* off-road
            # *and* van), so it cannot say which one is which. Only when a
            # series has no document of its own do those count. A series left
            # with no informative documents gets no types: no narrowing.
            self._series_types[fam] = (vehicle_types_of(only, n_only) if n_only
                                       else vehicle_types_of(shared, n_shared))

        # A document that names three models is filed under all three, so page
        # rows exceed documents. Say so, or the ingest total looks like it
        # invented records.
        rows = sum(len(offs) for offs in buckets.values())
        unscoped = len(buckets.get("", ()))
        if tally.kept:
            print(f"  {rows} page rows from {tally.kept} documents "
                  f"(x{rows / tally.kept:.2f}, a document is filed under every "
                  f"model it names); {len(buckets) - bool(unscoped)} model(s) "
                  f"+ {unscoped} document(s) naming none")

        # named models first, the no-model bucket last
        return sorted(buckets.items(), key=lambda kv: (kv[0] == "", kv[0]))

    def _audit(self, path: Path, tally: Tally) -> None:
        """Print the reconciliation, and check it against the manifest.

        IMPORT_SPEC section 4: `record_count` is there so the loader can verify
        it read the whole file. A short read otherwise looks exactly like a
        smaller corpus, which is the one failure mode nobody notices.
        """
        print(f"  {tally.line()}")
        if tally.malformed:
            print(f"  ! {tally.malformed} line(s) in {path.name} are not valid "
                  f"JSON and were dropped -- this is data loss, not filtering")
        expected = self._expected.get(path)
        if expected is None:
            return
        if tally.seen == expected:
            print(f"  manifest record_count {expected}: matches")
        else:
            short = expected - tally.seen
            print(f"  ! manifest says {expected} records but {path.name} has "
                  f"{tally.seen} ({short:+d}). The file looks "
                  f"{'truncated' if short > 0 else 'longer than the manifest'}"
                  f" -- re-run the extractor's parse stage before trusting this "
                  f"index.")

    # -- pages -----------------------------------------------------------

    def pages(self, vehicle: VehicleRecord) -> Iterator[PageRecord]:
        if vehicle.doc_id not in self._plan:
            raise KeyError(
                f"{vehicle.doc_id} was not discovered by this importer instance")
        path, offsets = self._plan[vehicle.doc_id]

        with path.open("rb") as f:
            if offsets is None:
                lines: Iterator[bytes] = iter(f.readline, b"")
            else:
                def seek_lines() -> Iterator[bytes]:
                    for off in offsets:
                        f.seek(off)
                        yield f.readline()
                lines = seek_lines()

            tally = Tally()
            for line in lines:
                if not line.strip():
                    continue
                tally.seen += 1
                try:
                    rec = json.loads(line)
                except ValueError:
                    tally.malformed += 1
                    continue
                page = self._page(vehicle, rec, path)
                if page is None:
                    tally.figure_only += 1
                    continue
                tally.kept += 1
                yield page

        # Only a whole-file read can be reconciled against the manifest; a
        # model vehicle reads its own slice and was already audited in
        # _scan_models.
        if offsets is None:
            self._audit(path, tally)

    def _page(self, vehicle: VehicleRecord, rec: dict,
              path: Path) -> PageRecord | None:
        # A record with no body text and no graphic on disk can answer
        # nothing, so it is counted and dropped. Since schema 1.3 most
        # figure-only records (wiring diagrams, tightening sequences, scanned
        # manual pages) carry converted graphics and become real pages.
        images = record_images(rec, path.parent)
        if not (rec.get("body_text") or "").strip() and not images:
            self.skipped_empty += 1
            return None

        return PageRecord(
            doc_id=vehicle.doc_id,
            page_no=str(rec.get("dok_id", "")),
            file_path=str(path),
            title=fix_mojibake((rec.get("title") or "").strip()),
            section_path=applicability(rec, self._catalogue, self._trust),
            system="",           # the corpus carries no system taxonomy
            doc_type=_classify(rec.get("title", ""), rec.get("body_format", "")),
            text=_page_text(rec, self._catalogue, self._trust, images),
            # only files that exist under <corpus>/images/, which is where the
            # server looks (vehicle.file_path is the corpus dir) -- so a cited
            # figure always resolves
            images=images,
            links=[],            # cross-refs are docnrs; see _page_text
            applicability="this_vehicle",
            vehicle_types=types_key(rec.get("fzg_art_indices")),
            engines=engines_key(rec),
            **dict(zip(("codes_only", "codes_except"), code_limits(rec))),
        )
