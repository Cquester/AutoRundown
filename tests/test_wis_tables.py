"""Torque specs / Capacities: the job's values, this car's column -- FREE, ~5 s.

    python tests/test_wis_tables.py

Checks autorundown/wis_tables.py and tab_values.py (2026-10-16): on the live
W210 index the tabs showed "Oil level sensor ... 20 Nm" (the 20 Nm was the
screw plug's -- a flattened table row), every engine's column side by side,
and transmission values for an oil level sensor. Now:

- a WIS value table is read from its SIDS layout, a value paired with its
  own row and column; only columns whose label fits the chosen car stay
  (English and German labels, WIS's "112.910/911/ 912" shorthand, "except",
  conditions such as a code kept as a condition); a dash is never a value;
  two columns' labels in one header cell are split;
- /api/values gives the values of the job's pages *and* of the procedures
  they link to (the oil pan bottom's bolts for an oil level sensor), never
  another model's document;
- /api/fluids gives the car's fluids from its capacity sheets, with the
  engine oil's specification sheets;
- the text fallback (other manuals) keeps a value with its own label.

The SIDS bodies below are invented, in WIS's format: nothing from WIS is in
this repo.
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo on sys.path)
import gzip
import json
import os
import sqlite3
import struct
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_wistables_"))
os.environ["AUTORUNDOWN_HOME"] = str(TMP / "home")  # before importing autorundown

from fastapi.testclient import TestClient  # noqa: E402

import ingest  # noqa: E402
import test_records  # noqa: E402
from autorundown import index, search, server, wis_links, wis_tables  # noqa: E402

fails = 0


def check(label, ok, got=None):
    global fails
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else f" -- got {got!r}"))
    fails += not ok


# -- invented SIDS ---------------------------------------------------------------

def sids(blocks) -> str:
    """blocks: ("p", text) | ("row", [cell, ...])  ->  a SIDS body."""
    recs = ['ASCII-SIDS2.00,"16.10.2026","test"', "V"]
    for kind, val in blocks:
        if kind == "p":
            recs += ["P", f'0,10,24,,,"{val}"']
        else:
            for i, cell in enumerate(val):
                recs += [f"PC,{'T' if i == 0 else ''},{(i + 1) * 2000}", f'T"{cell}"']
            recs.append("P")
    recs.append("W")
    return ";\n".join(recs) + ";\n"


SENSOR = sids([
    ("p", "▸ Oil level sensor"),
    ("row", ["Number", "Designation", "", "ENGINE 102.983", "ENGINE 103.942"]),
    ("row", ["BA18.40-P-1001-01B", "Screw plug on crankcase", "Nm", "–", "25"]),
    ("row", ["BA18.40-P-1002-01B", "Oil level sensor to oil pan", "Nm", "9", "12"]),
    ("row", ["BA18.40-P-1003-01B", "Self-tapping bolt, sensor", "Thread pre-tapped Nm", "8", "8"]),
    ("row", ["", "", "Thread not pre-tapped Nm", "11", "11"]),
    ("p", "▸ Engine lubrication"),
    ("row", ["Number", "Designation / MOTOR 102.983 im TYP 201", "MOTOR 103.942 im TYP 201"]),
    ("row", ["BF18.00-P-1001-01X", "Engine oil", "Filling capacity with oil filter", "Liters", "5.0", "6.0"]),
    ("row", ["", "", "Specifications for Operating Fluids", "Sheet", "BB00.40-P-0229-01A", "-"]),
])
PAN = sids([
    ("p", "▸ Oil pan"),
    # two columns' labels in one header cell, their values in one cell too
    ("row", ["Number", "Designation Engine 102.983/985/ 988 Engine 103.940/942", "Engine 104.945"]),
    ("row", ["BA01.45-P-1002-01B", "Bolt, oil pan bottom to top", "M6", "Nm", "11 13", "-"]),
    ("row", ["BA01.45-P-1009-01B", "Bolt, oil pan to crankcase", "Stage 1 Nm", "10 10", "10"]),
    ("row", ["", "", "Stage 2 ( °", "90 90", "90"]),
])
DIFF = sids([
    ("p", "▸ Rear axle differential"),
    ("row", ["Number", "Designation", "", "TYP 201.024 außer CODE 450",
             "MODEL 201.034 with CODE 450 (Taxi version), MODEL 201.029"]),
    ("row", ["BF35.31-P-1001-01G", "Filling capacity", "Rear axle differential Liter", "0,9", "1,1"]),
])
# a model this car is not: never a value for it
OTHER = sids([
    ("row", ["Number", "Designation", "", "MODEL 124"]),
    ("row", ["BF35.31-P-1001-01B", "Filling capacity", "Rear axle differential Liter", "1,3"]),
])

EXTRA = [
    test_records.rec(1002310, "Remove/install oil level sensor",
                     "Remove lower part of oil pan.\nUnplug the sensor.\nNm 20 – Oil level sensor to oil pan\n"
                     "Refer to: AR01.45-P-7555V, AS00.00-Z-0010-01A, BB00.40-P-0229-01A",
                     series=["201"], baumuster=["201.034"], yr=("1983-10-01", "2000-04-30"),
                     xrefs=["AR01.45-P-7555V", "AS00.00-Z-0010-01A", "BB00.40-P-0229-01A"]),
    test_records.rec(1002311, "Remove/install bottom part of oil pan",
                     "Drain the engine oil. Remove the oil pan bottom section.",
                     series=["201"], baumuster=["201.034"], yr=("1983-10-01", "2000-04-30")),
    test_records.rec(1002312, "Differential", "Filling capacity rear axle differential.",
                     series=["201"], yr=("1983-10-01", "2000-04-30")),
    test_records.rec(1002313, "Multigrade engine oils (specification 229.1)",
                     "Engine oils approved to sheet 229.1."),
    test_records.rec(1002314, "Differential", "Filling capacity rear axle differential.",
                     series=["176"], yr=("2012-06-01", "2018-05-31")),
]
BODIES = {1002310: SENSOR, 1002311: PAN, 1002312: DIFF, 1002314: OTHER}
DOCNRS = [("ar0145p7555v", 1002311), ("bf3531p100001g", 1002312),
          ("bb0040p022901a", 1002313), ("as0000z001001a", 1002299),
          ("bf3531p100001b", 1002314), ("ar1840p1000a", 1002310)]


def write_pack(cdir: Path) -> None:
    with (cdir / "bodies.pack").open("wb") as f:
        for dok, text in BODIES.items():
            gz = gzip.compress(text.encode("utf-8"))
            f.write(struct.pack(">ii", dok, len(gz)) + gz)


CODES = {"engines": {"102.983"}, "models": {"201.034"}, "series": {"201"},
         "families": {"102"}}


def main() -> int:
    print("-- which column fits the car --")
    fits = wis_tables.label_fits
    check("an engine family fits its exact engine", fits("ENGINE 102", CODES) == "yes")
    check("... unless it is excepted", fits("ENGINE 102 (except 102.982/983)", CODES) == "no")
    check("WIS shorthand: 102.910/983 lists 102.983", fits("ENGINE 102.910/983", CODES) == "yes")
    check("... and 102.910/911 does not", fits("ENGINE 102.910/911", CODES) == "no")
    check("another engine is not ours", fits("ENGINE 103.942", CODES) == "no")
    check("German: MOTOR ... im TYP", fits("MOTOR 102.983 im TYP 201", CODES) == "yes")
    check("German: wrong model", fits("MOTOR 102.983 im TYP 124", CODES) == "no")
    check("a two-digit model prefix (201.03 = 201.03x)", fits("MODEL 201.010/03", CODES) == "yes")
    check("a code we can't know is a condition", fits("MODEL 201.034 with CODE 450", CODES) == "maybe")
    check("alternatives: one fitting is enough",
          fits("MODEL 201.029, MODEL 201.034", CODES) == "yes")
    check("a label naming nothing is not judged", fits("shortened maintenance interval", CODES) is None)
    check("no car chosen: nothing is judged", fits("ENGINE 103", None) is None)

    print("-- WIS tables: the car's column, its own row --")
    cdir = test_records.build_corpus(TMP / "archive")
    with (cdir / "records.jsonl").open("a", encoding="utf-8", newline="\n") as f:
        for r in EXTRA:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    man = json.loads((cdir / "manifest.json").read_text(encoding="utf-8"))
    man["record_count"] += len(EXTRA)
    (cdir / "manifest.json").write_text(json.dumps(man, indent=2), encoding="utf-8")
    write_pack(cdir)
    rows = wis_tables.rows(cdir, 1002310, "torque", CODES)
    got = {r["name"]: r["value"] for r in rows}
    check("each value with its own part (never the next row's)",
          got.get("Oil level sensor to oil pan") == "9 Nm", rows)
    check("a dash in our column is no value", "Screw plug on crankcase" not in got, rows)
    check("sub-rows carry the part's name",
          got.get("Self-tapping bolt, sensor · Thread not pre-tapped") == "11 Nm", rows)
    check("only our column (no 12 Nm)", "12 Nm" not in got.values(), rows)
    cap = wis_tables.rows(cdir, 1002310, "capacity", CODES,
                          sheet_title=lambda d: "MB 229.1" if d.startswith("BB00.40-P-0229-01") else "")
    check("capacity: our engine's figure and its oil sheet",
          [(r["value"], r.get("spec")) for r in cap] == [("5.0 Liters", "MB 229.1")], cap)
    pan = {r["name"]: r["value"] for r in wis_tables.rows(cdir, 1002311, "torque", CODES)}
    check("two labels in one header cell: one value each",
          pan.get("Bolt, oil pan bottom to top · M6") == "11 Nm", pan)
    check("a turn is its own stage row",
          pan.get("Bolt, oil pan to crankcase · Stage 2") == "90°"
          and pan.get("Bolt, oil pan to crankcase · Stage 1") == "10 Nm", pan)
    diff = wis_tables.rows(cdir, 1002312, "capacity", CODES)
    check("a column under a condition is kept, with the condition",
          len(diff) == 1 and diff[0]["value"] == "1,1 Liter" and "CODE 450" in diff[0]["condition"], diff)
    allcols = wis_tables.rows(cdir, 1002310, "torque", None)
    check("no car chosen: every column, labelled",
          any("ENGINE 103.942" in r["value"] for r in allcols), allcols)

    print("-- text fallback keeps a value with its own label --")
    mem = sqlite3.connect(":memory:")
    mem.execute("CREATE TABLE pages (page_uid TEXT, title TEXT, text TEXT)")
    mem.execute("INSERT INTO pages VALUES ('x', 't', ?)", (
        "Screw plug on crankcase\nNm 20 – Oil level sensor on oil pan top part\nNm 10 –",))
    lines = search.value_lines(mem, "x", "torque", "")
    rows = [search.value_row(l, "torque") for l in lines]
    check("Nm 20 belongs to the line before it",
          any(r["name"].startswith("Screw plug") and "20" in r["value"] for r in rows)
          and not any(r["name"].startswith("Oil level sensor") and "20" in r["value"] for r in rows), rows)
    vr = search.value_row
    check("a stage number is not a torque", vr("Stage 1 Nm 20", "torque")["value"] == "Nm 20")
    check("value then unit: each column its own unit",
          vr("1982-85 190E 5.5 Liters 5.8 Quarts", "capacity")["value"] == "5.5 Liters / 5.8 Quarts")
    check("turns right after the torque stay with it",
          vr("Cylinder head bolt 55 Nm 90° 90°", "torque")["value"] == "55 Nm 90° 90°")

    print("-- the endpoints --")
    idx = TMP / "index.db"
    with open(os.devnull, "w") as quiet:
        out, sys.stdout = sys.stdout, quiet
        try:
            rc = ingest.main(["--archive", str(TMP / "archive"), "--index", str(idx)])
        finally:
            sys.stdout = out
    check("ingest", rc == 0, rc)
    with sqlite3.connect(cdir / wis_links.NAME) as c:
        c.execute("CREATE TABLE docnr(docnr TEXT, dok_nr_id INTEGER, dok_id INTEGER)")
        c.executemany("INSERT INTO docnr VALUES (?,?,?)", [(n, i, d) for i, (n, d) in enumerate(DOCNRS)])
    wis_links._open.clear()
    conn = index.connect(idx)
    vs = index.vehicles(conn)
    conn.close()
    w201 = next(v for v in vs if v["doc_id"].endswith("-201"))
    var = next(i for i, x in enumerate(json.loads(w201["variants"])) if x["name"] == "190 E 2.3 16")
    app = TestClient(server.create_app(str(idx), TMP / "auth.db"),
                     base_url="http://localhost:8756", client=("127.0.0.1", 50000))
    doc = w201["doc_id"]
    r = app.get(f"/api/values/{doc}", params={"kind": "torque", "pages": ["1002310"], "variant": var})
    vals = r.json()
    names = {x["name"]: x for x in vals}
    check("the job's page: its values", names.get("Oil level sensor to oil pan", {}).get("value") == "9 Nm", vals)
    check("and the linked procedure's (the oil pan bottom)",
          names.get("Bolt, oil pan bottom to top · M6", {}).get("via") == "1002310", vals)
    check("nothing from another engine's column", all("12 Nm" != x["value"] for x in vals), vals)
    c = app.get(f"/api/values/{doc}", params={"kind": "capacity", "pages": ["1002310"], "variant": var}).json()
    check("capacity: engine oil with its oil sheet",
          any(x["value"] == "5.0 Liters" and x.get("spec") == "MB 229.1" for x in c), c)
    check("no pages: nothing",
          app.get(f"/api/values/{doc}", params={"kind": "torque"}).json() == [])
    check("an unknown kind is refused",
          app.get(f"/api/values/{doc}", params={"kind": "labour", "pages": ["1"]}).status_code == 422)
    fl = app.get(f"/api/fluids/{doc}", params={"variant": var}).json()
    check("fluids: the car's capacity sheets", any(x["value"] == "1,1 Liter" for x in fl), fl)
    check("fluids: never another model's sheet", not any("1,3" in x["value"] for x in fl), fl)
    check("fluids: nothing until a model or engine is chosen",
          app.get(f"/api/fluids/{doc}").json() == [])
    print(f"\n{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
