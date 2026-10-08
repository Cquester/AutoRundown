#!/usr/bin/env python3
"""AutoRundown extractor - PARSE stage (config-driven).
Reads <AR_OUT>/bodies.pack + metadata.tsv + figures.tsv and writes records.jsonl.
Handles all four WIS body formats (SIDS gzip, RTF stub, LZW->XML). Dedups by DokID.

Config via environment:
  AR_OUT   output directory (same one the pull stage wrote to)
  AR_LANG  language code written into each record (2->"en", etc.)
"""
import json, os, struct, sys, collections, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parse_sids import parse_doc, bits_to_indices

SCHEMA_VERSION = "1.1"  # 1.1 adds model/model-year applicability fields (see IMPORT_SPEC.md §3)

OUT = os.environ.get("AR_OUT") or "."
LANG_CODE = int(os.environ.get("AR_LANG", "2"))
# Authoritative SprachCode -> ISO map, read from WIS table W_SprachCode2ISOSprachCode.
LANG_MAP = {1: "de", 2: "en", 3: "fr", 4: "es", 5: "pt", 6: "it", 7: "nl",
            8: "da", 9: "sv", 10: "fi", 12: "el", 13: "en-US", 15: "no",
            17: "tr", 20: "ja", 21: "hu", 22: "ru", 24: "cs", 25: "pl",
            27: "bg", 28: "zh-CN", 29: "ro", 39: "zh-TW", 81: "sl", 82: "hr",
            86: "ko", 87: "th", 88: "vi", 90: "sk"}
LANG = LANG_MAP.get(LANG_CODE, str(LANG_CODE))


def read_pack(path):
    blobs = {}
    with open(path, "rb") as f:
        while True:
            hdr = f.read(8)
            if len(hdr) < 8:
                break
            dokid, ln = struct.unpack(">ii", hdr)
            data = f.read(ln)
            if len(data) < ln:
                sys.stderr.write(f"truncated pack at DokID {dokid}\n")
                break
            blobs[dokid] = data
    return blobs


def load_meta(path):
    meta = {}
    with open(path, encoding="utf-8") as f:
        hdr = f.readline().rstrip("\n").split("\t")
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < len(hdr):
                continue
            row = dict(zip(hdr, p))
            meta[row["DokID"]] = row
    return meta


def load_figs(path):
    figs = {}
    if not os.path.exists(path):
        return figs
    with open(path, encoding="utf-8") as f:
        fhdr = f.readline().rstrip("\n").split("\t")
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < len(fhdr):
                continue
            r = dict(zip(fhdr, p))
            figs.setdefault(r["DokID"], {})[r["GraphikID"]] = r
    return figs


def load_baumuster(path):
    """W_Baumusterzeile -> {BMZeilenNr: {reihe, baumuster, specific, wildcard_series, von, bis}}.
    Baumuster is reihe + the leading non-wildcard digits (e.g. 201.034); a line whose
    BM4/5/6 are all '*' is a whole-series line (applies to every 201.xxx)."""
    bmz = {}
    if not os.path.exists(path):
        return bmz
    with open(path, encoding="utf-8") as f:
        f.readline()  # header
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 7:
                continue
            nr, reihe, d4, d5, d6, von, bis = p[0], p[1], p[2], p[3], p[4], p[5], p[6]
            digits = ""
            for d in (d4, d5, d6):
                if d in ("", "*"):
                    break
                digits += d
            baumuster = (reihe + "." + digits) if digits else reihe
            specific = all(d not in ("", "*") for d in (d4, d5, d6))
            wildcard_series = all(d in ("", "*") for d in (d4, d5, d6))
            try:
                bmz[int(nr)] = {"reihe": reihe, "baumuster": baumuster, "specific": specific,
                                "wildcard_series": wildcard_series,
                                "von": von or None, "bis": bis or None}
            except ValueError:
                continue
    return bmz


def series_spans(bmz):
    """reihe -> earliest BMProdVon / latest BMProdBis across the catalogue (ISO dates sort lexically)."""
    von, bis = {}, {}
    for r in bmz.values():
        s = r["reihe"]
        if r["von"]:
            von[s] = r["von"] if s not in von else min(von[s], r["von"])
        if r["bis"]:
            bis[s] = r["bis"] if s not in bis else max(bis[s], r["bis"])
    return von, bis


def load_dok2reihe(path):
    """W_Dok2Reihe -> {DokNrID: set(ReihenID)}. ReihenID == W_Baumusterzeile.BMZeilenNr."""
    d2r = collections.defaultdict(set)
    if not os.path.exists(path):
        return d2r
    with open(path, encoding="utf-8") as f:
        f.readline()  # header
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 3:
                continue
            try:
                d2r[int(p[0])].add(int(p[2]))
            except ValueError:
                continue
    return d2r


def resolve_models(dok_nr_id, d2r, bmz, svon, sbis):
    """Which models/years a document applies to, via its W_Dok2Reihe -> Baumuster lines.
    Year bounds fall back to the model series' overall production span when a line is
    unscoped (whole-series lines carry no date of their own)."""
    empty = {"model_series": [], "baumuster": [], "model_year_from": None,
             "model_year_to": None, "applies_all_series": False}
    ids = d2r.get(dok_nr_id)
    if not ids:
        return empty
    series, bau, vons, biss, all_series = set(), set(), [], [], False
    for rid in ids:
        r = bmz.get(rid)
        if not r:
            continue
        series.add(r["reihe"])
        if r["specific"]:
            bau.add(r["baumuster"])
        if r["wildcard_series"]:
            all_series = True
        ev = r["von"] or svon.get(r["reihe"])
        eb = r["bis"] or sbis.get(r["reihe"])
        if ev:
            vons.append(ev)
        if eb:
            biss.append(eb)
    return {"model_series": sorted(series), "baumuster": sorted(bau),
            "model_year_from": (min(vons) if vons else None),
            "model_year_to": (max(biss) if biss else None),
            "applies_all_series": all_series}


def write_catalogue(path, bmz, svon, sbis):
    """models.jsonl sidecar: one line per model series with its production span + Baumuster."""
    by_series = collections.defaultdict(lambda: {"baumuster": set(), "bm_zeilen": 0})
    for r in bmz.values():
        e = by_series[r["reihe"]]
        e["bm_zeilen"] += 1
        if r["specific"]:
            e["baumuster"].add(r["baumuster"])
    with open(path, "w", encoding="utf-8") as f:
        for reihe in sorted(by_series):
            e = by_series[reihe]
            f.write(json.dumps({"reihe": reihe, "year_from": svon.get(reihe),
                                "year_to": sbis.get(reihe), "baumuster": sorted(e["baumuster"]),
                                "bm_zeilen": e["bm_zeilen"]}, ensure_ascii=False) + "\n")
    return len(by_series)


def main():
    print(f"parse: out={OUT} lang={LANG}")
    blobs = read_pack(os.path.join(OUT, "bodies.pack"))
    print(f"  {len(blobs)} bodies")
    meta = load_meta(os.path.join(OUT, "metadata.tsv"))
    print(f"  {len(meta)} docs")
    figs = load_figs(os.path.join(OUT, "figures.tsv"))

    # model / model-year applicability (from pull_applic.js; degrades gracefully if absent)
    bmz = load_baumuster(os.path.join(OUT, "baumusterzeilen.tsv"))
    d2r = load_dok2reihe(os.path.join(OUT, "dok2reihe.tsv"))
    svon, sbis = series_spans(bmz)
    if bmz and d2r:
        n_series = write_catalogue(os.path.join(OUT, "models.jsonl"), bmz, svon, sbis)
        print(f"  applicability: {len(bmz)} model lines, {len(d2r)} linked docs, {n_series} series")
    else:
        n_series = 0
        print("  applicability: MISSING baumusterzeilen.tsv/dok2reihe.tsv "
              "(run pull_applic.js) - records will have empty model fields")

    jsonl = open(os.path.join(OUT, "records.jsonl"), "w", encoding="utf-8")
    summ = open(os.path.join(OUT, "PARSE_SUMMARY.tsv"), "w", encoding="utf-8")
    summ.write("DokID\tDokNr\tformat\tbody_chars\tbody_words\txrefs\tapplic\tfigures\ttitle\n")

    ok = err = nobody = with_models = 0
    fmt_counts = collections.Counter()
    for i, (dokid, row) in enumerate(meta.items(), 1):
        did = int(dokid)
        blob = blobs.get(did)
        try:
            if blob:
                fmt, header, body, xrefs, applic, imgrefs = parse_doc(dokid, blob, row["DokTitel"])
            else:
                fmt, header, body, xrefs, applic, imgrefs = "none", None, "", [], [], []
                nobody += 1
        except Exception as e:
            summ.write(f"{dokid}\t{row.get('DokNrID','')}\tERROR\t\t\t\t\t\t{e}\n")
            err += 1
            continue

        dfigs = list(figs.get(dokid, {}).values())
        m = resolve_models(int(row["DokNrID"]), d2r, bmz, svon, sbis)
        if m["model_series"]:
            with_models += 1
        rec = {
            "dok_id": did, "dok_nr_id": int(row["DokNrID"]), "dok_type": int(row["DokTypCode"]),
            "lang": LANG, "title": row["DokTitel"].strip(),
            "fzg_art_bits": row["FzgArtCodes"], "fzg_art_indices": bits_to_indices(row["FzgArtCodes"]),
            "kurzgueltigkeit_id": int(row["KurzgueltigkeitID"]), "cluster": int(row["Cluster"]),
            "body_format": fmt, "applicability_notes": applic, "cross_refs": xrefs, "image_refs": imgrefs,
            "model_series": m["model_series"], "baumuster": m["baumuster"],
            "model_year_from": m["model_year_from"], "model_year_to": m["model_year_to"],
            "applies_all_series": m["applies_all_series"],
            "figures": [{"graphik_id": int(g["GraphikID"]), "graphik_nr": g["GraphikNr"],
                         "klasse": int(g["GraphikKlasse"]), "cluster": int(g["Cluster"])} for g in dfigs],
            "body_text": body, "sids_header": header,
        }
        jsonl.write(json.dumps(rec, ensure_ascii=False) + "\n")
        summ.write(f"{dokid}\t{row['DokNrID']}\t{fmt}\t{len(body)}\t{len(body.split())}\t"
                   f"{len(xrefs)}\t{len(applic)}\t{len(dfigs)}\t{rec['title'][:60]}\n")
        ok += 1
        fmt_counts[fmt] += 1
        if i % 20000 == 0:
            print(f"  parsed {i}...")

    jsonl.close(); summ.close()

    # provenance manifest the importer reads (what this run produced)
    records_path = os.path.join(OUT, "records.jsonl")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source_type": "mercedes-wis-virtualbox",
        "source_id": os.environ.get("AR_HOST", ""),
        "database": os.environ.get("AR_DB", "wisnet"),
        "language": {"sprach_code": LANG_CODE, "iso": LANG},
        "record_count": ok,
        "empty_body_count": nobody,
        "error_count": err,
        "body_format_counts": dict(fmt_counts),
        "applicability": {
            "model_lines": len(bmz),
            "linked_docs": len(d2r),
            "model_series_count": n_series,
            "records_with_models": with_models,
            "models_file": ("models.jsonl" if bmz else None),
        },
        "records_file": "records.jsonl",
        "records_bytes": (os.path.getsize(records_path) if os.path.exists(records_path) else 0),
        "extracted_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, ensure_ascii=False, indent=2)

    print(f"parse done: records={ok} errors={err} empty_body={nobody} -> {records_path}")
    print(f"manifest  -> {os.path.join(OUT, 'manifest.json')}")


if __name__ == "__main__":
    main()
