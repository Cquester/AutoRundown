#!/usr/bin/env python3
"""AutoRundown extractor - VEHICLE parse: raw vehicle tables -> variants.jsonl.

Reads the TSVs written by pull_vehicles.js and writes, into the corpus dir
(next to records.jsonl), a sidecar AutoRundown reads (IMPORT_SPEC section 3b):

  variants.jsonl   one line per (model series, sales name):
                     {"model_series": "201", "name": "190 E 2.3 16",
                      "baumuster": ["201.034"], "wmi": ["WDB"],
                      "engines": ["102.983"],
                      "body_by_bm": {"201.034": "Sedan"}}
  vin_us.json      US VIN decoding: {"WDB": {"D": "201", ...}, ...}
                   A US VIN carries a coded letter at position 4, not the
                   Baumuster a European FIN carries at positions 4-9.

and records both in manifest.json ("variants_file", "vin_us_file").

Deliberately NOT emitted: model years. ASRA_FahrzeugII has model-year columns
but only 317 of 92,787 rows fill them (engine series only), and its
ProduktionVon/Bis are chassis-number ranges ("034959".."999999"), not dates.
Per-Baumuster dates from W_Baumusterzeile were checked too and are worse than
the series span: 124.030 (300E, 1985-1993) comes out 1990-1995, which would
hide the car from a 1987 filter. Years stay at series level (models.jsonl).

Config via environment:
  AR_VEH   dir holding the pulled TSVs  (default <out root>/wis_vehicles)
  AR_OUT   corpus dir to write into     (default <out root>/wis_extract)
"""
import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, str(HERE))  # the installed app's Python does not add a script's folder
from wis_paths import out_root  # noqa: E402  (out root: see wis_paths.py)
VEH = os.environ.get("AR_VEH") or os.path.join(out_root(), "wis_vehicles")
OUT = os.environ.get("AR_OUT") or os.path.join(out_root(), "wis_extract")

csv.field_size_limit(10_000_000)


def rows(name):
    path = os.path.join(VEH, name + ".tsv")
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        yield from csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE)


# The Transbase driver decodes the DB's UTF-8 as Windows-1252, so "Coupé"
# arrives as "CoupÃ©" and "Ü" as "Ãœ" (~1,000 names). Undo exactly that: a
# run is rewritten only when its bytes are valid UTF-8, so correct text stays.
_MOJIBAKE = re.compile(
    "[Â-ô]"
    "[\u0080-¿ŒœŠšŸŽžƒˆ˜"
    "–—‘-„†-•…‰‹›€™]+")


def fix_mojibake(text):
    if not text or text.isascii():
        return text
    def one(m):
        try:
            # cp1252 for its own glyphs, Latin-1 (raw C1 controls) for the rest
            return bytes(ord(c) if ord(c) < 256 else c.encode("cp1252")[0]
                         for c in m.group(0)).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return m.group(0)
    return _MOJIBAKE.sub(one, text)


# ASRA SparteAbk: 00 = passenger cars (incl. G-Class, Vito/V-Class MPVs,
# smart), 10 = commercial (Sprinter/T1 vans, trucks, buses, Unimog).
DIVISIONS = {"00": "car", "10": "commercial"}

# "TYPEN 308" = "types 308": a catalogue placeholder, not a sales name.
PLACEHOLDER = re.compile(r"^TYPEN\s+\d+$", re.I)


def vehicle_types_by_baumuster():
    """AW_Fahrzeuge.FzgArtCode -> English FzgArt name, per 6-digit Baumuster.

    Mercedes' own vehicle type: CAR, 4xWD, Light/Heavy transporter, TRUCK,
    BUS, UNIMOG, smart. Finer than SparteAbk, and right where SparteAbk is
    not -- the Vito (639) is sparte 00 but a Light transporter.
    """
    names = {r["FzgArtCode"]: r["FzgArtName"].strip()
             for r in rows("FzgArt") if r["SprachCode"].strip() == "2"}
    by, by_series = defaultdict(Counter), defaultdict(Counter)
    for r in rows("AW_Fahrzeuge"):
        t = names.get(r["FzgArtCode"].strip())
        if t:
            bm = dotted(r["TypBM"].strip())
            by[bm][t] += 1
            by_series[bm[:3]][t] += 1
    # exact Baumuster first; else the series' majority (ASRA and AW often
    # list different Baumuster of the same series -- W123, W107, bus 629)
    exact = {bm: c.most_common(1)[0][0] for bm, c in by.items()}
    series = {s: c.most_common(1)[0][0] for s, c in by_series.items()}
    return exact, series


def dotted(code):
    """'201034' -> '201.034'; anything else unchanged."""
    code = (code or "").strip()
    return f"{code[:3]}.{code[3:]}" if len(code) == 6 and code.isdigit() else code


def vehicle_type(v, series, name, exact, by_series):
    """Exact Baumuster, then the series' majority, then one name rule.

    Mercedes names Unimogs "U <number>"; where the type is known, that prefix
    is UNIMOG in 75 of 75 cases, so it's used for the old ones AW_Fahrzeuge
    lacks. The matching "O <number>" = Omnibus rule is NOT used: only 78%
    agree (the rest are van-based minibuses), too weak to assert "bus".
    """
    t = Counter(exact[b] for b in v["baumuster"] if b in exact).most_common(1)
    if t:
        return t[0][0]
    if by_series.get(series):
        return by_series[series]
    return "UNIMOG" if re.match(r"^U\s?\d", name) else ""


# Body style from ASRA_Typkennzahl's names (the 'type-number' of each vehicle).
# One sales name covers several bodies -- "E 320 4MATIC" is both the W210
# sedan (210.082) and the wagon (210.282) -- and the importer splits such a
# variant per body, so the picker can tell a wagon from a sedan. First match
# wins; English and German, since which language the table holds varies.
BODY_STYLES = [
    ("Shooting Brake", r"shooting"),
    ("Wagon", r"\bwagon|estate|\bt-?\s?modell?\b|\bkombi"),
    ("Convertible", r"cabrio|convertible"),
    ("Roadster", r"roadster"),
    ("Coupe", r"coup"),
    ("Sedan", r"saloon|sedan|limousine"),
]


def body_style(name):
    for body, rx in BODY_STYLES:
        if re.search(rx, name or "", re.I):
            return body
    return ""


def columns(name):
    path = os.path.join(VEH, name + ".tsv")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        return f.readline().rstrip("\r\n").split("\t")


def body_by_baumuster():
    """Baumuster ("210.282") -> body style ("Wagon"), from ASRA_Typkennzahl.

    The vehicle rows carry the type-number key (division, family, type
    number, country); ASRA_Typkennzahl names it per language. Column names
    are taken from the files, and every step that can't be made says so --
    no body is better than a guessed one. Returns ({}, reason) when unusable.
    """
    tk_cols = columns("ASRA_Typkennzahl")
    if not tk_cols:
        return {}, "ASRA_Typkennzahl.tsv not pulled"
    keys = [c for c in ("SparteAbk", "FamilieAbk", "TypkennzahlAbk", "ProduktionslandAbk")
            if c in tk_cols]
    name_col = "TypkennzahlName" if "TypkennzahlName" in tk_cols else \
        next((c for c in tk_cols if "name" in c.lower()), "")
    lang_col = next((c for c in tk_cols if c.lower().startswith("sprach")), "")
    if "TypkennzahlAbk" not in keys or not name_col:
        return {}, f"ASRA_Typkennzahl has no type-number key / name (columns: {tk_cols})"
    table = next((t for t in ("ASRA_FahrzeugII", "ASRA_Fahrzeug")
                  if set(keys) | {"TypBM"} <= set(columns(t))), "")
    if not table:
        return {}, f"no vehicle table carries {keys}"

    # key -> language -> body; the language that names most bodies is preferred
    named = defaultdict(dict)
    per_lang = Counter()
    for r in rows("ASRA_Typkennzahl"):
        k = tuple(r[c].strip() for c in keys)
        lang = r[lang_col].strip() if lang_col else ""
        b = body_style(fix_mojibake(r[name_col]))
        if b:
            named[k][lang] = b
            per_lang[lang] += 1
    order = [lang for lang, _ in per_lang.most_common()]

    votes = defaultdict(Counter)
    for r in rows(table):
        bm = r["TypBM"].strip()
        if len(bm) != 6 or not bm.isdigit():
            continue
        langs = named.get(tuple(r[c].strip() for c in keys))
        if langs:
            votes[dotted(bm)][next(langs[l] for l in order if l in langs)] += 1
    out = {bm: c.most_common(1)[0][0] for bm, c in votes.items()}
    return out, (f"{table} x ASRA_Typkennzahl on {'+'.join(keys)}, "
                 f"language {order[0] if order else '?'}")


def main():
    # series -> name -> {"baumuster": set, "wmi": set, "engines": set}
    variants = defaultdict(lambda: defaultdict(lambda: {"baumuster": set(), "wmi": set(),
                                                        "engines": set(),
                                                        # Baumuster -> its own engines: one
                                                        # sales name can span engines (E 350:
                                                        # M272 to 2011, M276 after), and only
                                                        # the exact model says which
                                                        "engine_by_bm": defaultdict(set),
                                                        "division": Counter()}))
    source = defaultdict(set)

    # 1) ASRA_FahrzeugII: one row per vehicle, duplicated per steering side and
    #    engine; the sales name is always filled
    for r in rows("ASRA_FahrzeugII"):
        bm = r["TypBM"].strip()
        name = fix_mojibake(" ".join(r["Verkaufsbezeichnung"].split()))
        if len(bm) != 6 or not bm[:3].isdigit() or not name or PLACEHOLDER.match(name):
            continue
        v = variants[bm[:3]][name]
        v["baumuster"].add(dotted(bm))
        if r["SparteAbk"].strip() in DIVISIONS:
            v["division"][DIVISIONS[r["SparteAbk"].strip()]] += 1
        if r["WerkherstellercodeAbk"].strip():
            v["wmi"].add(r["WerkherstellercodeAbk"].strip())
        for e in (r["MotorMin"], r["MotorMax"]):
            if e.strip():
                v["engines"].add(dotted(e.strip()[:6]))
                v["engine_by_bm"][dotted(bm)].add(dotted(e.strip()[:6]))
        source[bm[:3]].add("ASRA")

    # 2) AW_Fahrzeuge covers some older series ASRA lacks; its sales field can
    #    hold several names comma-joined ("190 E,190 E 2.0")
    for r in rows("AW_Fahrzeuge"):
        bm = r["TypBM"].strip()
        if len(bm) != 6 or not bm[:3].isdigit() or "ASRA" in source[bm[:3]]:
            continue      # ASRA's per-vehicle rows win where both exist
        names = [fix_mojibake(" ".join(n.split()))
                 for n in (r["TypBMVerkaufsBez"] or "").split(",")]
        names = [n for n in names if n] or [fix_mojibake(" ".join(r["TypBMName"].split()))]
        names = [n for n in names if not PLACEHOLDER.match(n)]
        for name in names:
            if not name:
                continue
            v = variants[bm[:3]][name]
            v["baumuster"].add(dotted(bm))
            if r["MotorBM"].strip():
                v["engines"].add(dotted(r["MotorBM"].strip()))
                v["engine_by_bm"][dotted(bm)].add(dotted(r["MotorBM"].strip()))
            source[bm[:3]].add("AW")

    # 3) US VIN map: WMI + position-4 letter -> series
    vin_us = defaultdict(dict)
    for r in rows("USTypBM2TypBM"):
        vin_us[r["WMI"].strip()][r["USTypBMReihe"].strip()] = r["TypBMReihe"].strip()

    # 3b) Mercedes' own US VIN decoder (AW_VIN2FIN): the first 9 characters of a
    #     US VIN -> the first 9 of the FIN, i.e. WMI + Baumuster. Pins the exact
    #     variant, where vin_us.json only pins the series. Optional table.
    vin2fin = {}
    if os.path.exists(os.path.join(VEH, "AW_VIN2FIN.tsv")):
        for r in rows("AW_VIN2FIN"):
            vin, fin = r["VIN"].strip().upper(), r["FIN"].strip()
            if len(vin) == 9 and len(fin) == 9 and fin[3:].isdigit():
                vin2fin[vin] = dotted(fin[3:])

    vtype, vtype_series = vehicle_types_by_baumuster()
    bodies, body_how = body_by_baumuster()

    os.makedirs(OUT, exist_ok=True)
    n = 0
    with open(os.path.join(OUT, "variants.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for series in sorted(variants):
            for name in sorted(variants[series]):
                v = variants[series][name]
                f.write(json.dumps({"model_series": series, "name": name,
                                    "baumuster": sorted(v["baumuster"]),
                                    "wmi": sorted(v["wmi"]),
                                    "engines": sorted(v["engines"]),
                                    "engine_by_bm": {b: sorted(e) for b, e
                                                     in sorted(v["engine_by_bm"].items())},
                                    # body style per Baumuster, where known
                                    "body_by_bm": {b: bodies[b] for b
                                                   in sorted(v["baumuster"]) if b in bodies},
                                    # "" when only AW_Fahrzeuge knew it
                                    "division": (v["division"].most_common(1)[0][0]
                                                 if v["division"] else ""),
                                    # Mercedes' vehicle type (FzgArt), "" if unknown
                                    "vehicle_type": vehicle_type(v, series, name,
                                                                 vtype, vtype_series)},
                                   ensure_ascii=False) + "\n")
                n += 1
    with open(os.path.join(OUT, "vin_us.json"), "w", encoding="utf-8") as f:
        json.dump(vin_us, f, indent=1, sort_keys=True)
    if vin2fin:
        with open(os.path.join(OUT, "vin2fin.json"), "w", encoding="utf-8") as f:
            json.dump(vin2fin, f, indent=0, sort_keys=True)

    mpath = os.path.join(OUT, "manifest.json")
    if os.path.exists(mpath):
        with open(mpath, encoding="utf-8") as f:
            manifest = json.load(f)
        manifest["variants_file"] = "variants.jsonl"
        manifest["variant_count"] = n
        manifest["vin_us_file"] = "vin_us.json"
        if vin2fin:
            manifest["vin2fin_file"] = "vin2fin.json"
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)

    only_aw = sum(1 for s in source if source[s] == {"AW"})
    print(f"variants: {n} across {len(variants)} series "
          f"({len(variants) - only_aw} from ASRA_FahrzeugII, {only_aw} only in AW_Fahrzeuge)")
    print(f"US VIN map: {sum(len(v) for v in vin_us.values())} codes across {len(vin_us)} WMIs")
    print(f"US VIN -> Baumuster (AW_VIN2FIN): {len(vin2fin)} VIN prefixes, "
          f"{len(set(vin2fin.values()))} Baumuster")
    # body styles: printed with a real case to check by eye (W210 E 320
    # 4MATIC = sedan 210.082 + wagon 210.282), since a wrong join is silent
    if bodies:
        print(f"body styles: {len(bodies)} Baumuster ({body_how}): "
              + ", ".join(f"{b} {n}" for b, n in Counter(bodies.values()).most_common()))
        mixed = [(s, name) for s in variants for name, v in variants[s].items()
                 if len({bodies[b] for b in v["baumuster"] if b in bodies}) > 1]
        print(f"  {len(mixed)} sales names span several bodies (split in the picker)")
        for s, name in [m for m in mixed if m[0] == "210"][:3] or mixed[:3]:
            v = variants[s][name]
            print(f"  e.g. {s} {name}: " + ", ".join(
                f"{b} {bodies.get(b, '?')}" for b in sorted(v["baumuster"])))
    else:
        print(f"body styles: none ({body_how}) -- the picker cannot split sedan/wagon")
    print(f"-> {os.path.join(OUT, 'variants.jsonl')}")


if __name__ == "__main__":
    sys.exit(main())
