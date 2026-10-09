"""Build the LEMON catalogue snapshot the "LEMON catalogue" import source
falls back on when no torrent index.json is on this PC.

    python tools/build_lemon_catalogue.py <path to lemon/index.json>

Input: `lemon/index.json` from the LEMON torrent (143 MB, 279,988 vehicles).
Output: autorundown/importers/data/lemon_catalogue.json.gz, about 2.5 MB:

    {"source": ..., "built": "YYYY-MM-DD", "vehicles": N,
     "makes": {make: {year: [variant, ...]}}}

where `variant` is the last part of the vehicle's uriPath, unquoted -- the
name LEMON's own breadcrumbs (and so our `lemon` importer) give the car.
The model/engine split is kept as "model\\tengine" when the two differ from
a plain join, which never happens today (the script checks and says so).
Run by hand; the site never needs network access for the catalogue.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import sys
from pathlib import Path
from urllib.parse import unquote

OUT = Path(__file__).resolve().parent.parent / "autorundown" / "importers" / "data" / "lemon_catalogue.json.gz"


def variant_of(v: dict) -> str:
    parts = [p for p in v["uriPath"].split("/") if p]
    if len(parts) != 3:
        raise ValueError(f"unexpected uriPath {v['uriPath']!r}")
    return unquote(parts[2])


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    src = Path(sys.argv[1])
    data = json.loads(src.read_text(encoding="utf-8"))
    if data.get("database") != "lemon":
        print(f"not a LEMON index.json (database={data.get('database')!r})")
        return 1
    makes: dict[str, dict[str, dict[str, list[str]]]] = {}
    odd = []
    for v in data["vehicles"]:
        engine = v.get("engine") or ""
        if " ".join(x for x in (v["model"], engine) if x) != variant_of(v):
            odd.append(v["uriPath"])
        for year in v["years"]:
            makes.setdefault(v["make"], {}).setdefault(str(year), {}).setdefault(
                v["model"], []).append(engine)
    if odd:
        print(f"{len(odd)} vehicle name(s) are not 'model engine', e.g. {odd[0]}; not written")
        return 1
    for years in makes.values():
        for y, models in years.items():
            years[y] = {m: sorted(e, key=str.lower) for m, e in sorted(models.items(), key=lambda kv: kv[0].lower())}
    out = {"source": "LEMON torrent lemon/index.json", "built": dt.date.today().isoformat(),
           "vehicles": len(data["vehicles"]),
           "makes": {m: dict(sorted(y.items())) for m, y in sorted(makes.items(), key=lambda kv: kv[0].lower())}}
    OUT.write_bytes(gzip.compress(json.dumps(out, separators=(",", ":"), ensure_ascii=False).encode("utf-8"), 9))
    print(f"{len(data['vehicles'])} vehicles, {len(makes)} makes -> {OUT} "
          f"({OUT.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
