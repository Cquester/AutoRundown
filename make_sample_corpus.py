"""Generate a stand-in WIS corpus, for working on the UI without the VM.

    python make_sample_corpus.py [--catalogue <models.jsonl>] [--per-series N]
    python ingest.py --archive sample_corpus --index sample_wis.db
    python serve.py --index sample_wis.db

The prose is invented -- **this is NOT Mercedes manual content**. What is
faithful is the record *shape* (IMPORT_SPEC.md sections 3 and 3a) and, when a
real `models.jsonl` is available, the vehicle catalogue: real Baureihe codes,
real production spans, real Baumuster. That makes the picker and the VIN
lookup behave as they will on the real corpus while the document pull is
still outstanding.

By default it looks for the catalogue preserved from the applicability pull at
`../Mercedes Documents/autorundown_out/wis_applic/models.jsonl` and falls back
to a small invented one. Free: no API calls, no VM, no licensed content.
"""
import argparse
import json
import random
from pathlib import Path

OUT = Path(__file__).resolve().parent / "sample_corpus" / "wis_en"

random.seed(7)

MODELS = ["201", "176", "246", "204", "205", "211", "212", "213", "222",
          "463", "901", "902", "903", "906", "639", "447"]

# Stand-in for `models.jsonl` (IMPORT_SPEC 3a), rolled up from
# W_Baumusterzeile in the real thing: series -> (production span, Baumuster).
# Note there is NO sales designation here -- W_Baumusterzeile does not carry
# one, so labels stay bare model codes. Approximate, for shape only.
#
# 201 (the W201 190/190E) is deliberately included: it is the pre-1990 case
# the spec warns about, whose documents rarely name the model in prose.
REAL_CATALOGUE = (Path(__file__).resolve().parent.parent
                  / "Mercedes Documents" / "autorundown_out" / "wis_applic"
                  / "models.jsonl")


def load_catalogue(path):
    """models.jsonl -> {reihe: (year_from, year_to, [baumuster])}."""
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            e = json.loads(line)
            reihe = str(e.get("reihe") or "").strip()
            if reihe:
                out[reihe] = (e.get("year_from"), e.get("year_to"),
                              e.get("baumuster") or [])
    return out


FALLBACK_MODEL_DATA = {
    "201": ("1983-10-01", "2000-04-30", ["201.018", "201.024", "201.034"]),
    "176": ("2012-06-01", "2018-05-31", ["176.042", "176.052"]),
    "246": ("2011-11-01", "2018-12-31", ["246.242"]),
    "204": ("2007-01-01", "2014-12-31", ["204.041", "204.077"]),
    "205": ("2014-03-01", "2021-08-31", ["205.045"]),
    "211": ("2002-03-01", "2009-03-31", ["211.061", "211.082"]),
    "212": ("2009-03-01", "2016-12-31", ["212.005"]),
    "213": ("2016-04-01", None, ["213.004"]),
    "222": ("2013-09-01", "2020-09-30", ["222.057"]),
    "463": ("1990-01-01", None, ["463.200", "463.349"]),
    "901": ("1995-02-01", "2006-05-31", ["901.611"]),
    "902": ("1995-02-01", "2006-05-31", ["902.612"]),
    "903": ("1995-02-01", "2006-05-31", ["903.612"]),
    "906": ("2006-05-01", "2018-06-30", ["906.633"]),
    "639": ("2003-08-01", "2015-06-30", ["639.701"]),
    "447": ("2014-09-01", None, ["447.601"]),
    "124": ("1984-11-01", "1997-06-30", ["124.030", "124.032"]),
    "164": ("2005-03-01", "2011-12-31", ["164.186", "164.177"]),
    "166": ("2011-07-01", "2019-06-30", ["166.057", "166.073"]),
    "210": ("1995-06-01", "2003-03-31", ["210.082", "210.282"]),
}

# Named models per series (IMPORT_SPEC 3b `variants.jsonl`), so the picker's
# Variant menu, the one-line car box and VIN lookup have something to work
# on -- test_ui_picks.py needs them. Hand-made from public knowledge (sales
# names, model and engine codes as printed on the cars and in
# importers/data/mercedes_us_years.csv), NOT copied from the WIS tables.
# Chosen to exercise what broke before: W211 "E 500/550 4MATIC" next to the
# E 63 AMG (the sorted-index bug), a W212 E 350 that is M272 or M276 by
# Baumuster, and a W210 name spanning sedan and wagon (split_by_body).
SAMPLE_VARIANTS = [
    # series, name, {Baumuster: [engines]}, {Baumuster: body}
    ("201", "190 E 2.3 16", {"201.034": ["102.983"]}, {}),
    ("201", "190 E 2.3", {"201.028": ["102.985"]}, {}),
    ("201", "190 E 2.6", {"201.029": ["103.942"]}, {}),
    ("201", "190 D 2.5", {"201.126": ["602.911"]}, {}),
    ("124", "300 E", {"124.030": ["103.983"]}, {}),
    ("124", "E 320", {"124.032": ["104.992"]}, {}),
    ("210", "E 320 4MATIC", {"210.082": ["112.941"], "210.282": ["112.941"]},
     {"210.082": "Sedan", "210.282": "Wagon"}),
    ("204", "C 300", {"204.054": ["272.948"]}, {}),
    ("204", "C 300 4MATIC", {"204.080": ["272.948"]}, {}),
    ("204", "C 63 AMG", {"204.077": ["156.985"]}, {}),
    ("211", "E 350", {"211.056": ["272.964"]}, {}),
    ("211", "E 350 4MATIC", {"211.087": ["272.964"]}, {}),
    ("211", "E 500/550 4MATIC", {"211.083": ["113.967"], "211.090": ["273.924"]},
     {"211.083": "Sedan", "211.090": "Sedan"}),
    ("211", "E 550", {"211.072": ["273.923"]}, {}),
    ("211", "E 63 AMG", {"211.077": ["156.983"]}, {}),
    ("212", "E 350", {"212.056": ["272.977"], "212.059": ["276.952"]}, {}),
    ("212", "E 550 4MATIC", {"212.072": ["273.971"]}, {}),
    ("164", "ML 350", {"164.186": ["272.967"]}, {}),
    ("164", "ML 63 AMG", {"164.177": ["156.980"]}, {}),
    ("166", "ML 350", {"166.057": ["276.820"]}, {}),
    ("166", "ML 550 4MATIC", {"166.073": ["278.928"]}, {}),
]

# US VIN tables (IMPORT_SPEC 3b): position 4 letter -> series, and the
# first 9 VIN characters -> Baumuster. The two prefixes are the documented
# examples in CLAUDE.md; enough for the VIN box and dialog.
SAMPLE_VIN_US = {"WDB": {"D": "201", "E": "124", "J": "210", "U": "211"}}
SAMPLE_VIN2FIN = {"WDBDA34D4": "201.034", "WDDGF8AB3": "204.080"}

# Engine documents tied to no car: filed under the engine (E102 ...), which a
# car reaches through its variants' engines.
SAMPLE_ENGINE_DOCS = [("102", "Cylinder head cover, engine 102"),
                      ("272", "Cylinder head cover, engine 272"),
                      ("273", "Timing chain, engine 273"),
                      ("276", "Spark plugs, engine 276")]

SYSTEMS = [
    ("Front axle", [
        ("Remove/install front brake pads", "AR42.10-P-{n:04d}A",
         "Raise the vehicle and remove the front wheels.\nPress the brake "
         "caliper piston back with the resetting tool.\nRemove the retaining "
         "spring and drive out the retaining pins.\nWithdraw the brake pads.\n"
         "Tighten the brake caliper to steering knuckle bolts to 115 Nm.\n"
         "Note: replace the self-locking bolts."),
        ("Remove/install front brake disc", "AR42.10-P-{n:04d}B",
         "Remove the brake pads first.\nUnbolt the brake caliper carrier and "
         "suspend it from the body; do not let it hang on the brake hose.\n"
         "Tighten the caliper carrier bolts to 115 Nm.\nWheel bolts: 130 Nm."),
        ("Tightening torques for front axle", "AR33.25-P-{n:04d}A",
         "Wheel bolt to wheel hub: 130 Nm\nTrack rod end to steering knuckle: "
         "45 Nm\nLower control arm to subframe: 100 Nm + 90 degrees\n"
         "Shock absorber to wheel carrier: 90 Nm\nStabiliser link to strut: "
         "45 Nm"),
        ("Check front axle geometry", "AR40.10-P-{n:04d}A",
         "Set the vehicle to the specified ride height.\nToe-in per wheel: "
         "10' +/- 5'\nCamber: -0 deg 40' +/- 20'\nCaster: 9 deg 20' +/- 30'"),
    ]),
    ("Engine", [
        ("Engine oil and filter change", "AR18.00-P-{n:04d}A",
         "Run the engine to operating temperature.\nDrain the engine oil.\n"
         "Oil change quantity with filter: 8.5 litres\nOil drain plug: 30 Nm\n"
         "Oil filter housing cap: 25 Nm\nSpecification: MB 229.51"),
        ("Remove/install spark plugs", "AR15.10-P-{n:04d}A",
         "Remove the ignition coils.\nBlow out the spark plug shafts with "
         "compressed air.\nTighten new spark plugs to 25 Nm.\nIgnition coil "
         "bolts: 8 Nm."),
        ("Remove/install timing chain", "AR05.10-P-{n:04d}A",
         "Set the engine to ignition TDC of cylinder 1.\nLock the camshafts "
         "with the locking tool.\nChain tensioner: 80 Nm\nCamshaft sprocket "
         "bolt: 40 Nm + 90 degrees\nNote: the chain must not be reused."),
        ("Check compression pressure", "AR01.00-P-{n:04d}A",
         "Nominal compression pressure: 11 to 15 bar\nMinimum: 9 bar\n"
         "Permissible difference between cylinders: 3 bar"),
        ("Coolant change", "AR20.00-P-{n:04d}A",
         "Coolant capacity: 9.5 litres\nAntifreeze concentration: 50 percent\n"
         "Radiator drain plug: 3 Nm\nBleed the cooling system with the "
         "vacuum filler."),
    ]),
    ("Transmission", [
        ("Automatic transmission oil change", "AR27.00-P-{n:04d}A",
         "Oil change quantity: 7.4 litres\nOil pan bolts: 8 Nm\nOverflow "
         "plug: 20 Nm\nSet the transmission oil temperature to 45 deg C "
         "before checking the level."),
        ("Remove/install propeller shaft", "AR41.10-P-{n:04d}A",
         "Mark the position of the flange relative to the shaft.\n"
         "Flange bolts: 40 Nm + 90 degrees\nCentre bearing to floor: 25 Nm"),
    ]),
    ("Electrical", [
        ("Connector end view, engine control module", "GF54.21-P-{n:04d}A",
         "Pin 1: circuit 30 battery positive\nPin 2: circuit 31 ground\n"
         "Pin 14: CAN high\nPin 15: CAN low"),
        ("Wiring diagram, engine compartment", "PE54.15-P-{n:04d}A",
         "Terminal 30 supply from the fuse and relay box.\nCircuit 15 "
         "switched positive via the ignition lock."),
        ("Remove/install battery", "AR54.10-P-{n:04d}A",
         "Switch off the ignition and wait 5 minutes.\nDisconnect the "
         "negative terminal first.\nBattery clamp bolt: 12 Nm\n"
         "Note: register the new battery with the diagnostic tester."),
        ("Component location, front SAM control unit", "GF54.21-P-{n:04d}B",
         "The front SAM control unit is behind the left front wheel arch "
         "liner, above the battery."),
    ]),
    ("Body", [
        ("Remove/install front bumper", "AR88.10-P-{n:04d}A",
         "Remove the wheel arch liner screws.\nRelease the retaining clips "
         "from the wing.\nBumper carrier bolts: 10 Nm"),
        ("Adjust the bonnet", "AR88.20-P-{n:04d}A",
         "Gap to wing: 4.0 mm +/- 1.0 mm\nHinge bolts: 20 Nm"),
    ]),
    ("Maintenance", [
        ("Maintenance acceptance protocol", "AP-KD-MP-{n:04d}",
         "Check brake fluid level and condition.\nCheck tyre pressures and "
         "tread depth.\nCheck the condition of the wiper blades.\n"
         "Reset the service indicator."),
        ("Service sheet, Service A", "AP00.00-P-{n:04d}A",
         "Engine oil and filter change.\nVisual inspection for leaks.\n"
         "Tyre pressure correction.\nService indicator reset."),
    ]),
]

SERVICE_INFO = [
    ("Service Information: revised brake pad retaining spring",
     "With this Service Information a revised retaining spring is "
     "introduced. The previous part may be replaced on repair."),
    ("Overview of innovations, model year update",
     "With this Service Information the innovations of the current model "
     "year are described."),
    ("Service Information: engine oil specification update",
     "Engine oils to specification MB 229.52 may now be used in place of "
     "MB 229.51 on the engines listed."),
]


# Real SIDS bodies carry both kinds of break: a single newline is a new layout
# line (the renderer joins those with spaces, which is right for a sentence the
# manual wrapped) and a blank line is a paragraph break, emitted by parse_sids'
# ('break','') marker. Procedures here get paragraph breaks between steps; spec
# lists stay as consecutive layout lines, which is what makes them run together
# in the source panel -- see the note in CLAUDE.md.
RUN_ON = ("torque", "geometry", "compression", "end view", "location",
          "wiring diagram")


def shape(title, body):
    if any(w in title.lower() for w in RUN_ON):
        return body
    return body.replace("\n", "\n\n")


def applic(models, engine=False):
    if not models:
        return []
    codes = ", ".join(models)
    if engine:
        return [f"651.### as of 359084 in MODELS {codes}"]
    return [f"MODEL {codes}"]


def model_fields(series, specific=False, all_series=True):
    """The schema 1.1 structured applicability block for a record.

    Derived from the catalogue exactly as parse.py derives it, so a
    whole-series document inherits the series' production span.
    """
    series = [s for s in series if s in MODEL_DATA]
    if not series:
        return {"model_series": [], "baumuster": [], "model_year_from": None,
                "model_year_to": None, "applies_all_series": False}
    # 239 of the 406 real series carry no production dates at all, so both
    # ends can be entirely absent
    froms = [MODEL_DATA[s][0] for s in series if MODEL_DATA[s][0]]
    tos = [MODEL_DATA[s][1] for s in series]
    bau = []
    if specific:
        for s in series:
            bau.extend(MODEL_DATA[s][2][:1])
    return {
        "model_series": sorted(series),
        "baumuster": sorted(bau),
        "model_year_from": min(froms) if froms else None,
        # any open-ended member leaves the window open
        "model_year_to": None if any(t is None for t in tos) else max(tos),
        "applies_all_series": all_series and not specific,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--catalogue", default=None,
                    help="a real models.jsonl to take series/years/Baumuster "
                         "from (default: the preserved applicability pull)")
    ap.add_argument("--per-series", type=int, default=3,
                    help="documents to generate per model series")
    args = ap.parse_args()

    global MODEL_DATA, MODELS
    src = Path(args.catalogue) if args.catalogue else REAL_CATALOGUE
    if src.is_file():
        MODEL_DATA = load_catalogue(src)
        print(f"catalogue: {src}  ({len(MODEL_DATA)} real model series)")
    else:
        MODEL_DATA = FALLBACK_MODEL_DATA
        print(f"catalogue: invented fallback ({len(MODEL_DATA)} series) -- "
              f"no real models.jsonl at {src}")
    MODELS = sorted(MODEL_DATA)

    OUT.mkdir(parents=True, exist_ok=True)
    records = []
    dok = 1000000
    docnrs = []

    # Every series gets documents of its own, so the picker is not full of
    # cars with an empty manual. A series only appears in AutoRundown if some
    # document names it.
    flat = [(system, t, d, b) for system, docs in SYSTEMS for t, d, b in docs]
    for si, series in enumerate(MODELS):
        for k in range(args.per_series):
            system, title, docnr_tpl, body = flat[(si * 7 + k * 3) % len(flat)]
            dok += 1
            docnr = docnr_tpl.format(n=dok % 10000)
            docnrs.append(docnr)
            # most documents name one series; some span several, which is
            # what produces the page fan-out
            models = [series]
            if k == 0 and len(MODELS) > 3:
                models += random.sample(MODELS, random.choice([0, 0, 1, 2]))
            models = sorted(set(models))
            records.append({
                "dok_id": dok, "dok_nr_id": 2000000 + dok, "dok_type": 1,
                "lang": "en", "title": title,
                "fzg_art_bits": "00100000000000000000000000000000",
                "fzg_art_indices": [2],
                "kurzgueltigkeit_id": 98000 + (dok % 900),
                "cluster": random.choice([3, 4, 7, 8]),
                "body_format": "sids",
                # half of them never name the model in the body -- the case
                # that used to make whole series look missing
                "applicability_notes": applic(models) if k % 2 else [],
                **model_fields(models, specific=(k == 2)),
                "cross_refs": random.sample(docnrs, min(len(docnrs),
                                                        random.choice([0, 1, 2]))),
                "image_refs": [],
                "figures": ([{"graphik_id": dok * 10, "graphik_nr":
                              f"{docnr}-01", "klasse": 3, "cluster": 8}]
                            if random.random() < 0.4 else []),
                "body_text": f"{shape(title, body)}\n\nSystem: {system}.",
                "sids_header": 'ASCII-SIDS2.00,"18.06.2016","HP 9000/700"',
            })

    for system, docs in SYSTEMS:
        for title, docnr_tpl, body in docs:
            # a few procedures filed per applicability slice, as WIS does
            for group in range(2):
                dok += 1
                n = dok % 10000
                docnr = docnr_tpl.format(n=n)
                docnrs.append(docnr)
                models = random.sample(MODELS, random.choice([1, 1, 2, 3]))
                records.append({
                    "dok_id": dok, "dok_nr_id": 2000000 + dok, "dok_type": 1,
                    "lang": "en", "title": f"{title}",
                    "fzg_art_bits": "00100000000000000000000000000000",
                    "fzg_art_indices": [2],
                    "kurzgueltigkeit_id": 98000 + (dok % 900),
                    "cluster": random.choice([3, 4, 7, 8]),
                    "body_format": "sids",
                    "applicability_notes": applic(
                        models, engine=(system == "Engine" and group % 2 == 0)),
                    **model_fields(models, specific=(group == 3)),
                    "cross_refs": random.sample(docnrs, min(len(docnrs),
                                                            random.choice([0, 1, 2, 3]))),
                    "image_refs": [],
                    "figures": ([{"graphik_id": dok * 10, "graphik_nr":
                                  f"{docnr}-01", "klasse": 3, "cluster": 8}]
                                if random.random() < 0.4 else []),
                    "body_text": f"{shape(title, body)}\n\nSystem: {system}.",
                    "sids_header": 'ASCII-SIDS2.00,"18.06.2016","HP 9000/700"',
                })

    # Documents whose body never names the model. Under schema 1.0 these all
    # looked "general"; under 1.1 nearly all of them are tied to a series by
    # W_Dok2Reihe, which is why the manifest reports records_with_models at
    # ~98%. The W201 slice is the pre-1990 case the spec calls out: real
    # documents, invisible to prose filtering.
    for system, docs in SYSTEMS:
        for title, docnr_tpl, body in docs:
            for group in range(2):
                dok += 1
                # one in twelve is genuinely tied to nothing
                tied = [] if dok % 12 == 0 else (
                    ["201"] if group == 0 else random.sample(MODELS, 1))
                records.append({
                    "dok_id": dok, "dok_nr_id": 2000000 + dok, "dok_type": 1,
                    "lang": "en",
                    "title": f"{title}" + (" (no model in body)" if tied
                                           else " (general)"),
                    "fzg_art_bits": "00100000000000000000000000000000",
                    "fzg_art_indices": [2],
                    "kurzgueltigkeit_id": 98000 + (dok % 900),
                    "cluster": 8, "body_format": "sids",
                    "applicability_notes": [],
                    **model_fields(tied),
                    "cross_refs": random.sample(docnrs, min(len(docnrs), 2)),
                    "image_refs": [], "figures": [],
                    "body_text": f"{shape(title, body)}\n\nSystem: {system}.",
                    "sids_header": 'ASCII-SIDS2.00,"18.06.2016","HP 9000/700"',
                })

    # engine documents, named by an ENGINE line and no MODEL
    for fam, title in SAMPLE_ENGINE_DOCS:
        dok += 1
        records.append({
            "dok_id": dok, "dok_nr_id": 2000000 + dok, "dok_type": 1,
            "lang": "en", "title": title,
            "fzg_art_bits": "00100000000000000000000000000000",
            "fzg_art_indices": [2], "kurzgueltigkeit_id": 98393,
            "cluster": 8, "body_format": "sids",
            "applicability_notes": [f"ENGINE {fam}"], **model_fields([]),
            "cross_refs": [], "image_refs": [], "figures": [],
            "body_text": "Remove the cover bolts in the reverse of the "
                         "tightening sequence.\n\nCover bolts: 9 Nm",
            "sids_header": 'ASCII-SIDS2.00,"18.06.2016","HP 9000/700"',
        })

    # service information -> tsb
    for title, body in SERVICE_INFO:
        for group in range(3):
            dok += 1
            records.append({
                "dok_id": dok, "dok_nr_id": 2000000 + dok, "dok_type": 2,
                "lang": "en", "title": title,
                "fzg_art_bits": "00100000000000000000000000000000",
                "fzg_art_indices": [2], "kurzgueltigkeit_id": 98393,
                "cluster": 8, "body_format": "sids",
                "applicability_notes": applic(si_models := random.sample(MODELS, 2)),
                **model_fields(si_models),
                "cross_refs": random.sample(docnrs, 1),
                "image_refs": [], "figures": [],
                "body_text": body,
                "sids_header": 'ASCII-SIDS2.00,"18.06.2016","HP 9000/700"',
            })

    # xml checklists
    for i in range(6):
        dok += 1
        records.append({
            "dok_id": dok, "dok_nr_id": 2000000 + dok, "dok_type": 5,
            "lang": "en", "title": "Maintenance acceptance protocol, checklist",
            "fzg_art_bits": "00100000000000000000000000000000",
            "fzg_art_indices": [2], "kurzgueltigkeit_id": 98393,
            "cluster": 8, "body_format": "xml",
            "applicability_notes": applic(xml_models := [random.choice(MODELS)]),
            **model_fields(xml_models, specific=True),
            "cross_refs": [], "image_refs": [f"bild-{i}"], "figures": [],
            "body_text": "Check brake fluid.\nCheck tyre pressures.\n"
                         "Check lighting.\nRoad test.",
            "sids_header": None,
        })

    # figure-only stubs: 15% of the corpus, empty body (rtf + none)
    n_stub = int(len(records) * 0.18)
    for i in range(n_stub):
        dok += 1
        records.append({
            "dok_id": dok, "dok_nr_id": 2000000 + dok, "dok_type": 3,
            "lang": "en", "title": f"Assembly drawing {i}",
            "fzg_art_bits": "00100000000000000000000000000000",
            "fzg_art_indices": [2], "kurzgueltigkeit_id": 98393,
            "cluster": 8,
            "body_format": "rtf" if i % 10 else "none",
            "applicability_notes": [], **model_fields([]), "cross_refs": [],
            "image_refs": [f"5510683{i:03d}|800|600|DXF"] if i % 10 else [],
            "figures": [], "body_text": "", "sids_header": None,
        })

    with (OUT / "records.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    fmt = {}
    for r in records:
        fmt[r["body_format"]] = fmt.get(r["body_format"], 0) + 1
    manifest = {
        "schema_version": "1.0",
        "source_type": "mercedes-wis-virtualbox",
        "source_id": "192.168.56.101",
        "database": "wisnet",
        "language": {"sprach_code": 2, "iso": "en"},
        "record_count": len(records),
        "empty_body_count": sum(1 for r in records if not r["body_text"]),
        "error_count": 0,
        "body_format_counts": fmt,
        "records_file": "records.jsonl",
        "records_bytes": (OUT / "records.jsonl").stat().st_size,
        "extracted_at": "2026-09-10T16:28:50Z",
    }
    with (OUT / "models.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for series, (year_from, year_to, baumuster) in sorted(
                MODEL_DATA.items()):   # real catalogue when one was found
            f.write(json.dumps({
                "reihe": series,
                "year_from": year_from,
                "year_to": year_to,
                "baumuster": baumuster,
                "bm_zeilen": len(baumuster) * 4,
            }, ensure_ascii=False) + "\n")
        # the real catalogue lists engine series too; an ENGINE line is only
        # trusted for a series the catalogue knows
        for fam in sorted({fam for fam, _ in SAMPLE_ENGINE_DOCS} - set(MODEL_DATA)):
            f.write(json.dumps({"reihe": fam, "year_from": None, "year_to": None,
                                "baumuster": [], "bm_zeilen": 0}) + "\n")

    with_models = sum(1 for r in records if r["model_series"])
    manifest["schema_version"] = "1.1"
    manifest["applicability"] = {
        "model_lines": sum(len(v[2]) * 4 for v in MODEL_DATA.values()),
        "linked_docs": sum(len(r["model_series"]) for r in records),
        "model_series_count": len(MODEL_DATA),
        "records_with_models": with_models,
        "models_file": "models.jsonl",
    }

    variants = [v for v in SAMPLE_VARIANTS if v[0] in MODEL_DATA]
    with (OUT / "variants.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for series, name, ebm, bodies in variants:
            f.write(json.dumps({
                "model_series": series, "name": name, "baumuster": sorted(ebm),
                "wmi": ["WDB"], "engines": sorted({e for es in ebm.values() for e in es}),
                "engine_by_bm": ebm, "body_by_bm": bodies,
                "division": "car", "vehicle_type": "CAR"}) + "\n")
    (OUT / "vin_us.json").write_text(json.dumps(SAMPLE_VIN_US), encoding="utf-8")
    (OUT / "vin2fin.json").write_text(json.dumps(SAMPLE_VIN2FIN), encoding="utf-8")
    manifest.update({"variants_file": "variants.jsonl", "vin_us_file": "vin_us.json",
                     "vin2fin_file": "vin2fin.json"})

    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                       encoding="utf-8")
    print(f"{len(records)} records ({with_models} with model data) + "
          f"{len(MODEL_DATA)} series -> {OUT}")
    print(f"  formats: {fmt}")
    print(f"  empty body: {manifest['empty_body_count']}")
    print(f"  {len(variants)} named models, {len(SAMPLE_VIN2FIN)} US VIN prefixes")


if __name__ == "__main__":
    main()
