"""Known-answer retrieval check for the Mercedes WIS source.

FREE -- no API calls, and the index is opened READ-ONLY. It never writes to
index.db (which also holds the spend log), never re-ingests.

`eval.py` covers the 2015 F-150 (LEMON) only. This file covers WIS keyword
retrieval: scope building (series + variant engine buckets + type-narrowed
general pool), filing, and the wrong-vehicle safety property.

    python eval_wis.py                    # all cases
    python eval_wis.py --case w124-300e-brake-pads -v
    python eval_wis.py --index other.db --top 10

WHY THE CHECKS ARE NOT CIRCULAR
-------------------------------
A hand check once "passed" while 98.4% of documents sat under the wrong model,
because it asked which bucket a page was in -- the same thing the importer had
decided. Every check here reads the *page text* instead:

  expect      a DokID read by hand out of the corpus (title + applicability
              recorded next to each) must be in the top k.
  forbid      a DokID whose own text says it is for another vehicle class
              (commercial twin of a car page, a 906 slash-list page) must NOT
              be in the top k.
  filing      every result from the selected series' own bucket must NAME that
              series in its title/text; every result from an engine bucket
              must name that engine. (The "Applies to:" header is Mercedes'
              W_Dok2Reihe join, not the importer's bucketing -- a bucketing bug
              like the 906.111 /211 slash-list one shows up here.)
  dedup       no (page_no, chunk) twice -- a page filed under a model and its
              engine is one page.
  engine      a chosen variant's engine bucket contributes >= 1 result.
  commercial  no result whose named models are ALL commercial series
              (Sprinter/T1, trucks, buses, Unimog) and which does not name the
              selected series. The narrowing property.
  wrong       no result that names models/engines and none of them is the
              selected series or one of the chosen variant's engines -- "this
              page states only a different, incompatible vehicle". The core
              safety property.
  foreign     stricter: no result from outside the series' own bucket that
              names models and not this one. Catches pages that share the
              engine but are for another vehicle (a G-Class oil capacity
              reached through the M102 bucket), which `wrong` lets through.

A page that names no series at all is neutral (genuinely general, or the text
gives no evidence either way). Codes are read only after a MODEL/ENGINE/TYPE
keyword or in "<engine> in model ..." form; a slash suffix after a dotted head
(`906.111 /113 /211`) is a variant of the head, never a series -- the same rule
the importer uses, because the opposite reading is the bug it fixed.

KNOWN-BAD, AS A RATCHET
-----------------------
Some guards fail today because retrieval is genuinely wrong (see KNOWN_BAD
reasons). They are not weakened: each records the number of offending results
measured today as a ceiling. More offenders than that is a FAIL (regression);
fewer prints IMPROVED (lower the ceiling); zero prints FIXED (delete the
entry). Known-bad never hides an `expect`, `forbid`, `filing`, `engine` or
`dedup` failure. Ceilings are counts at --top 10; with any other --top the
guards are printed but not scored.

Mutation-checked: switching off the general pool's type narrowing makes 10
cases fail (commercial pages and the forbidden twins come straight back);
dropping the variant engine buckets makes 15 fail.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

from autorundown import config
from autorundown.index import link_sources
from autorundown.search import scope_ids, search

PREFIX = "mercedes-wis-virtualbox-en-"

# Series whose every variant is a truck, bus, Unimog or light/heavy transporter
# per AW_Fahrzeuge.FzgArtCode (variants.jsonl `vehicle_type`), minus the four
# the passenger import keeps (414 Vaneo, 415 Citan, 447 V-Class/Vito, 448)
# and 000 (trailers). Only ever matched against MODEL mentions, so the 6xx
# numbers here (T1 vans, truck series) do not collide with the diesel engines.
COMMERCIAL = set("""
345 357 364 368 371 372 374 375 380 382 384 386 388 390 397 398 405 408 409
410 418 424 427 435 436 437 440 441 442 443 444 467 468 601 602 611 613 615
616 617 619 620 621 622 623 624 625 628 629 631 632 633 634 636 638 639 645
646 647 648 649 650 651 652 653 654 655 656 657 658 659 660 661 664 666 667
668 669 670 671 673 674 675 676 677 678 679 682 684 685 688 690 693 694 695
696 800 831 900 901 902 903 904 905 906 909 930 932 933 934 936 938 940 942
943 944 949 950 952 953 954 956 957 958 963 964 967 970 972 974 975 976 979
""".split()) - {"636", "638", "639"}  # Vito/Viano are indexed as passenger

_KW = r"(?:MODELS?|Models?|models?|ENGINES?|Engines?|engines?|TYPES?|Types?)"
# A keyword directly followed by a series code starts an applicability run,
# which lasts to the next keyword or the end of the line. WIS writes "Model 107
# from 8/85; 123; 124, 129, 140 up to 6/93": the qualifiers are not codes, but
# every 3-digit number after them is. Decimals ("8,0") and torques with a
# comma are not codes; neither is anything glued to a letter or a dot.
_KW_RE = re.compile(rf"\b({_KW})\b")
_TOKEN_RE = re.compile(r"(/\s*)?(?<![\w.])(?<!\d,)(\d{3})(\.\d+)?(?!\d|,\d)")
_IN_MODEL_RE = re.compile(r"\b(\d{3})(?:\.\d{2,3})?\s+in\s+models?\b", re.I)


def _run_codes(run: str) -> set[str]:
    """Series codes in one applicability run. A slash suffix after a dotted
    head (906.111 /113 /211) is a variant of the head and is skipped; bare
    slash lists (MODELS 202/210) are kept. Over-reading is the safe error
    here: an extra code can only make a page look *more* compatible."""
    codes: set[str] = set()
    dotted = False
    for m in _TOKEN_RE.finditer(run):
        slash, code, suffix = m.groups()
        if slash and dotted:
            continue
        codes.add(code)
        dotted = bool(suffix)
    return codes


def named_codes(text: str) -> tuple[set[str], set[str]]:
    """(models, engines): 3-digit series codes the page itself names.

    A run starts at a keyword directly followed by a code and ends at the next
    keyword or the end of the line."""
    models: set[str] = set()
    engines: set[str] = set()
    for line in text.splitlines():
        marks = list(_KW_RE.finditer(line))
        for n, m in enumerate(marks):
            end = marks[n + 1].start() if n + 1 < len(marks) else len(line)
            run = line[m.end():end]
            if not re.match(r"[ \t]+\d{3}(?!\d)", run):
                continue
            target = engines if m.group(1).lower().startswith("engine") else models
            target |= _run_codes(run)
    engines.update(_IN_MODEL_RE.findall(text))
    return models, engines


# ---------------------------------------------------------------------------
# Cases. `variant` is the index into the vehicle's `variants` JSON (the same
# number the UI sends as `variant=`); None = the whole series. Every expected
# DokID was found by searching, then read in full; the comment says why it is
# the right page for that car.
# ---------------------------------------------------------------------------
CASES: list[dict] = [
    # --- W201 190 E 2.3-16 (201.034, engine M102.983) -----------------------
    dict(id="w201-2316-cyl-head-bolts", series="201", variant=8,
         variant_name="190 E 2.3 16", query="cylinder head bolts",
         # 49013417 "0221 - Removal and installation of the exhaust camshaft,
         #   Engine 102 KE in model 201" -- ENGINE 102.983 (the 2.3-16); its
         #   preceding work is removing the cylinder head. 49013418 is the
         #   inlet-camshaft twin. 10006245 "Crankcase, timing case cover, end
         #   cover" -- ENGINE 102 torque table incl. "Bolt, cylinder head to
         #   timing case cover Nm 25". All three live only in the M102 bucket.
         # Added with schema 1.2 (the old index ranked other engines' bolt
         #   pages above these): 49013392 / 49014339 "0415 - Removal and
         #   installation of the cylinder head", ENGINE 102 (49013392 also
         #   MODEL 201) -- "Cylinder head bolts (90/1) unscrew ... screw in";
         #   49014185 "0130 - Removal and installation of cylinder head",
         #   ENGINE 102 -- "Cylinder head bolt 70 Nm, 2 x 90°".
         expect=["49013417", "49013418", "10006245",
                 "49013392", "49014339", "49014185"], expect_engine=True),
    dict(id="w201-2316-valve-clearance", series="201", variant=8,
         variant_name="190 E 2.3 16", query="valve clearance",
         # 50000342 "0561 - Checking the valve clearance, Engine 102.983/99
         #   Model 201 from 1/85". 9003319 "Valve clearance" check values,
         #   ENGINE 102 without hydraulic compensation.
         # Added with schema 1.2: 50000484 "0560.11 - Adjustment of the valve
         #   clearance" ENGINE 102; 49013414 "0210 - Checking the valve
         #   clearance" MODEL 201 ENGINE 102; 50000338 "0560 - Adjustment of
         #   the valve clearance" MODEL 123/201 ENGINE 102.
         expect=["50000342", "9003319", "50000484", "49013414", "50000338"],
         expect_engine=True),
    dict(id="w201-2316-oil-capacity", series="201", variant=8,
         variant_name="190 E 2.3 16", query="engine oil capacity",
         # 49013525 "0002 - Engine oil and filter change, Engine 102 KE in
         #   model 201" -- ENGINE 102.983, "Total capacity when changing oil
         #   and filter 5.0 liters".
         # Added with schema 1.2: 49013378 / 49013216 "0030 - Removal and
         #   installation of the engine (oil capacity)", MODEL 201 ENGINE 102
         #   -- "Engine (total capacity when refilling) 5.5 ... when changing
         #   oil and filter 5.0".
         expect=["49013525", "49013378", "49013216"], expect_engine=True),
    dict(id="w201-2316-valve-cover", series="201", variant=8,
         variant_name="190 E 2.3 16", query="cylinder head cover",
         # The user's report, 2026-10-08: an Ask about the 2.3-16's valve
         # cover answered 15 Nm from 50000338, "Engine 102 (except
         # 102.983/99 ...)" -- a page that excludes this car's engine.
         # 49013390 "0406 - Removal and installation of the cylinder head
         #   cover, Engine 102 KE in model 201" covers 102.983 (cap nuts 10 Nm,
         #   plastic screws 2 Nm). Needs exact-code filtering + boost.
         expect=["49013390"], forbid=["50000338", "49014337"], expect_engine=True),
    dict(id="w201-2316-break-in-oils", series="201", variant=8,
         variant_name="190 E 2.3 16", query="approved break-in engine oils",
         # General pool + type narrowing. 12000483 "Approved break-in engine
         #   oils" -- "all Passenger car - engine" (types 0,1,2,9). Its twin
         #   12000484 says "all Commercial vehicle - engine" (types 3,5,7,8)
         #   and must never reach a car.
         expect=["12000483"], forbid=["12000484"]),

    # --- W124 E-Class --------------------------------------------------------
    dict(id="w124-300e-brake-pads", series="124", variant=32,
         variant_name="300 E", query="brake pads",
         # 49015270 "0160 - Checking and replacement of the brake pads ...
         #   Models 124, 201". 4006810 "Test specifications for brake
         #   pads/brake rotors", MODEL 124.0xx wear limits.
         expect=["49015270", "4006810"]),
    dict(id="w124-300e-wheel-bolts", series="124", variant=32,
         variant_name="300 E", query="wheel bolts tightening torque",
         # 50000406 "4070 - Retightening the wheel bolts, Model 107 from 8/85;
         #   123; 124, 129, 140 up to 6/93 ...". Forbidden: 50000558 (MB 100 D
         #   van, types 2,3) and 49012301 (truck wheel mountings, types 5,7).
         expect=["50000406"], forbid=["50000558", "49012301"]),
    dict(id="w124-series-cyl-head", series="124", variant=None,
         query="remove install cylinder head",
         # Whole series = every variant's engine bucket. 49014601 "4150 -
         #   Removal and installation of the cylinder head, Engine 103" --
         #   names models 107, 124, 126, 201 (the 300 E's M103). 7146636
         #   "Removing, installing cylinder head cover", ENGINE 604/605/606
         #   in model 124.
         expect=["49014601", "7146636"]),

    # --- W126 S-Class --------------------------------------------------------
    dict(id="w126-series-brake-pads", series="126", variant=None,
         query="checking renewing brake pads",
         # 49013648 "0160 - Checking and renewing of the brake pads, A.
         #   Floating caliper on front axle (Model 126.04 to 08/85)". Sits in
         #   the general pool (no W_Dok2Reihe link, applicability only in the
         #   title) and must still be reachable from the W126.
         expect=["49013648"]),
    # guards only: no M117 timing-chain procedure exists in the corpus text
    dict(id="w126-560sel-timing-chain", series="126", variant=17,
         variant_name="560 SEL", query="timing chain", expect_engine=True),

    # --- R107 SL -------------------------------------------------------------
    dict(id="r107-560sl-chain-tensioner", series="107", variant=12,
         variant_name="560 SL", query="timing chain tensioner",
         # 28009852 "202 589 14 63 00 Tensioning plates" -- special tool,
         #   MODEL 107, 114, 116, 123, 126, 140 ... (the only R107-naming
         #   chain-tensioner hit; there is no M117 procedure in text form).
         # Guards only since schema 1.2: that tool sheet fell from rank 6 to
         #   14 as the 107 bucket grew, and it was never a real answer --
         #   the corpus text has no M117 chain-tensioner procedure. Restore an
         #   `expect` when the image pass or a better page gives one.
         expect_engine=True),

    # --- W140 S-Class --------------------------------------------------------
    dict(id="w140-s500-front-brake-pads", series="140", variant=22,
         variant_name="S 500", query="front axle brake pads",
         # 7101387 "Remove/check/install front axle brake pads", MODEL 129,
         #   140. 4007606 "Test specifications for brake pads/brake rotors",
         #   MODEL 140 all variants.
         expect=["7101387", "4007606"]),

    # --- W202 / W203 / W204 / W205 C-Class -----------------------------------
    dict(id="w202-c280-oil-capacity", series="202", variant=26,
         variant_name="C 280", query="engine oil capacity",
         # 11000738 "Filling capacities, engine lubrication system", ENGINE
         #   112, 113 -- "Filling capacity with oil filter Liter 8,0" (the
         #   C 280 M112; variant 26 also covers the M104 202.028).
         expect=["11000738"], expect_engine=True),
    dict(id="w203-c230-timing-chain", series="203", variant=17,
         variant_name="C 230", query="timing chain",
         # 10006819 "Timing chain drive, toothed belt drive", ENGINE 103, 104,
         #   111, 113, 119, 120, 156, 159, 272, 273 -- chain tensioner torques
         #   incl. the C 230 M272.
         expect=["10006819"], expect_engine=True),
    dict(id="w204-c220cdi-glow-plugs", series="204", variant=22,
         variant_name="C 220 CDI Lim.", query="remove install glow plugs",
         # 7115453 "Remove/install glow plugs", MODEL 166, 172, 204, 207, 212,
         #   218, 221; ENGINE 651 ("Shown on engine 651.911"). The C 220 CDI
         #   (204.002) is an OM651.
         expect=["7115453"], expect_engine=True),
    dict(id="w205-c220d-timing-chain", series="205", variant=20,
         variant_name="C 220 d", query="timing chain",
         # OM651, the engine whose number collides with a truck series.
         # 9005055 "Timing chain check values", "Engine 651 in model 117, 156,
         #   166, 172, 176, 204, 205, ...". 10009101 "Timing chain drive,
         #   toothed belt drive", ENGINE 651 torques.
         expect=["9005055", "10009101"], expect_engine=True),
    # guards only: no capacity page names both 205 and OM651; the OM651
    # bucket's capacity pages are for 117/156/176/246 and Sprinter/Vito
    dict(id="w205-c220d-oil-capacity", series="205", variant=20,
         variant_name="C 220 d", query="engine oil capacity",
         expect_engine=True),

    # --- W211 E-Class: the slash-list bug ------------------------------------
    dict(id="w211-e320cdi-manual-trans", series="211", variant=15,
         variant_name="E 320 CDI", query="manual transmission synchronizer",
         # 18086779 "Manual transmission (MG), function" -- 716.6 in MODEL 170,
         #   171, 203, 208, 209, 210, 211. 18086793 "Multiple cone
         #   synchronization", same applicability. Forbidden: 18086949 and
         #   18086954, the Sprinter NSG 400 pages whose applicability is
         #   "MODEL 906.111 /113 ... /211 /213" -- variants of the 906, which
         #   the old parser filed under the E-Class (211) and the E (213).
         expect=["18086779", "18086793"], forbid=["18086949", "18086954"]),
    dict(id="w211-e320cdi-limp-home", series="211", variant=15,
         variant_name="E 320 CDI", query="automatic transmission limp-home mode",
         # 18088069 "Emergency default mode, function", MODEL 171, 203, 209,
         #   211 ... Forbidden: 18090614 "Limp-home mode, function", Applies to
         #   MODEL 906; ENGINE 642 -- a Sprinter page, reachable only through
         #   the OM642 engine bucket.
         expect=["18088069"], forbid=["18090614"]),
    # --- EPC parts joined into the WIS car (skipped without EPC in the index).
    # Text, not page numbers: EPC page numbers follow the pull's dok_id order.
    dict(id="epc-w124-part-number", series="124", query="A 000 420 99 20",
         # "TS BRAKE PAD, PARTS KIT" on the W124 FRONT WHEEL BRAKE parts page
         parts=True, expect_parts=True, expect_text=["A 000 420 99 20"]),
    dict(id="epc-w203-brake-pads", series="203", query="brake pad",
         # group 42 BRAKES / subgroup 030 FRONT WHEEL BRAKE, read by hand
         parts=True, expect_parts=True, expect_text=["FRONT WHEEL BRAKE"]),
    # engine parts, reached through the chosen variant's engines (read by hand
    # 2026-10-15 on WIS + all 443 EPC catalogs)
    dict(id="epc-w210-e320-oil-filter", series="210", variant=30, variant_name="E 320",
         query="oil filter",
         # M112 group 18 / 060: "TS OIL FILTER ELEMENT" A 000 180 26 09 (the
         # current number; 31 09 is replaced by it). E 320 spans M104 and M112.
         parts=True, expect_parts=True, expect_text=["A 000 180 26 09"]),
    dict(id="epc-w201-2316-thermostat", series="201", variant=8,
         variant_name="190 E 2.3 16", query="thermostat",
         # M102 group 20 ENGINE COOLING SYSTEM (thermostat seal, housing cover)
         parts=True, expect_parts=True, expect_text=["THERMOSTAT HOUSING COVER"]),
    dict(id="epc-w204-c220cdi-glow-plugs", series="204", variant=22,
         variant_name="C 220 CDI Lim.", query="glow plug",
         # OM651 group 15 / 105: GLOW PLUG qty 4, A 001 159 66 01
         parts=True, expect_parts=True, expect_text=["A 001 159 66 01"]),
    dict(id="epc-w211-e320cdi-fuel-filter", series="211", variant=15,
         variant_name="E 320 CDI", query="fuel filter",
         # OM642 group 07 FUEL INJECTION / subgroup 110 FUEL FILTER, LINES
         parts=True, expect_parts=True, expect_text=["FUEL FILTER, LINES"]),
]

GUARDS =("commercial", "wrong", "foreign")
CEILING_TOP = 10  # known-bad ceilings below were measured at --top 10

# Why a guard is known-bad. Measured 2026-10-01; ceilings re-set on the schema 1.2
# index (3.3 GB). Only one rose: w201 cyl-head foreign 1 -> 2, one general-pool
# manual-transmission page (122/124/129/140/163, no W_Dok2Reihe row) as two chunks.
REASONS = {
    "general-engine": "the general pool is narrowed only by vehicle-type bit, "
        "so untied engine documents for other engines (OM642, M276, OM668 ...) "
        "share the car's bit and pass",
    "general-untied": "documents whose applicability is only in the title or "
        "body (\"Model 126.04 ...\", \"Model 163.113/...\") have no W_Dok2Reihe "
        "link and land in the general pool, so other models' procedures reach "
        "this car",
    "engine-bucket": "engine buckets are not type-narrowed: an engine bucket "
        "holds every vehicle with that engine -- G-Class wiring diagrams, "
        "Sprinter/Vario/T1N pages, W210 diagrams -- and they reach this car",
}

# (case id, guard) -> (ceiling, reason). See "KNOWN-BAD, AS A RATCHET" above.
KNOWN_BAD: dict[tuple[str, str], tuple[int, str]] = {
    ("w201-2316-cyl-head-bolts", "wrong"): (2, "general-engine"),
    ("w201-2316-cyl-head-bolts", "foreign"): (2, "general-untied"),
    ("w201-2316-break-in-oils", "wrong"): (1, "general-untied"),
    ("w201-2316-break-in-oils", "foreign"): (1, "general-untied"),
    ("w126-series-brake-pads", "foreign"): (5, "engine-bucket"),
    ("w124-series-cyl-head", "foreign"): (1, "engine-bucket"),
    ("w126-560sel-timing-chain", "wrong"): (2, "general-engine"),
    ("r107-560sl-chain-tensioner", "wrong"): (4, "general-engine"),
    ("r107-560sl-chain-tensioner", "foreign"): (1, "general-engine"),
    ("w140-s500-front-brake-pads", "foreign"): (2, "general-untied+engine-bucket"),
    ("w204-c220cdi-glow-plugs", "foreign"): (1, "general-engine+general-untied"),
    ("w211-e320cdi-manual-trans", "wrong"): (1, "general-untied"),
    ("w211-e320cdi-manual-trans", "foreign"): (1, "general-untied+engine-bucket"),
    # WIS pages (brake-light switch, ME-SFI) via the whole-series engine
    # buckets E111/E112/E113 -- not EPC results (measured 2026-10-14)
    ("epc-w203-brake-pads", "foreign"): (3, "engine-bucket"),
    # the part number's digits match an OM605 end-cover page loosely
    ("epc-w124-part-number", "foreign"): (1, "engine-bucket"),
    # WIS general-pool W207 pages (oil filter cover, transmission oil change),
    # not EPC results (measured 2026-10-15, WIS + all 443 EPC catalogs)
    ("epc-w210-e320-oil-filter", "wrong"): (1, "general-untied"),
    ("epc-w210-e320-oil-filter", "foreign"): (1, "general-untied"),
    ("epc-w204-c220cdi-glow-plugs", "foreign"): (1, "general-untied"),
}


def _vehicles(conn) -> dict[str, dict]:
    """Every vehicle, with the parts-catalogue join (EPC) the app makes."""
    return {v["doc_id"]: v for v in link_sources(
        [dict(r) for r in conn.execute("SELECT * FROM vehicles")])}


def _short(doc_id: str) -> str:
    return doc_id[len(PREFIX):] if doc_id.startswith(PREFIX) else doc_id


def run(cases: list[dict], index_path: str, top_n: int, verbose: bool) -> int:
    uri = Path(index_path).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    texts: dict[str, str] = {}

    def page_text(row) -> str:
        uid = row["page_uid"]
        if uid not in texts:
            t = conn.execute("SELECT title, text FROM pages WHERE page_uid=?",
                             (uid,)).fetchone()
            body = t["text"] if t else ""
            # A parts page (EPC) states what it is for in its header; below the
            # table header, part lines and footnotes use EPC's shorthand: "FOR
            # MODEL 970-975" there means engines 119.970-975, not trucks 970/975.
            cut = re.search(r"\n(?:Callout|Item) \| Part number \|", body)
            if cut:
                body = body[:cut.start()]
            texts[uid] = f"{t['title']}\n{body}" if t else ""
        return texts[uid]

    def full_text(row) -> str:
        t = conn.execute("SELECT title, text FROM pages WHERE page_uid=?",
                         (row["page_uid"],)).fetchone()
        return f"{t['title']}\n{t['text']}" if t else ""

    failures = known = 0
    reasons_seen: set[str] = set()
    ratchet = top_n == CEILING_TOP
    started = time.time()
    print("WIS retrieval check -- no API calls, index opened read-only")
    print(f"(top {top_n} per case)\n")

    allv = _vehicles(conn)
    has_parts = any(v.get("doc_type") == "parts" for v in allv.values())
    for case in cases:
        cid = case["id"]
        if case.get("parts") and not has_parts:
            print(f"  SKIP  {cid:<30} no parts catalogue (EPC) in this index")
            continue
        vehicle = allv.get(PREFIX + case["series"])
        if vehicle is None:
            failures += 1
            print(f"  FAIL  {cid:<30} vehicle {PREFIX}{case['series']} not indexed")
            continue
        variants = json.loads(vehicle.get("variants") or "[]")
        var = case.get("variant")
        if var is not None and case.get("variant_name") and (
                var >= len(variants) or variants[var]["name"] != case["variant_name"]):
            failures += 1
            print(f"  FAIL  {cid:<30} variant {var} is no longer "
                  f"{case['variant_name']!r} -- variants list changed")
            continue
        scope = scope_ids(vehicle, var, case.get("engine"))
        series = case["series"]
        # joined parts catalogue buckets (EPC): doc_id -> "210" or "E102"
        parts = {d: allv[d]["model_code"] for d in scope
                 if allv.get(d, {}).get("doc_type") == "parts"}
        engine_ids = [d for d in scope
                      if d not in (vehicle["doc_id"], scope.general) and d not in parts]
        engines = ({_short(d).lstrip("e") for d in engine_ids}
                   | {c[1:] for c in parts.values() if c.startswith("E")})
        compatible = {series} | engines

        t0 = time.time()
        rows = search(conn, scope, case["query"], top_n)
        elapsed = time.time() - t0

        problems: list[str] = []
        offenders: dict[str, list[str]] = {g: [] for g in GUARDS}

        # dedup
        keys = [f"{r['page_no']}#{r['ord']}" for r in rows]
        dups = sorted({k for k in keys if keys.count(k) > 1})
        if dups:
            problems.append(f"dedup: same chunk returned twice {dups}")

        # expected pages
        pages = [str(r["page_no"]) for r in rows]
        want = case.get("expect", [])
        hit = next(((p, pages.index(p) + 1) for p in pages if p in want), None)
        if want and not hit:
            problems.append(f"expect: none of {want} in top {top_n} {pages}")

        bad = [p for p in case.get("forbid", []) if p in pages]
        if bad:
            problems.append(f"forbid: {bad} returned (ranks "
                            f"{[pages.index(p) + 1 for p in bad]})")

        # parts: a joined catalogue bucket contributes, and the text sought
        # (a part number read by hand) is on a returned page
        if case.get("expect_parts") and not any(r["doc_id"] in parts for r in rows):
            problems.append(f"parts: no result from {sorted(parts.values())}")
        for needle in case.get("expect_text", []):
            if not any(needle in full_text(r) for r in rows):
                problems.append(f"expect_text: {needle!r} on none of the top {top_n}")

        # engine bucket contributes
        if case.get("expect_engine") and not any(r["doc_id"] in engine_ids for r in rows):
            problems.append(f"engine: no result from {[_short(d) for d in engine_ids]}")

        for rank, r in enumerate(rows, 1):
            models, engs = named_codes(page_text(r))
            named = models | engs
            tag = f"#{rank} {r['page_no']} [{_short(r['doc_id'])}] {r['title'][:50].strip()}"
            # filing: a page in a bucket must name what that bucket is
            if r["doc_id"] == vehicle["doc_id"] and series not in named:
                problems.append(f"filing: {tag} is in {series}'s bucket but "
                                f"names {sorted(named) or 'nothing'}")
            elif r["doc_id"] in parts:
                code = parts[r["doc_id"]]
                need = code[1:] if code.startswith("E") else code
                if need not in named:
                    problems.append(f"filing: {tag} is in parts bucket {code} but "
                                    f"names {sorted(named) or 'nothing'}")
            elif r["doc_id"] in engine_ids:
                eng = _short(r["doc_id"]).lstrip("e")
                if eng not in named:
                    problems.append(f"filing: {tag} is in engine {eng}'s bucket "
                                    f"but names {sorted(named) or 'nothing'}")
            if models and models <= COMMERCIAL and series not in models:
                offenders["commercial"].append(f"{tag} models {sorted(models)}")
            if named and not (named & compatible):
                offenders["wrong"].append(f"{tag} names {sorted(named)[:8]}")
            if models and series not in models:
                offenders["foreign"].append(f"{tag} models {sorted(models)[:8]}")

        notes: list[str] = []
        for guard in GUARDS:
            n = len(offenders[guard])
            ceiling, reason = KNOWN_BAD.get((cid, guard), (0, ""))
            if reason:
                reasons_seen.add(reason)
            if not ratchet:
                if n:
                    notes.append(f"{guard}: {n} offending result(s) at top {top_n} "
                                 f"(ceilings are for top {CEILING_TOP}; not scored)")
            elif n > ceiling:
                label = "regression past known-bad ceiling" if ceiling else "guard"
                problems.append(f"{guard} {label}: {n} offending result(s)"
                                + (f" (ceiling {ceiling})" if ceiling else ""))
                problems.extend(f"    {o}" for o in offenders[guard])
            elif ceiling:
                known += 1
                state = ("FIXED -- delete the KNOWN_BAD entry" if n == 0 else
                         f"IMPROVED -- lower ceiling to {n}" if n < ceiling else
                         "unchanged")
                notes.append(f"known-bad {guard:<10} {n}/{top_n} (ceiling {ceiling}, "
                             f"{state}) [{reason}]")
                if verbose:
                    notes.extend(f"    {o}" for o in offenders[guard])

        where = f"{hit[0]} at rank {hit[1]}" if hit else "guards only"
        if problems:
            failures += 1
            print(f"  FAIL  {cid:<30} {elapsed:4.1f}s")
            for p in problems:
                print(f"          - {p}")
        else:
            print(f"  PASS  {cid:<30} {elapsed:4.1f}s  {where}")
        for n in notes:
            print(f"        {n}")
        if verbose:
            for rank, r in enumerate(rows, 1):
                m, e = named_codes(page_text(r))
                print(f"          {rank:2} {r['page_no']:>9} {_short(r['doc_id']):8} "
                      f"{r['title'][:52].strip():52} M{sorted(m)[:6]} E{sorted(e)[:6]}")

    conn.close()
    if reasons_seen:
        print("\nknown-bad reasons:")
        for key in sorted({k for r in reasons_seen for k in r.split("+")}):
            print(f"  [{key}] {REASONS[key]}")
    print(f"\n{len(cases)} case(s) in {time.time() - started:.1f}s, "
          f"{known} known-bad guard(s) within ceiling")
    print(f"{failures} failure(s)")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="FREE retrieval check for the Mercedes WIS source. "
                    "No API calls; opens the index read-only.")
    ap.add_argument("--index", default=None,
                    help="path to index.db (defaults to the configured one)")
    ap.add_argument("--case", action="append", default=None,
                    help="run only this case id; repeatable")
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="print every result and known-bad offender")
    args = ap.parse_args(argv)

    cases = CASES
    if args.case:
        cases = [c for c in CASES if c["id"] in set(args.case)]
        if not cases:
            print(f"no case matches {args.case}; have {[c['id'] for c in CASES]}",
                  file=sys.stderr)
            return 1
    return run(cases, args.index or config.load().index_path, args.top, args.verbose)


if __name__ == "__main__":
    raise SystemExit(main())
