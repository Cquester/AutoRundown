"""AutoRundown extractor - LINK parse: pulled reference tables -> wis_links.sqlite.

Offline. Reads what pull_vehicles.js (docnrs, SA codes, oil capacities) and
pull_links.js (wiring-diagram component codes) wrote, and writes one SQLite
sidecar into the corpus dir. The importer reads `docnr` to turn the
cross-references printed on pages into links; the server reads the rest.

Tables written (all English, as pulled):
  docnr(docnr, dok_nr_id, dok_id)   "ah0320n100002a" -> W_Dokumente.DokNrID
                               -> the corpus record's DokID (= page_no); only
                               documents the corpus has. Pages print a
                               reference without its last variant letters
                               ("AR15.40-P-5710E" = ...5710ea/eb/ecm, one per
                               model), so the server matches by prefix and
                               picks the variant in the chosen car's scope.
  diagram(dok_id, max_x, max_y)          DXF drawing extent
  diagram_code(dok_id, code, x, y)       component label positions; drawing
                               units, Y up from the bottom edge
  diagram_name(dok_id, code, name)       "N3/10" -> "ME control unit"
  diagram_box(dok_id, x, y, w, h, docnr, code)   clickable boxes -> docnr
  diagram_svgref(dok_id, link_type, code, docnr)
  sa_code(vehicle_type, code, name)      option (SA) codes
  sa_number(number, name)
  oil(series, engine, engine_type, litres, unit, note)   engine oil capacity

Rows a resumed pull_links.js batch wrote twice are dropped (set semantics).
The labour-time (ASRA_*) and damage-code (SS*) tables are pulled but not
parsed yet: how they join to our vehicles is not established.

Config via environment:
  AR_VEH    pulled vehicle/reference TSVs  (default <out root>/wis_vehicles)
  AR_LINKS  pulled diagram-link TSVs       (default <out root>/wis_links)
  AR_OUT    corpus dir to write into       (default <out root>/wis_extract)
"""
import csv
import json
import os
import re
import sqlite3
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from wis_paths import out_root  # noqa: E402
from parse_vehicles import fix_mojibake  # noqa: E402

VEH = os.environ.get("AR_VEH") or os.path.join(out_root(), "wis_vehicles")
LINKS = os.environ.get("AR_LINKS") or os.path.join(out_root(), "wis_links")
OUT = os.environ.get("AR_OUT") or os.path.join(out_root(), "wis_extract")
NAME = "wis_links.sqlite"

csv.field_size_limit(10_000_000)


def norm_docnr(s: str) -> str:
    """'AH03.20-N-1000-02A' and 'ah0320n100002a' -> 'ah0320n100002a'."""
    return re.sub(r"[^0-9a-z]", "", (s or "").lower())


def tsv(folder, name):
    """Rows of a pulled table as dicts; [] if it was never pulled. A repeated
    header line (a resumed batch re-wrote it) is skipped."""
    path = os.path.join(folder, name + ".tsv")
    if not os.path.isfile(path):
        print(f"  - {name}: not pulled")
        return []
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        r = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        head = next(r, None)
        if not head:
            return []
        out = [dict(zip(head, row)) for row in r if row and row != head]
    print(f"  {name}: {len(out):,} rows")
    return out


def num(s):
    try:
        return int(s)
    except (TypeError, ValueError):
        return None


def corpus_docs():
    """{DokNrID: [DokID]} for every record in this corpus, from metadata.tsv."""
    ids = {}
    path = os.path.join(OUT, "metadata.tsv")
    with open(path, encoding="utf-8", errors="replace") as f:
        head = f.readline().rstrip("\n").split("\t")
        kd, kn = head.index("DokID"), head.index("DokNrID")
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) > max(kd, kn) and p[kd].isdigit() and p[kn].isdigit():
                ids.setdefault(int(p[kn]), []).append(int(p[kd]))
    return ids


def main():
    tmp = os.path.join(OUT, NAME + ".tmp")
    if os.path.exists(tmp):
        os.remove(tmp)
    c = sqlite3.connect(tmp)
    c.executescript("""
    CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT);
    CREATE TABLE docnr(docnr TEXT, dok_nr_id INTEGER, dok_id INTEGER,
                       PRIMARY KEY (docnr, dok_id));
    CREATE TABLE diagram(dok_id INTEGER PRIMARY KEY, max_x INTEGER, max_y INTEGER);
    CREATE TABLE diagram_code(dok_id INTEGER, code TEXT, x INTEGER, y INTEGER,
                              PRIMARY KEY (dok_id, code, x, y));
    CREATE TABLE diagram_name(dok_id INTEGER, code TEXT, name TEXT, PRIMARY KEY (dok_id, code));
    CREATE TABLE diagram_box(dok_id INTEGER, x INTEGER, y INTEGER, w INTEGER, h INTEGER,
                             docnr TEXT, code TEXT, PRIMARY KEY (dok_id, x, y, docnr));
    CREATE TABLE diagram_svgref(dok_id INTEGER, link_type INTEGER, code TEXT, docnr TEXT,
                                PRIMARY KEY (dok_id, code, docnr));
    CREATE TABLE sa_code(vehicle_type INTEGER, code TEXT, name TEXT, PRIMARY KEY (vehicle_type, code));
    CREATE TABLE sa_number(number TEXT PRIMARY KEY, name TEXT);
    CREATE TABLE oil(series TEXT, engine TEXT, engine_type INTEGER, litres REAL,
                     unit TEXT, note TEXT);
    CREATE INDEX docnr_id ON docnr(dok_nr_id);
    CREATE INDEX sa_code_code ON sa_code(code);
    CREATE INDEX oil_engine ON oil(engine);
    """)
    counts = {}

    print("docnrs")
    have = corpus_docs()
    rows = {(norm_docnr(r["DokNr"]), int(r["DokNrID"]), dok)
            for r in tsv(VEH, "W_Dokumente")
            if r.get("DokNrID", "").isdigit() and norm_docnr(r.get("DokNr"))
            for dok in have.get(int(r["DokNrID"]), ())}
    c.executemany("INSERT INTO docnr VALUES (?,?,?)", sorted(rows))
    counts["docnr"] = len(rows)

    print("wiring diagrams")
    def ins(table, sql_cols, items):
        items = sorted(set(items), key=repr)
        c.executemany(f"INSERT OR IGNORE INTO {table} VALUES ({','.join('?' * sql_cols)})", items)
        counts[table] = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    ins("diagram", 3, ((num(r["DokID"]), num(r["MaxXpos"]), num(r["MaxYpos"]))
                       for r in tsv(LINKS, "DXF") if num(r.get("DokID"))))
    ins("diagram_code", 4, ((num(r["DokID"]), r["Kurzbezeichnung"], num(r["Xpos"]), num(r["Ypos"]))
                            for r in tsv(LINKS, "DXFLegendePos")
                            if num(r.get("DokID")) and r.get("Kurzbezeichnung")))
    ins("diagram_name", 3, ((num(r["DokID"]), r["Kurzbezeichnung"], fix_mojibake(r["Langbezeichnung"]))
                            for r in tsv(LINKS, "DXFLegende")
                            if num(r.get("DokID")) and r.get("Kurzbezeichnung")))
    ins("diagram_box", 7, ((num(r["DokID"]), num(r["XPos"]), num(r["YPos"]), num(r["XWidth"]),
                            num(r["YHeight"]), norm_docnr(r["DokNrZu"]), r["CompName"])
                           for r in tsv(LINKS, "AW_DXFReferenz") if num(r.get("DokID"))))
    ins("diagram_svgref", 4, ((num(r["DokID"]), num(r["LinkType"]), r["Kurzbezeichnung"],
                               norm_docnr(r["Link"]))
                              for r in tsv(LINKS, "AW_SVGReferenz") if num(r.get("DokID"))))

    print("option codes")
    ins("sa_code", 3, ((num(r["FzgArtCode"]), r["SACode"].strip(), fix_mojibake(r["SACodeName"]).strip())
                       for r in tsv(VEH, "SACode") if r.get("SACode", "").strip()))
    ins("sa_number", 2, ((r["SANummer"].strip(), fix_mojibake(r["SANummerName"]).strip())
                         for r in tsv(VEH, "SANummer") if r.get("SANummer", "").strip()))

    print("oil capacities")
    notes = {r["FussnoteID"]: fix_mojibake(r["FussnoteText"]) for r in tsv(VEH, "AW_AssystFussnote")}
    oil = []
    for r in tsv(VEH, "AW_AssystFuellmengen"):
        try:
            litres = float(r["Oelmenge"].replace(",", "."))
        except (KeyError, ValueError):
            continue
        note = fix_mojibake(r.get("Bemerkungen") or "").strip()
        oil.append((r["TypBM"].strip(), r["MotorBM"].strip(), num(r.get("MotorTyp")),
                    litres, r.get("Einheit", "").strip(), notes.get(note, note)))
    c.executemany("INSERT INTO oil VALUES (?,?,?,?,?,?)", sorted(set(oil)))
    counts["oil"] = len(set(oil))

    c.execute("INSERT INTO meta VALUES ('counts', ?)", (json.dumps(counts),))
    c.commit()
    c.close()
    os.replace(tmp, os.path.join(OUT, NAME))
    print("-> " + os.path.join(OUT, NAME))
    for k, v in counts.items():
        print(f"  {k:15} {v:>10,}")


if __name__ == "__main__":
    main()
