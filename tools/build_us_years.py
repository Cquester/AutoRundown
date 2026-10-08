"""Build importers/data/mercedes_us_years.csv from NHTSA vPIC -- run once, by hand.

    python tools/build_us_years.py [--vin2fin <path>] [--limit N]

Which US model years each Mercedes model (Baumuster) was sold in. Mercedes'
own data has no per-model years (CLAUDE.md, Vehicle selection); NHTSA's public
VIN decoder (vPIC) does, indirectly: a US VIN pattern only decodes for the
model years it was registered for -- the 2008-09 C 300 4MATIC pattern
WDDGF81X fails for 2010, when Mercedes changed the coding.

So: for each distinct VIN pattern in Mercedes' AW_VIN2FIN table (first 8
characters; vPIC ignores the check digit), decode a synthetic VIN for each
plausible model year and keep the years vPIC recognises. Synthetic means the
pattern plus a year letter, plant "A" and serial 123456 -- no real car's VIN.

The output is committed to the repo and the site never calls NHTSA: if vPIC
changes or disappears, nothing breaks; the file just can't be refreshed (and
these cars are all 2016 or older). Polite: batches of 50, ~1 request/s,
cached per pattern so a stopped run resumes.
"""
from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "autorundown" / "importers" / "data"
OUT = DATA / "mercedes_us_years.csv"
CACHE = ROOT / "tools" / ".us_years_cache.json"
URL = "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVINValuesBatch/"
YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"   # position 10 cycle from 1980 (A) ...


def year_code(y: int) -> str:
    # 1980-2000: A..Y; 2001-2009: 1..9; 2010-2030: A..Y again
    if 2001 <= y <= 2009:
        return str(y - 2000)
    base = 1980 if y < 2010 else 2010
    return "ABCDEFGHJKLMNPRSTVWXY"[y - base]


def plausible_years(series: str, spans: dict[str, tuple[int, int]]) -> range:
    a, b = spans.get(series, (1981, 2017))
    return range(max(1981, a - 1), min(2017, b + 1) + 1)


def decode(vins: list[str]) -> list[dict]:
    body = urllib.parse.urlencode({"format": "json", "data": ";".join(vins)}).encode()
    req = urllib.request.Request(URL, data=body, method="POST",
                                 headers={"User-Agent": "AutoRundown (one-time year table build)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)["Results"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vin2fin", default=str(ROOT.parent / "Mercedes Documents" / "autorundown_out"
                                             / "wis_extract" / "vin2fin.json"))
    ap.add_argument("--limit", type=int, default=0, help="only the first N patterns (testing)")
    args = ap.parse_args(argv)

    v2f = json.loads(Path(args.vin2fin).read_text(encoding="utf-8"))
    spans = {}
    with (DATA / "mercedes_years.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            spans[r["series"]] = (int(r["year_from"]), int(r["year_to"] or 2017))

    # VIN8 pattern -> the Baumuster it can mean (check-digit variants may differ)
    patterns: dict[str, set[str]] = defaultdict(set)
    for vin9, bm in v2f.items():
        patterns[vin9[:8]].add(bm)
    todo = sorted(patterns)[: args.limit or None]

    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.is_file() else {}
    queue = [(p, y) for p in todo for y in plausible_years(min(patterns[p])[:3], spans)
             if f"{p}|{y}" not in cache]
    print(f"{len(todo)} patterns, {len(queue)} lookups to do ({len(cache)} cached)", flush=True)
    for i in range(0, len(queue), 50):
        chunk = queue[i:i + 50]
        vins = [f"{p}0{year_code(y)}A123456" for p, y in chunk]   # check digit position: dummy 0
        for attempt in range(4):
            try:
                results = decode(vins)
                break
            except Exception as exc:
                print(f"  retry after {exc}", flush=True)
                time.sleep(5 * (attempt + 1))
        else:
            print("vPIC unreachable; run again to resume from the cache")
            break
        for (p, y), r in zip(chunk, results):
            ok = bool((r.get("Model") or "").strip()) and str(r.get("ModelYear")) == str(y)
            cache[f"{p}|{y}"] = [ok, (r.get("Trim") or r.get("Series") or "").strip(),
                                 (r.get("Model") or "").strip()]
        CACHE.write_text(json.dumps(cache), encoding="utf-8")
        if (i // 50) % 20 == 0:
            print(f"  {min(i + 50, len(queue))}/{len(queue)}", flush=True)
        time.sleep(1)

    # aggregate per Baumuster
    years: dict[str, set[int]] = defaultdict(set)
    trims: dict[str, set[str]] = defaultdict(set)
    for p in todo:
        series = min(patterns[p])[:3]
        # clip to the generation's span (+1 at the end: a US model year runs
        # ahead of production): vPIC accepts the 4JGAB54E ML320 pattern back
        # to 1996, two years before the ML existed
        lo, hi = spans.get(series, (1900, 2100))
        for y in plausible_years(series, spans):
            hit = cache.get(f"{p}|{y}")
            # a year counts only if vPIC named a trim: 4JGAB57E decodes 1996-2000
            # as a bare "M-Class" (a generic match), ML350 only from 2003
            if hit and hit[0] and hit[1] and lo <= y <= hi + 1:
                for bm in patterns[p]:
                    years[bm].add(y)
                    if hit[1]:
                        trims[bm].add(hit[1])
    with OUT.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["baumuster", "us_years", "us_trims"])
        for bm in sorted(years):
            w.writerow([bm, compact(sorted(years[bm])), "; ".join(sorted(trims[bm]))])
    print(f"{len(years)} Baumuster with US years -> {OUT} ({OUT.stat().st_size // 1024} KB)")
    return 0


def compact(ys: list[int]) -> str:
    """[2008, 2009, 2011] -> "2008-2009,2011"."""
    out, start, prev = [], None, None
    for y in ys:
        if start is None:
            start = prev = y
        elif y == prev + 1:
            prev = y
        else:
            out.append(f"{start}-{prev}" if prev != start else str(start))
            start = prev = y
    if start is not None:
        out.append(f"{start}-{prev}" if prev != start else str(start))
    return ",".join(out)


if __name__ == "__main__":
    raise SystemExit(main())
