"""Rewrite records.jsonl with the real WIS applicability (schema 1.2). Offline.

    python apply_applic.py [--extract <wis_extract dir>] [--applic <wis_applic2 dir>]

Schema 1.1 joined W_Dok2Reihe.ReihenID straight to W_Baumusterzeile.BMZeilenNr.
They are different keys whose ranges overlap, so the join produced plausible
nonsense: 98.4% of documents that state their model in text were linked to a
different one (HANDBACK.md section 0). ReihenID is an applicability
*combination*; it resolves to model lines through the WS*/WC* tables, and the
table a row comes from is the line's role:

    W_Dok2Reihe (DokNrID, ReihenID)            ReihenID -1 = "all"
      -> WS1..3 / WC1..8 (BMZeilenNr, ReihenID)   1 = vehicle, 2 = engine,
                                                 3 = transmission, 4-8 other
      -> W_Baumusterzeile.BMReihe (series)

`pull_applic2.js` pulls those tables and its analysis writes
`doc_applicability.tsv` (one row per DokNrID). Validated on the English set:
the vehicle role agrees with the document's own MODEL lines on 99.94% of
108,853 documents, the engine role with ENGINE lines on 99.1%.

What this writes, per record (joined on dok_nr_id):
  model_series         vehicle-role series (WS1/WC1)       -- replaces 1.1
  baumuster            vehicle-role Baumuster, fully specific ones only
                       ("215.378"); wildcard rows say "whole series"
  engine_series        engine-role series (WS2/WC2)        -- new
  transmission_series  transmission-role series (WS3/WC3)  -- new
  model_year_from/to   null: the only dates are W_Baumusterzeile's, which are
                       documentation windows, not production years
  applicability_summary  W_Kurzgueltigkeit's one-line validity text for the
                       record's kurzgueltigkeit_id ("MODEL 129 up to 31.12.93")
                                                            -- new
A record with no row gets empty lists: genuinely untied, not "every model".

The previous file is kept as records.v1.1.jsonl; the manifest gets
schema_version 1.2 and the applicability counts.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "autorundown_out"

_MOJIBAKE = re.compile(
    "[Â-ô]"
    "[\u0080-¿ŒœŠšŸŽžƒˆ˜"
    "–—‘-„†-•…‰‹›€™]+")


def fix_mojibake(text: str) -> str:
    """UTF-8 read as cp1252 or Latin-1; rewrites a run only if it is valid UTF-8."""
    if not text or text.isascii():
        return text

    def one(m):
        try:
            return bytes(ord(c) if ord(c) < 256 else c.encode("cp1252")[0]
                         for c in m.group(0)).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return m.group(0)
    return _MOJIBAKE.sub(one, text)


def split(cell: str) -> list[str]:
    return [s for s in (cell or "").split(",") if s and s != "000"]


def specific_bm(cell: str) -> list[str]:
    """"215.3.7.8" -> "215.378"; any "*" means the row is series-wide: skip it."""
    out = []
    for bm in split(cell):
        parts = bm.split(".")
        if len(parts) == 4 and "*" not in bm and parts[0] != "000":
            out.append(f"{parts[0]}.{''.join(parts[1:])}")
    return sorted(set(out))


ROLES = {"WS1": "vehicle", "WC1": "vehicle", "WS2": "engine", "WC2": "engine",
         "WS3": "transmission", "WC3": "transmission",
         **{f"WC{i}": "other" for i in range(4, 9)}}


def _tsv(path: Path):
    import csv
    csv.field_size_limit(10**9)
    with path.open(encoding="utf-8", errors="replace", newline="") as f:
        yield from csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE)


def build_doc_applicability(applic2: Path, applic1: Path) -> Path:
    """The WS/WC join -> doc_applicability.tsv (one row per DokNrID).

    Inputs: W_Baumusterzeile + W_Dok2Reihe from pull_applic.js (`applic1`,
    autorundown_out/wis_applic) and WS1-3/WC1-8 from pull_applic2.js
    (`applic2`). ReihenID -1 ("all") has no WS/WC row, so it maps to nothing.
    """
    bmz = {int(r["BMZeilenNr"]): (r["BMReihe"], r["BM4"], r["BM5"], r["BM6"])
           for r in _tsv(applic1 / "baumusterzeilen.tsv")}
    rid: dict[int, dict[str, set]] = {}
    for table, role in ROLES.items():
        for r in _tsv(applic2 / f"{table}.tsv"):
            b = bmz.get(int(r["BMZeilenNr"]))
            if b and b[0] != "000":
                rid.setdefault(int(r["ReihenID"]), {}).setdefault(role, set()).add(".".join(b))
    d2r: dict[int, set] = {}
    for r in _tsv(applic1 / "dok2reihe.tsv"):
        d2r.setdefault(int(r["DokNrID"]), set()).add(int(r["ReihenID"]))
    out = applic2 / "doc_applicability.tsv"
    n = 0
    with out.open("w", encoding="utf-8", newline="") as f:
        f.write("DokNrID\tvehicle_series\tengine_series\ttransmission_series"
                "\tother_series\tvehicle_baumuster\n")
        for nr in sorted(d2r):
            s: dict[str, set] = {}
            for x in d2r[nr]:
                for role, v in rid.get(x, {}).items():
                    s.setdefault(role, set()).update(v)
            if not s:
                continue
            ser = lambda k: ",".join(sorted({b[:3] for b in s.get(k, ())}))
            f.write(f"{nr}\t{ser('vehicle')}\t{ser('engine')}\t{ser('transmission')}"
                    f"\t{ser('other')}\t{','.join(sorted(s.get('vehicle', ())))}\n")
            n += 1
    print(f"built {out.name}: {n} DokNrIDs")
    return out


def load_applic(path: Path) -> dict[int, dict]:
    out = {}
    with path.open(encoding="utf-8") as f:
        head = f.readline().rstrip("\n").split("\t")
        for line in f:
            row = dict(zip(head, line.rstrip("\n").split("\t")))
            out[int(row["DokNrID"])] = row
    return out


def load_summaries(path: Path, wanted: set[int]) -> dict[int, str]:
    """Only the ids the records use; the table holds every language."""
    out = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8", errors="replace") as f:
        f.readline()
        for line in f:
            kid, _, text = line.rstrip("\n").partition("\t")
            if kid.isdigit() and int(kid) in wanted:
                out[int(kid)] = " ".join(fix_mojibake(text).split())
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--extract", default=str(OUT / "wis_extract"))
    ap.add_argument("--applic", default=str(OUT / "wis_applic2"))
    ap.add_argument("--applic1", default=str(OUT / "wis_applic"),
                    help="W_Baumusterzeile + W_Dok2Reihe from pull_applic.js")
    ap.add_argument("--rebuild-join", action="store_true",
                    help="rebuild doc_applicability.tsv from the pulled tables")
    args = ap.parse_args(argv)
    ext, app = Path(args.extract), Path(args.applic)
    src = ext / "records.jsonl"
    keep = ext / "records.v1.1.jsonl"
    tmp = ext / "records.jsonl.tmp"
    manifest_path = ext / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") == "1.2":
        print("already schema 1.2; rewriting from records.v1.1.jsonl")
        src = keep
    if not src.is_file():
        print(f"missing {src}", file=sys.stderr)
        return 1

    if args.rebuild_join or not (app / "doc_applicability.tsv").is_file():
        build_doc_applicability(app, Path(args.applic1))
    applic = load_applic(app / "doc_applicability.tsv")
    print(f"doc_applicability: {len(applic)} DokNrIDs")

    wanted = set()
    with src.open(encoding="utf-8") as f:
        for line in f:
            kid = json.loads(line).get("kurzgueltigkeit_id")
            if isinstance(kid, int):
                wanted.add(kid)
    summaries = load_summaries(app / "W_Kurzgueltigkeit.tsv", wanted)
    print(f"W_Kurzgueltigkeit: {len(summaries)} of {len(wanted)} ids used by records")

    n = mapped = with_engine = with_trans = with_summary = 0
    series_seen: set[str] = set()
    with src.open(encoding="utf-8") as f, tmp.open("w", encoding="utf-8", newline="\n") as w:
        for line in f:
            rec = json.loads(line)
            n += 1
            row = applic.get(rec.get("dok_nr_id")) or {}
            veh = sorted(set(split(row.get("vehicle_series", ""))))
            eng = sorted(set(split(row.get("engine_series", ""))))
            trn = sorted(set(split(row.get("transmission_series", ""))))
            rec["model_series"] = veh
            rec["baumuster"] = specific_bm(row.get("vehicle_baumuster", ""))
            rec["engine_series"] = eng
            rec["transmission_series"] = trn
            rec["model_year_from"] = None
            rec["model_year_to"] = None
            rec["applies_all_series"] = bool(veh) and not rec["baumuster"]
            summary = summaries.get(rec.get("kurzgueltigkeit_id"), "")
            rec["applicability_summary"] = summary
            mapped += bool(veh)
            with_engine += bool(eng)
            with_trans += bool(trn)
            with_summary += bool(summary)
            series_seen.update(veh)
            w.write(json.dumps(rec, ensure_ascii=False) + "\n")

    if n != manifest.get("record_count"):
        tmp.unlink()
        print(f"record count {n} != manifest {manifest.get('record_count')}; aborted",
              file=sys.stderr)
        return 1
    if src != keep:
        os.replace(src, keep)
    os.replace(tmp, ext / "records.jsonl")

    manifest["schema_version"] = "1.2"
    manifest["records_bytes"] = (ext / "records.jsonl").stat().st_size
    manifest["applicability"] = {
        **manifest.get("applicability", {}),
        "join": "W_Dok2Reihe -> WS1-3/WC1-8 (role by table) -> W_Baumusterzeile",
        "records_with_models": mapped,
        "records_with_engines": with_engine,
        "records_with_transmissions": with_trans,
        "records_with_summary": with_summary,
        "model_series_count": len(series_seen),
    }
    manifest_path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False),
                             encoding="utf-8")
    print(f"{n} records: {mapped} with a vehicle series, {with_engine} with an engine, "
          f"{with_trans} with a transmission, {with_summary} with a validity summary")
    print(f"previous file kept as {keep.name}; manifest -> schema 1.2")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
