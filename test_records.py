"""Regression check for the `records` importer -- free, no API calls.

    python test_records.py

Builds a synthetic corpus from the record shapes in the extractor's
IMPORT_SPEC.md (sections 3 and 3a), then drives the whole path: detect -> discover -> pages ->
ingest -> search, in both grouping modes. Unlike eval.py these cases are not
tied to a particular archive, so they keep working when the real corpus is
re-pulled.
"""
import collections
import contextlib
import csv
import io
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from autorundown import importers, index, search  # noqa: E402
from autorundown.importers.records import (  # noqa: E402
    applicability, class_name, fix_mojibake, model_families, record_series,
    curated_names, curated_years, sample_names, series_division, series_make,
    structured_agreement, engine_families,
    types_key, vehicle_types_of, year_range)
import ingest  # noqa: E402

FAILURES = []


def check(label, got, want):
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {label}: {got!r}" +
          ("" if ok else f"  (expected {want!r})"))
    if not ok:
        FAILURES.append(label)


def rec(dok_id, title, body, fmt="sids", applic=(), xrefs=(), imgrefs=(), figs=(),
        series=(), baumuster=(), yr=(None, None), all_series=False, fzg=(2,)):
    return {
        "model_series": list(series), "baumuster": list(baumuster),
        "model_year_from": yr[0], "model_year_to": yr[1],
        "applies_all_series": all_series,
        "dok_id": dok_id, "dok_nr_id": 1000000 + dok_id, "dok_type": 1,
        "lang": "en", "title": title,
        "fzg_art_bits": "".join("1" if i in fzg else "0" for i in range(32)),
        "fzg_art_indices": sorted(fzg), "kurzgueltigkeit_id": 98393, "cluster": 8,
        "body_format": fmt, "applicability_notes": list(applic),
        "cross_refs": list(xrefs), "image_refs": list(imgrefs),
        "figures": [{"graphik_id": g, "graphik_nr": f"N{g}", "klasse": 3,
                     "cluster": 8} for g in figs],
        "body_text": body,
        "sids_header": 'ASCII-SIDS2.00,"18.06.2016","HP 9000/700"' if fmt == "sids" else None,
    }


RECORDS = [
    rec(1002295,
        "Overview of innovations for Sprinter, December 2002",
        "With this Service Information the innovations are described.",
        applic=["MODEL 901.6, 902.6, 903.6, 904.6, 905.6"],
        series=["901", "902", "903"], yr=("1995-02-01", "2006-05-31"),
        all_series=True, xrefs=["SI00.00-D-0026A"], figs=[1]),
    rec(1002296,
        "Remove/install front brake pads",
        "Unscrew the brake caliper. Tighten the caliper bolts to 30 Nm.",
        applic=["MODEL 176"], series=["176"], baumuster=["176.042"],
        yr=("2012-06-01", "2018-05-31"), xrefs=["AR42.10-P-0100A"]),
    rec(1002297,
        "Tightening torques for front axle",
        "Wheel bolt to hub: 130 Nm. Track rod end nut: 45 Nm.",
        applic=["MODEL 176", "MODEL 246"], series=["176", "246"],
        yr=("2011-11-01", "2018-12-31"), all_series=True,
        # names two series, so its bits are the union of theirs: must not
        # give 176 type 5 when 176's own document says type 2
        fzg=(2, 5)),
    rec(1002298,
        "Engine oil capacity",
        "Oil change with filter: 5.5 litres.",
        applic=["611.### as of 359084 in MODELS 202, 203, 210"],
        series=["202", "203", "210"], yr=("1993-01-01", "2002-12-31")),
    # The pre-1990 case IMPORT_SPEC 3 warns about: a real W201 document whose
    # body never names the model. Prose filtering files this as "general" and
    # the 190E looks like it has no manual at all.
    rec(1002306,
        "Remove/install cylinder head, 190E 2.3-16",
        "Slacken the cylinder head bolts in the reverse of the tightening "
        "sequence. Tighten to 55 Nm plus 90 degrees twice.",
        series=["201"], baumuster=["201.034"],
        yr=("1983-10-01", "2000-04-30")),
    # General documents that WIS flags for one vehicle type only. The model
    # documents above are all type 2; this one is type 5 (bus/truck
    # territory on the real corpus) and must not reach a type-2 model.
    rec(1002307,
        "Test and adjustment values: Buses: engine oil capacity",
        "Engine oil capacity with filter: 28 litres. Drain plug 60 Nm.",
        fzg=(5,)),
    # flagged for every type -- universal, stays in every scope
    rec(1002308,
        "Service Information: engine oil capacity reference",
        "Engine oil capacity is listed per engine in the capacities table.",
        fzg=(0, 2, 5)),
    # WIS files engine documents under the ENGINE's series: this one is M102
    # only, never the W201 -- reachable from a 190E only via its variant's
    # engine. Flagged type 5, unlike the W201's type 2, to prove engine
    # buckets are not type-narrowed.
    rec(1002309,
        "Check cylinder head sealing surface and cylinder head gasket",
        "Check the cylinder head sealing surface for flatness. Renew the gasket.",
        series=["102"], baumuster=["102.983"], yr=("1982-01-01", "1993-12-31"),
        fzg=(5,)),
    # genuinely tied to nothing
    rec(1002299,
        "Bleed the brake system",
        "Connect the bleeder unit and open the bleed screw at each caliper.",
        xrefs=["AR42.10-P-0010A"]),
    rec(1002305,
        "Renew the brake fluid",
        "Brake fluid must be renewed every two years regardless of mileage.",
        xrefs=["AR42.10-P-0011A"]),
    # figure-only stubs: empty body, graphic still in the source
    rec(1002300, "Front axle assembly drawing", "", fmt="rtf",
        imgrefs=["551068321|800|600|DXF"]),
    rec(1002301, "Wiring diagram, engine compartment", "", fmt="rtf",
        imgrefs=["551068322|800|600|TIFF"]),
    rec(1002302, "Doc row with no stored body", "", fmt="none"),
    # xml checklist
    rec(1002303,
        "Maintenance acceptance protocol",
        "Check brake fluid. Check tyre pressures.",
        fmt="xml", applic=["MODEL 204"], series=["204"],
        yr=("2007-01-01", "2014-12-31"), xrefs=["AP-KD-MP-0001"],
        imgrefs=["bild-1"],
        # 204's only document is flagged for every type: that says nothing
        # about 204, so 204 must end up with no narrowing at all
        fzg=(0, 1, 2, 3, 5, 7, 8)),
    rec(1002304,
        "Connector end view, engine control module",
        "Pin 1: battery positive. Pin 2: ground.",
        applic=["MODEL 246"], series=["246"],
        yr=("2011-11-01", "2018-12-31")),
]

MODELS_CATALOGUE = [
    {"reihe": "176", "year_from": "2012-06-01", "year_to": "2018-05-31",
     "baumuster": ["176.042", "176.052"], "bm_zeilen": 8},
    {"reihe": "201", "year_from": "1983-10-01", "year_to": "2000-04-30",
     "baumuster": ["201.018", "201.024", "201.034"], "bm_zeilen": 80},
    {"reihe": "246", "year_from": "2011-11-01", "year_to": "2018-12-31",
     "baumuster": ["246.242"], "bm_zeilen": 4},
    {"reihe": "204", "year_from": "2007-01-01", "year_to": "2014-12-31",
     "baumuster": ["204.041"], "bm_zeilen": 4},
    # still in production -> open-ended window
    {"reihe": "213", "year_from": "2016-04-01", "year_to": None,
     "baumuster": ["213.004"], "bm_zeilen": 4},
]

MANIFEST = {
    "schema_version": "1.1",
    "source_type": "mercedes-wis-virtualbox",
    "source_id": "192.168.56.101",
    "database": "wisnet",
    "language": {"sprach_code": 2, "iso": "en"},
    "record_count": len(RECORDS),
    "empty_body_count": 1,
    "error_count": 0,
    "body_format_counts": {"sids": 8, "rtf": 2, "xml": 1},
    "records_file": "records.jsonl",
    "applicability": {
        "model_lines": 100,
        "linked_docs": 12,
        "model_series_count": len(MODELS_CATALOGUE),
        "records_with_models": 8,
        "models_file": "models.jsonl",
    },
    "records_bytes": 0,
    "extracted_at": "2026-09-10T16:28:50Z",
}


# Real names from ASRA_FahrzeugII; the US codes are real rows of USTypBM2TypBM.
VARIANTS = [
    {"model_series": "176", "name": "A 180", "baumuster": ["176.042"]},
    # an engine number that is also a VEHICLE series (201 is the W201) -- the
    # real corpus has these (OM906 vs the Sprinter 906): must not borrow it
    {"model_series": "176", "name": "A 200 CDI", "baumuster": ["176.008"],
     "engines": ["201.920"]},
    {"model_series": "201", "name": "190 E 2.3 16", "baumuster": ["201.034"],
     "engines": ["102.983"]},
    # M103 has no documents here: its variant reaches no engine bucket
    {"model_series": "201", "name": "190 E 2.6", "baumuster": ["201.029"],
     "engines": ["103.942"]},
    # commercial division (ASRA SparteAbk 10): left out by the passenger default
    {"model_series": "901", "name": "208 CDI", "baumuster": ["901.611"],
     "division": "commercial", "vehicle_type": "Light transporter"},
    {"model_series": "176", "name": "A 160", "baumuster": ["176.041"], "division": "car"},
    # a series with names but no documents must not invent a vehicle
    {"model_series": "140", "name": "500 SE, S 500", "baumuster": ["140.050"]},
]
VIN_US = {"WDB": {"D": "201", "G": "140"}, "WDD": {"A": "199"}}


def build_corpus(root: Path, with_vehicles: bool = True) -> Path:
    cdir = root / "wis_en"
    cdir.mkdir(parents=True)
    with (cdir / "records.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for r in RECORDS:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    manifest = dict(MANIFEST)
    if with_vehicles:
        with (cdir / "models.jsonl").open("w", encoding="utf-8", newline="\n") as f:
            for v in MODELS_CATALOGUE:
                f.write(json.dumps(v, ensure_ascii=False) + "\n")
        # IMPORT_SPEC 3b sidecars, as parse_vehicles.py writes them
        with (cdir / "variants.jsonl").open("w", encoding="utf-8", newline="\n") as f:
            for v in VARIANTS:
                f.write(json.dumps(v, ensure_ascii=False) + "\n")
        (cdir / "vin_us.json").write_text(json.dumps(VIN_US), encoding="utf-8")
        manifest["variants_file"] = "variants.jsonl"
        manifest["vin_us_file"] = "vin_us.json"
    else:
        manifest.pop("applicability", None)
    (cdir / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                        encoding="utf-8")
    return cdir


def main():
    tmp = Path(tempfile.mkdtemp(prefix="ar_records_"))
    try:
        build_corpus(tmp)

        print("\n-- helpers --")
        check("MODEL 901.6, 902.6",
              model_families(["MODEL 901.6, 902.6, 903.6, 904.6, 905.6"]),
              ["901", "902", "903", "904", "905"])
        check("engine code is not a model",
              model_families(["611.### as of 359084 in MODELS 202, 203, 210"]),
              ["202", "203", "210"])
        check("no model stated", model_families([]), [])
        check("engine lines parse like model lines",
              engine_families(["ENGINE 102.983, 111.920/940"]), ["102", "111"])
        check("a doc naming model and engine is filed under the model only",
              record_series({"applicability_notes": ["ENGINE 102.983 in MODEL 201"]}),
              ["201"])
        check("table names only an engine, text names cars: the cars win",
              record_series({"model_series": [], "engine_series": ["102"],
                             "applicability_notes": ["MODEL 460, 461, 463"]}),
              ["460", "461", "463"])
        # Real notes from the English corpus. Slash suffixes after a dotted
        # Baumuster are variants of that series -- reading them as series
        # filed Sprinter 906 documents under 211/213, the E-Class.
        check("slash suffixes are variants, not series",
              model_families(["MODEL 906.111 /113 /131 /133 /211 /213 /235"]),
              ["906"])
        check("Vito chain stays one series",
              model_families(["MODEL 639.601/603/605/701/703/705/711/713/811/813/815"]),
              ["639"])
        check("engine prefix and variant chain",
              model_families(["651 in MODEL 447.603/605/703/705/813/815"]),
              ["447"])
        check("several dotted items, comma separated",
              model_families(["MODEL 950.14 /16, 952.14 /16, 953.14 /16"]),
              ["950", "952", "953"])
        check("bare slash pair names two series",
              model_families(["(MODELS 202/210 also N69/3 or N69/4)"]),
              ["202", "210"])
        check("short suffix after a bare head is not a series",
              model_families(["MODEL 169 /09"]), ["169"])
        check("long bare chain is a variant list, not series (218 = CLS)",
              model_families(["MODEL 203 /018 /065 /076 /218 /718, 208"]),
              ["203", "208"])
        check("prose code must exist in the catalogue when one is given",
              record_series({"model_series": [],
                             "applicability_notes": ["MODEL 346 332 04 16"]},
                            catalogue={"176", "201"}), [])
        check("prose code in the catalogue is kept",
              record_series({"model_series": [],
                             "applicability_notes": ["MODEL 176"]},
                            catalogue={"176", "201"}), ["176"])
        check("except clause is not applicability",
              model_families(["MODEL 202 / / /, 208.3, 210 (except 202.07 /08 /18)"]),
              ["202", "208", "210"])
        check("year range",
              year_range({"year_from": "2007-01-01", "year_to": "2014-12-31"}),
              "2007-2014")
        check("still in production",
              year_range({"year_from": "2016-04-01", "year_to": None}), "2016+")
        check("one year",
              year_range({"year_from": "2011-01-01", "year_to": "2011-12-31"}),
              "2011")
        check("no dates", year_range({}), "")
        years = curated_years("mercedes-wis-virtualbox")
        check("curated table loads", len(years) > 70, True)
        check("W126 starts in 1979, not the database's 1983", years["126"], "1979-1991")
        check("a model still in production is open-ended", years["447"], "2014+")
        check("uncertain series are left out rather than guessed", "636" in years, False)
        cn = curated_names("mercedes-wis-virtualbox")
        check("curated names load with the years", cn["126"], "W126 S-Class")
        check("a name shared by several series gets its number",
              [cn[s] for s in ("667", "670")], ["Vario (667)", "Vario (670)"])
        check("no two series end up with the same label",
              len(cn) == len(set(cn.values())), True)
        check("other sources have no curated table", curated_years("some-other-source"), {})
        with open(Path(__file__).resolve().parent / "autorundown" / "importers" / "data"
                  / "mercedes_years.csv", encoding="utf-8", newline="") as f:
            rows_ = list(csv.DictReader(f))
        bad_rows = [r["series"] for r in rows_ if not (
            len(r["series"]) == 3 and 1900 <= int(r["year_from"]) <= 2035 and
            (not r["year_to"] or int(r["year_from"]) <= int(r["year_to"]) <= 2035))]
        check("every curated row is well-formed", bad_rows, [])
        check("no series listed twice",
              len(rows_) == len({r["series"] for r in rows_}), True)
        check("types_key is delimited so 1 never matches 11",
              types_key([11, 1, 1]), ",1,11,")
        check("no bits, no key", types_key([]), "")
        check("mis-tag under 1% is ignored",
              vehicle_types_of(collections.Counter({0: 1000, 1: 40, 9: 3}), 1000),
              ",0,1,")
        # the real catalogue stamps series 124 as 1194-03-01 (a transposed
        # 1984); an unchecked value like that floods the year picker
        check("implausible start is treated as unknown",
              year_range({"year_from": "1194-03-01", "year_to": "1997-06-30"}),
              "")
        check("implausible end is treated as unknown",
              year_range({"year_from": "1984-03-01", "year_to": "9997-06-30"}),
              "1984+")

        print("\n-- structured model applicability (schema 1.1) --")
        check("structured field wins over prose",
              record_series({"model_series": ["201"],
                             "applicability_notes": ["MODEL 176"]}), ["201"])
        # an empty join is absence of evidence: the prose still counts, or a
        # doc that says "MODEL 176" lands in the bucket every car searches
        check("empty structured field falls back to prose",
              record_series({"model_series": [],
                             "applicability_notes": ["MODEL 176"]}), ["176"])
        check("no structured field and no prose is untied",
              record_series({"model_series": [],
                             "applicability_notes": []}), [])
        check("1.0 doc still falls back to prose",
              record_series({"applicability_notes": ["MODEL 176"]}), ["176"])
        # no "built <years>": those dates were scored unreliable (see CLAUDE.md)
        check("baumuster reaches section_path, build years do not",
              applicability({"model_series": ["201"], "baumuster": ["201.034"],
                             "model_year_from": "1983-10-01",
                             "model_year_to": "2000-04-30"}),
              ["MODEL 201", "201.034"])
        check("whole-series docs say so",
              applicability({"model_series": ["201"], "baumuster": [],
                             "model_year_from": None, "model_year_to": None,
                             "applies_all_series": True}),
              ["MODEL 201", "all variants"])
        check("nothing structured falls back to General",
              applicability({"model_series": [], "baumuster": [],
                             "applicability_notes": []}), ["General"])
        check("class from designations",
              class_name(["C 180", "C 220 CDI", "C 63 AMG"]), "C-Class")
        check("multi-letter class", class_name(["Sprinter 208", "Sprinter 211"]),
              "Sprinter")
        check("heuristic declines when they disagree",
              class_name(["Vito 111 CDI", "V 250 d"]), "")
        check("sample names show the spread, first/middle/last",
              sample_names([{"name": n} for n in
                            ("260 SE", "280 S", "300 SE", "380 SEL", "560 SEL")]),
              "260 SE, 300 SE, 560 SEL …")
        # UTF-8 read as Windows-1252, exactly as it arrives from the WIS driver
        check("mojibake: Coupé", fix_mojibake("E 200 CoupÃ©"), "E 200 Coupé")
        check("mojibake: Ü via cp1252's œ", fix_mojibake("O 530 Ãœ"), "O 530 Ü")
        check("mojibake: diameter sign in a spec",
              fix_mojibake("Bolt Ã˜ 10 mm"), "Bolt Ø 10 mm")
        check("correct text is never touched",
              fix_mojibake("Größe 20 °C Coupé Ø 10"),
              "Größe 20 °C Coupé Ø 10")
        check("ascii passes through", fix_mojibake("C 220 CDI"), "C 220 CDI")
        # the Latin-1 form: en dash as "â" + raw C1 controls, from a WIS torque table
        check("mojibake: Latin-1 en dash in a spec",
              fix_mojibake("Nm â\u0080\u0093 30"), "Nm – 30")
        check("mojibake: cp1252 en dash still repaired",
              fix_mojibake("Nm â€“ 30"), "Nm – 30")
        check("lone accented letter before a C1 control is left alone",
              fix_mojibake("café\u0080"), "café\u0080")
        check("make from Mercedes' vehicle type",
              series_make([{"vehicle_type": "TRUCK"}, {"vehicle_type": "TRUCK"},
                           {"vehicle_type": "BUS"}], "Mercedes-Benz"),
              "Mercedes-Benz Trucks")
        check("Vito is a van even though its division is car",
              series_make([{"vehicle_type": "Light transporter", "division": "car"}],
                          "Mercedes-Benz"), "Mercedes-Benz Vans")
        check("unknown type falls back to the division",
              series_make([{"vehicle_type": "", "division": "commercial"}], "Mercedes-Benz"),
              "Mercedes-Benz Commercial")
        check("division decides passenger vs commercial",
              series_division([{"division": "car", "vehicle_type": "Light transporter"}]), "car")
        check("with no division, a bus type makes it commercial",
              series_division([{"division": "", "vehicle_type": "BUS"}]), "commercial")
        check("nothing known: undecided (kept)", series_division([{"name": "x"}]), "")
        check("nothing known keeps the source make",
              series_make([{"name": "x"}], "Mercedes-Benz"), "Mercedes-Benz")
        check("few names are listed whole",
              sample_names([{"name": "300 SL"}, {"name": "500 SL"}]), "300 SL, 500 SL")

        print("\n-- registry / detection --")
        check("registered", "records" in importers.available(), True)
        check("autodetect picks records",
              importers.autodetect(tmp).name, "records")
        check("lemon does not claim it",
              importers.get("lemon").detect(tmp), False)
        check("default mode is passenger vehicles", importers.get("records").group, "passenger")
        check("all vehicles still selectable", importers.get("records:model").group, "model")
        check("corpus mode still selectable",
              importers.get("records:corpus").group, "corpus")

        print("\n-- discover: corpus mode --")
        imp = importers.get("records:corpus")
        vehicles = list(imp.discover(tmp))
        check("one vehicle", len(vehicles), 1)
        v = vehicles[0]
        check("doc_id", v.doc_id, "mercedes-wis-virtualbox-en")
        check("make", v.make, "Mercedes-Benz")
        check("page_count from manifest", v.page_count, 15)
        check("no companion scope", v.general_doc_id, "")
        pages = list(imp.pages(v))
        check("figure-only stubs dropped", len(pages), 12)
        check("stubs counted", imp.skipped_empty, 3)
        p = next(x for x in pages if x.page_no == "1002296")
        check("title", p.title, "Remove/install front brake pads")
        check("structured applicability leads the text",
              p.text.splitlines()[0],
              "Applies to: MODEL 176; 176.042")
        check("cross-refs trail the text",
              p.text.strip().endswith("Refer to: AR42.10-P-0100A"), True)
        check("section_path carries series and baumuster, no build years",
              p.section_path, ["MODEL 176", "176.042"])
        check("no unresolvable images", p.images, [])
        check("no dead links", p.links, [])
        check("general page section_path",
              next(x for x in pages if x.page_no == "1002299").section_path,
              ["General"])
        check("xml -> spec_table",
              next(x for x in pages if x.page_no == "1002303").doc_type,
              "spec_table")
        check("connector title -> wiring",
              next(x for x in pages if x.page_no == "1002304").doc_type,
              "wiring")
        check("service info -> tsb",
              next(x for x in pages if x.page_no == "1002295").doc_type, "tsb")

        print("\n-- discover: model mode --")
        mimp = importers.get("records:model")
        mvehicles = list(mimp.discover(tmp))
        ids = [x.doc_id for x in mvehicles]
        check("one vehicle per series + general", len(mvehicles), 12)
        check("general bucket is last", ids[-1],
              "mercedes-wis-virtualbox-en-general")
        m176 = next(x for x in mvehicles if x.doc_id.endswith("-176"))
        check("MODEL 176 page count", m176.page_count, 2)
        check("a curated name is the label", m176.model, "W176 A-Class")
        check("variants attached for the picker",
              [v["name"] for v in m176.variants], ["A 180", "A 200 CDI", "A 160"])
        check("variant keeps its Baumuster",
              m176.variants[0]["baumuster"], ["176.042"])
        check("series with names but no documents gets no vehicle",
              [x.doc_id for x in mvehicles if x.doc_id.endswith("-140")], [])
        check("year range from models.jsonl", m176.year_range, "2012-2018")
        check("model_code", m176.model_code, "176")
        check("companion is the general bucket", m176.general_doc_id,
              "mercedes-wis-virtualbox-en-general")
        m639 = next((x for x in mvehicles if x.doc_id.endswith("-639")), None)
        check("no 639 documents in this corpus", m639, None)
        m202 = next(x for x in mvehicles if x.doc_id.endswith("-202"))
        check("the curated name applies without a models.jsonl entry",
              m202.model, "W202 C-Class")
        check("years come from the curated table, not the catalogue",
              m202.year_range, "1993-2001")
        m102 = next(x for x in mvehicles if x.doc_id.endswith("-102"))
        check("a series the curated table lacks gets no year", m102.year_range, "")

        # the regression this schema exists for
        m201 = next(x for x in mvehicles if x.doc_id.endswith("-201"))
        check("W201 appears even though no body names it",
              m201.model, "W201 190")
        check("US VIN code WDB + D resolves to the W201", m201.vin_codes, ",WDB:D,")
        check("a series with no US code carries none", m176.vin_codes, "")
        check("W201 years are its real production years", m201.year_range, "1982-1993")
        check("the 190E cylinder-head doc is filed under W201",
              [x.page_no for x in mimp.pages(m201)], ["1002306"])
        check("prose-only filtering would have missed it",
              model_families(
                  next(r for r in RECORDS if r["dok_id"] == 1002306)
                  ["applicability_notes"]), [])
        gen = next(x for x in mvehicles if x.doc_id.endswith("-general"))
        check("general bucket is nobody's companion", gen.general_doc_id, "")
        check("general bucket has no model code", gen.model_code, "")
        m176_pages = sorted(x.page_no for x in mimp.pages(m176))
        check("MODEL 176 pages", m176_pages, ["1002296", "1002297"])
        check("general bucket pages",
              sorted(x.page_no for x in mimp.pages(gen)),
              ["1002299", "1002305", "1002307", "1002308"])
        check("a doc in two models is in both",
              sorted(x.page_no for x in
                     mimp.pages(next(y for y in mvehicles
                                     if y.doc_id.endswith("-246")))),
              ["1002297", "1002304"])

        print("\n-- vehicles.jsonl is optional --")
        bare = Path(tempfile.mkdtemp(prefix="ar_bare_"))
        try:
            build_corpus(bare, with_vehicles=False)
            bimp = importers.get("records:model")
            b176 = next(x for x in bimp.discover(bare)
                        if x.doc_id.endswith("-176"))
            check("the curated name doesn't need the sidecars", b176.model, "W176 A-Class")
            b102 = next(x for x in bimp.discover(bare) if x.doc_id.endswith("-102"))
            check("an uncurated series with no sidecars falls back to the bare code",
                  b102.model, "MODEL 102")
            check("no variants without the sidecar", b176.variants, [])
            check("curated years don't depend on the sidecars", b176.year_range, "2012-2018")
            check("companion still set", b176.general_doc_id,
                  "mercedes-wis-virtualbox-en-general")
        finally:
            shutil.rmtree(bare, ignore_errors=True)

        print("\n-- structured links that contradict the text are ignored --")
        # the real WIS 1.1 failure: links point at a different model than the
        # document states (a Sprinter bulletin linked to the V-Class)
        badlink = Path(tempfile.mkdtemp(prefix="ar_badlink_"))
        try:
            cdir = badlink / "wis_en"; cdir.mkdir(parents=True)
            recs = [rec(9001, "Overview of innovations to Sprinter", "Sprinter body text.",
                        applic=["MODEL 901, 902"], series=["447"]),
                    rec(9002, "W201 brake note", "190 brake text.",
                        applic=["MODEL 201"], series=["124"]),
                    rec(9003, "C-Class wiper", "wiper text.",
                        applic=["MODEL 204"], series=["212"]),
                    rec(9004, "Untold", "no model stated here.", series=["230"]),
                    # engine-only document: filed under its engine, never general
                    rec(9005, "Tightening specifications for cylinder head bolts",
                        "head bolt text.", applic=["ENGINE 102.983"], series=["444"]),
                    # names both: filed under both, shown once in a joint scope
                    rec(9006, "Remove/install cylinder head", "head text.",
                        applic=["ENGINE 102.983 in MODEL 201"], series=["171"])]
            with (cdir / "records.jsonl").open("w", encoding="utf-8", newline="\n") as f:
                for r_ in recs: f.write(json.dumps(r_) + "\n")
            (cdir / "manifest.json").write_text(json.dumps(
                {**MANIFEST, "record_count": len(recs)}), encoding="utf-8")
            check("agreement measured", structured_agreement(cdir / "records.jsonl"), (4, 0.0))
            bi = importers.get("records:model")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                bv = {v.doc_id.rsplit("-", 1)[1]: v for v in bi.discover(badlink)}
                filed = {k: sorted(p.page_no for p in bi.pages(v)) for k, v in bv.items()}
            check("the contradiction is announced", "structured model links IGNORED" in buf.getvalue(), True)
            check("filed by the documents' own MODEL and ENGINE lines",
                  sorted(k for k in filed if k != "general"),
                  ["201", "204", "901", "902", "e102"])
            check("an engine-only document goes to its engine bucket", filed["e102"],
                  ["9005"])
            check("one naming its car too stays with the car", "9006" in filed["201"], True)
            check("the engine bucket is labelled as an engine", bv["e102"].model, "Engine 102")
            check("nothing lands on the wrongly linked cars",
                  [k for k in filed if k in ("447", "124", "212", "230", "444", "171")], [])
            check("a document stating no model goes to the general pool",
                  filed.get("general"), ["9004"])
            check("record_series can be told not to trust the link",
                  record_series({"model_series": ["447"], "applicability_notes": ["MODEL 901"]},
                                trust_structured=False), ["901"])
        finally:
            shutil.rmtree(badlink, ignore_errors=True)

        print("\n-- schema 1.2: real applicability, engines by role --")
        # the WS*/WC* join: vehicle and engine roles come from different
        # tables, so 906 the engine and 906 the Sprinter can't be confused
        good = Path(tempfile.mkdtemp(prefix="ar_v12_"))
        try:
            cdir = good / "wis_en"; cdir.mkdir(parents=True)

            def rec12(n, title, body, applic=(), series=(), engines=(), summary=""):
                return {**rec(n, title, body, applic=applic, series=series),
                        "engine_series": list(engines), "transmission_series": [],
                        "applicability_summary": summary}
            recs = [rec12(9101, "Sprinter bulletin", "text.", ["MODEL 901, 902"], ["901", "902"]),
                    rec12(9102, "W201 brakes", "text.", ["MODEL 201"], ["201"]),
                    # no MODEL line in the text: the table alone ties it to the car
                    rec12(9103, "Remove/install cylinder head", "head text.",
                          series=["201"], engines=["102"],
                          summary="MODEL 201 with ENGINE 102"),
                    # an engine document numbered like a vehicle: role says engine
                    rec12(9104, "OM906 injector", "text.", engines=["906"]),
                    rec12(9105, "Truly general", "text."),
                    # no row at all, but the text names a model: prose fallback
                    rec12(9106, "Untied W124 note", "text.", ["MODEL 124"])]
            with (cdir / "records.jsonl").open("w", encoding="utf-8", newline="\n") as f:
                for r_ in recs: f.write(json.dumps(r_) + "\n")
            (cdir / "manifest.json").write_text(json.dumps(
                {**MANIFEST, "schema_version": "1.2", "record_count": len(recs)}),
                encoding="utf-8")
            gi = importers.get("records:model")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                gv = {v.doc_id.rsplit("-", 1)[1]: v for v in gi.discover(good)}
                gfiled = {k: sorted(p.page_no for p in gi.pages(v)) for k, v in gv.items()}
            check("agreeing links are trusted", "trusted" in buf.getvalue()
                  and "IGNORED" not in buf.getvalue(), True)
            check("a document with no MODEL line reaches its car", "9103" in gfiled["201"], True)
            check("...but not the engine bucket: other cars with that engine search it",
                  gfiled.get("e102"), None)
            check("an engine numbered like a vehicle stays an engine",
                  (gfiled.get("e906"), "906" in gfiled), (["9104"], False))
            check("no row, but a MODEL line: prose fallback", gfiled.get("124"), ["9106"])
            check("no row, no MODEL line: general", gfiled.get("general"), ["9105"])
            page = next(p for p in gi.pages(gv["201"]) if p.page_no == "9103")
            check("the validity text is searchable", "Validity: MODEL 201 with ENGINE 102" in page.text, True)
        finally:
            shutil.rmtree(good, ignore_errors=True)

        print("\n-- engine narrowing --")
        from autorundown.importers.records import engines_key, with_engine_labels
        from autorundown.search import scope_ids
        check("page engines from the engine role",
              engines_key({"engine_series": ["642"], "model_series": ["212"]}), ",642,")
        check("...else from ENGINE lines", engines_key({"applicability_notes": ["ENGINE 272.980"]}), ",272,")
        check("no engine named: any engine", engines_key({"model_series": ["212"]}), "")
        check("an engine in the title, when nothing else says",
              engines_key({"title": "Cylinder head cover, OM457"}), ",457,")
        check("bolt sizes are not engines", engines_key({"title": "Bolt M12x1.5, M8 nut"}), "")
        check("engine label from the table",
              with_engine_labels({"engines": ["272.980"]}, {"272": "M272 · V6 petrol"})["engine_families"],
              {"272": "M272 · V6 petrol"})
        w212 = {"doc_id": "x-212", "general_doc_id": "x-general", "vehicle_types": ",0,",
                "variants": json.dumps([
                    {"name": "E 300", "engines": ["272.952", "276.952"], "engine_ids": ["x-e272", "x-e276"]},
                    {"name": "E 350 BlueTEC", "engines": ["642.852"], "engine_ids": ["x-e642"]}])}
        s = scope_ids(w212, engine="272")
        check("an engine choice adds only that engine's bucket, though 'E 300' spans two",
              list(s), ["x-212", "x-e272", "x-general"])
        check("...and filters pages to that engine", s.engines, ("272",))
        check("a car choice filters to its engines", scope_ids(w212, variant=0).engines, ("272", "276"))
        check("no choice: no engine filter", scope_ids(w212).engines, ())

        print("\n-- exact codes: 'except 102.983', 'Engine 102.942 ...' --")
        from autorundown.importers.records import code_limits
        check("an 'except' excludes the 2.3-16's engine (the page the user was given)",
              code_limits({"title": "0560 - Adjustment of the valve clearance Engine 102 "
                                    "(except 102.983/99 and with hydraulic valve-clearance compensation)"}),
              ("", ",102.983,"))
        check("a code in the title is the page's only target",
              code_limits({"title": "0106 - Removal and installation of cylinder head cover "
                                    "Engine 102.942 multi belt drive"}), (",102.942,", ""))
        check("slash variants and validity text count",
              code_limits({"title": "Cover", "applicability_summary": "MODEL 906.111 /113 except 906.211"}),
              ("", ",906.211,"))
        check("no codes: no limit", code_limits({"title": "Engine 102 KE in model 201"}), ("", ""))
        w201 = {"doc_id": "x-201", "variants": json.dumps([
            {"name": "190 E 2.3 16", "baumuster": ["201.034"], "engines": ["102.983"],
             "engine_ids": ["x-e102"], "engine_by_bm": {"201.034": ["102.983"]}}])}
        check("a chosen car carries its exact codes", scope_ids(w201, variant=0).codes,
              (("201.034",), ("102.983",)))

        print("\n-- US VIN -> exact variant (AW_VIN2FIN) --")
        from autorundown.importers.records import RecordsImporter
        vdir = Path(tempfile.mkdtemp(prefix="ar_vin_"))
        try:
            # exact rows from the real table: 190 E 2.3-16 and C 300 4MATIC
            (vdir / "vin2fin.json").write_text(json.dumps(
                {"WDBDA34D4": "201.034", "WDBDA34D5": "201.034",
                 "WDDGF8AB3": "204.080"}), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                by_bm = RecordsImporter._vin2fin(vdir, {"vin2fin_file": "vin2fin.json"})
            v = RecordsImporter._with_vin9({"name": "190 E 2.3 16",
                                            "baumuster": ["201.034"]}, by_bm)
            check("a variant carries the VIN prefixes that decode to it",
                  v["vin9"], ["WDBDA34D4", "WDBDA34D5"])
            check("a variant no VIN decodes to is left as it was",
                  "vin9" in RecordsImporter._with_vin9({"baumuster": ["201.018"]}, by_bm), False)
            check("no vin2fin.json: nothing, quietly",
                  RecordsImporter._vin2fin(vdir, {"vin2fin_file": "missing.json"}), {})
        finally:
            shutil.rmtree(vdir, ignore_errors=True)

        print("\n-- schema 1.3: converted graphics --")
        img = Path(tempfile.mkdtemp(prefix="ar_v13_"))
        try:
            cdir = img / "wis_en"; (cdir / "images" / "8").mkdir(parents=True)
            (cdir / "images" / "8" / "500.svg").write_text("<svg/>", encoding="utf-8")
            (cdir / "images" / "8" / "501.png").write_bytes(b"\x89PNG\r\n\x1a\n")

            def rec13(n, title, body, files=(), text="", series=("201",)):
                return {**rec(n, title, body, applic=["MODEL 201"], series=series,
                              figs=(500, 501, 502)),
                        "engine_series": [], "transmission_series": [],
                        "applicability_summary": "", "image_files": list(files),
                        "figure_text": text}
            recs = [rec13(9201, "Wiring diagram of radio", "", ["8/500.svg"],
                          "N26/15 PE 54.21-D-2104AA 1,5 ge/bl"),
                    rec13(9202, "Tightening diagram of cylinder head bolts",
                          "Tighten in the specified order.", ["8/501.png"]),
                    # listed but never converted: must not become a citable figure
                    rec13(9203, "Missing graphic", "", ["8/502.png"]),
                    rec13(9204, "Escape attempt", "", ["../../secret.png"]),
                    rec13(9205, "Remove/install pump",
                          "1 Remove bolt.\n[diagram: 8/501.png]\n2 Remove pump.\n"
                          "[diagram: 8/502.png]", ["8/501.png"])]
            with (cdir / "records.jsonl").open("w", encoding="utf-8", newline="\n") as f:
                for r_ in recs: f.write(json.dumps(r_) + "\n")
            (cdir / "manifest.json").write_text(json.dumps(
                {**MANIFEST, "schema_version": "1.3", "record_count": len(recs)}),
                encoding="utf-8")
            ii = importers.get("records:model")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                iv = {v.doc_id.rsplit("-", 1)[1]: v for v in ii.discover(img)}
                ipages = {p.page_no: p for p in ii.pages(iv["201"])}
            check("a figure-only record with a graphic becomes a page", "9201" in ipages, True)
            check("...carrying its image", ipages["9201"].images, ["8/500.svg"])
            check("...and its diagram labels as searchable text",
                  "Diagram labels: N26/15 PE 54.21-D-2104AA" in ipages["9201"].text, True)
            check("a procedure page gets its figure", ipages["9202"].images, ["8/501.png"])
            check("...as a [diagram:] marker the answer model can cite, before the body",
                  ipages["9202"].text.index("[diagram: 8/501.png]")
                  < ipages["9202"].text.index("Tighten in the specified order"), True)
            check("a graphic missing on disk is never attached, and with no text "
                  "the record is still dropped", "9203" in ipages, False)
            check("paths can't climb out of images/", "9204" in ipages, False)
            check("a figure placed inline after its step is not repeated up front",
                  ipages["9205"].text.count("[diagram: 8/501.png]"), 1)
            check("...and stays after its step",
                  ipages["9205"].text.index("1 Remove bolt")
                  < ipages["9205"].text.index("[diagram: 8/501.png]"), True)
            check("an inline marker to a file not on disk is removed",
                  "[diagram: 8/502.png]" in ipages["9205"].text, False)
            check("the arithmetic still closes", "5 records = 3 indexed + 2 figure-only"
                  in buf.getvalue(), True)
        finally:
            shutil.rmtree(img, ignore_errors=True)

        print("\n-- record accounting --")
        for mode in ("records:corpus", "records:model"):
            imp2 = importers.get(mode)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                for veh in imp2.discover(tmp):
                    list(imp2.pages(veh))
            log = buf.getvalue()
            check(f"{mode}: reconciliation printed",
                  "15 records = 12 indexed + 3 figure-only" in log, True)
            check(f"{mode}: manifest verified",
                  "manifest record_count 15: matches" in log, True)

        # a truncated file with two corrupt lines must be called out, not
        # silently treated as a smaller corpus
        bad = Path(tempfile.mkdtemp(prefix="ar_bad_"))
        try:
            cdir = build_corpus(bad)
            lines = (cdir / "records.jsonl").read_text(
                encoding="utf-8").splitlines()
            lines[3] = lines[3][:60]          # cut mid-JSON
            lines[5] = "{not json at all"
            (cdir / "records.jsonl").write_text(
                "\n".join(lines[:-2]) + "\n", encoding="utf-8")  # and truncate
            imp3 = importers.get("records:corpus")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                for veh in imp3.discover(bad):
                    list(imp3.pages(veh))
            log = buf.getvalue()
            check("malformed lines counted, not swallowed",
                  "2 MALFORMED" in log, True)
            check("malformed lines called data loss",
                  "this is data loss, not filtering" in log, True)
            check("truncation caught against the manifest",
                  "manifest says 15 records but records.jsonl has 13" in log,
                  True)
            check("truncation named", "looks truncated" in log, True)
        finally:
            shutil.rmtree(bad, ignore_errors=True)

        print("\n-- schema migration --")
        old = tmp / "old.db"
        raw = sqlite3.connect(str(old))
        raw.execute("CREATE TABLE vehicles(doc_id TEXT PRIMARY KEY, "
                    "source_name TEXT, file_path TEXT, make TEXT, model TEXT, "
                    "year_range TEXT, engine TEXT, drivetrain TEXT, "
                    "doc_type TEXT, page_count INTEGER)")
        raw.execute("INSERT INTO vehicles(doc_id, model) VALUES ('old','Legacy')")
        raw.commit()
        raw.close()
        conn = index.connect(old)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(vehicles)")}
        check("general_doc_id added", "general_doc_id" in cols, True)
        check("model_code added", "model_code" in cols, True)
        check("existing row survives",
              conn.execute("SELECT model FROM vehicles WHERE doc_id='old'"
                           ).fetchone()[0], "Legacy")
        conn.close()

        print("\n-- ingest + search (model mode) --")
        db = tmp / "index.db"
        rc = ingest.main(["--archive", str(tmp), "--index", str(db)])
        check("ingest exit code", rc, 0)
        conn = index.connect(db)
        conn.row_factory = sqlite3.Row
        rows = {r["doc_id"]: dict(r) for r in index.vehicles(conn)}
        # the default ingest is passenger-only: the commercial Sprinter T1N
        # (901) is left out; everything else stays
        check("vehicles indexed", len(rows), 11)
        check("commercial series left out by default",
              "mercedes-wis-virtualbox-en-901" in rows, False)
        check("records:model still has it",
              any(x.doc_id.endswith("-901") for x in mvehicles), True)
        veh176 = rows["mercedes-wis-virtualbox-en-176"]
        check("scope is model + general", search.scope_ids(veh176),
              ["mercedes-wis-virtualbox-en-176",
               "mercedes-wis-virtualbox-en-general"])
        check("general bucket scopes to itself only",
              search.scope_ids(rows["mercedes-wis-virtualbox-en-general"]),
              ["mercedes-wis-virtualbox-en-general"])

        scope = search.scope_ids(veh176)
        hits = search.search(conn, scope, "bleed brake system bleeder", 8)
        check("general procedure reachable from a model",
              [h["page_no"] for h in hits][:1], ["1002299"])
        # the OR top-up is deliberately loose, so a model-only search still
        # returns something -- just never the general page
        check("model-only search cannot reach the general page",
              [h["page_no"] for h in search.search(
                  conn, "mercedes-wis-virtualbox-en-176",
                  "bleed brake system bleeder") if h["page_no"] == "1002299"],
              [])

        mixed = search.search(conn, scope, "brake caliper bolts", 8)
        check("model-specific page leads the interleave",
              mixed[0]["doc_id"], "mercedes-wis-virtualbox-en-176")
        check("nothing outside the scope leaks in",
              [h["doc_id"] for h in mixed if h["doc_id"] not in scope], [])
        check("another model's scope never returns 176's pages",
              [h["doc_id"] for h in search.search(
                  conn, search.scope_ids(rows["mercedes-wis-virtualbox-en-204"]),
                  "unscrew the brake caliper tighten caliper bolts 30")
               if h["doc_id"].endswith("-176")], [])

        check("get_page finds a general page through a model scope",
              (search.get_page(conn, scope, "1002299") or {}).get("title"),
              "Bleed the brake system")
        check("get_page finds the model's own page",
              (search.get_page(conn, scope, "1002296") or {}).get("title"),
              "Remove/install front brake pads")
        check("get_page still refuses a page outside the scope",
              search.get_page(conn, scope, "1002303"), None)

        # vehicle-type narrowing of the general companion
        check("model types come from documents naming only that model",
              veh176["vehicle_types"], ",2,")
        check("variants stored in the index as JSON",
              [v["name"] for v in json.loads(veh176["variants"] or "[]")],
              ["A 180", "A 200 CDI", "A 160"])
        check("vin codes stored in the index",
              rows["mercedes-wis-virtualbox-en-201"]["vin_codes"], ",WDB:D,")
        e102 = "mercedes-wis-virtualbox-en-102"
        veh201 = rows["mercedes-wis-virtualbox-en-201"]
        check("an engine series is labelled as an engine", rows[e102]["model"], "Engine 102")
        v201 = json.loads(veh201["variants"])
        check("190 E 2.3 16 reaches the M102 engine bucket", v201[0]["engine_ids"], [e102])
        check("an engine with no documents adds nothing", v201[1]["engine_ids"], [])
        check("an engine number that is another car's series is not borrowed",
              json.loads(veh176["variants"])[1]["engine_ids"], [])
        check("...so the A 200 CDI scope never contains the W201",
              "mercedes-wis-virtualbox-en-201" in search.scope_ids(veh176, 1), False)
        check("a car series is never labelled an engine",
              veh201["model"], "W201 190")
        check("variant scope: car, its engine, then general",
              list(search.scope_ids(veh201, 0)),
              ["mercedes-wis-virtualbox-en-201", e102, "mercedes-wis-virtualbox-en-general"])
        check("the M103 variant does not get the M102 bucket",
              e102 in search.scope_ids(veh201, 1), False)
        check("choosing the series includes every variant's engine",
              e102 in search.scope_ids(veh201), True)
        gasket = [h["page_no"] for h in search.search(
            conn, search.scope_ids(veh201, 0), "cylinder head gasket sealing surface", 6)]
        check("the 2.3-16's engine document is found from the car", "1002306" in gasket
              or "1002309" in gasket, True)
        check("...specifically the M102 one, despite its different type bit",
              "1002309" in gasket, True)
        check("the car's own series scope alone never had it",
              "1002309" in [h["page_no"] for h in search.search(
                  conn, "mercedes-wis-virtualbox-en-201", "cylinder head gasket sealing surface", 6)],
              False)
        check("an engine page opens from the car (series-level page scope)",
              (search.get_page(conn, search.scope_ids(veh201), "1002309") or {}).get("page_no"),
              "1002309")
        check("all-types documents say nothing: no narrowing",
              rows["mercedes-wis-virtualbox-en-204"]["vehicle_types"] or "", "")
        check("general bucket carries none", rows[
              "mercedes-wis-virtualbox-en-general"]["vehicle_types"] or "", "")
        oil = [h["page_no"] for h in search.search(
            conn, scope, "engine oil capacity", 10)]
        check("a bus-only (type 5) general doc stays out of a type-2 model",
              "1002307" in oil, False)
        check("an all-types general doc is still in scope", "1002308" in oil, True)
        check("without the type filter the bus doc would have surfaced",
              "1002307" in [h["page_no"] for h in search.search(
                  conn, list(scope), "engine oil capacity", 10)], True)
        check("a cited page still opens regardless of type",
              (search.get_page(conn, scope, "1002307") or {}).get("page_no"),
              "1002307")
        conn.close()

        print("\n-- ingest (corpus mode) --")
        db2 = tmp / "index2.db"
        rc = ingest.main(["--archive", str(tmp), "--index", str(db2),
                          "--importer", "records:corpus"])
        check("ingest exit code", rc, 0)
        conn = index.connect(db2)
        conn.row_factory = sqlite3.Row
        check("one vehicle", index.stats(conn)["vehicles"], 1)
        check("pages indexed", index.stats(conn)["pages"], 12)
        check("single doc_id still works",
              [h["page_no"] for h in search.search(
                  conn, "mercedes-wis-virtualbox-en", "SI00.00-D-0026A")][:1],
              ["1002295"])
        conn.close()

        print(f"\n{len(FAILURES)} failure(s)" +
              ("".join(f"\n  - {f}" for f in FAILURES) if FAILURES else ""))
        return 1 if FAILURES else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
