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
  labour_car(baumuster, engine, sparte, pl, family, type)   which labour-time
                               book(s) a car is in (ASRA_Fahrzeug); one
                               Baumuster can be in several (by chassis range:
                               124.026 is in "124 (up to 09/92)" and the later
                               book), so the server lists them, never guesses
  labour_book(sparte, pl, family, type, family_name, type_name)
  labour_group(kg, name), labour_subgroup(sparte, kg, entry, name)
  labour_op(sparte, kg, op, entry, category, text)   English operation texts
  labour_time(sparte, pl, family, kg, op, typenart, raw)   raw times;
                               typenart is the type code, its group ("40"
                               = 4x) or "00" (all) -- never overlapping
  labour_formula(region, sparte, pl, family, kg, category, expr)   the
                               flat rate is expr applied to the raw time, in
                               minutes; checked against LEMON's warranty
                               hours for the 190E 2.3-16 (five jobs, all
                               within 0.1 h). Category Z (extra work) has none.

Rows a resumed pull_links.js batch wrote twice are dropped (set semantics).
The damage-code (SS*) tables are pulled but not parsed yet.

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

    labour(c, counts)

    c.execute("INSERT INTO meta VALUES ('counts', ?)", (json.dumps(counts),))
    c.commit()
    c.close()
    replace(tmp, os.path.join(OUT, NAME))
    print("-> " + os.path.join(OUT, NAME))
    for k, v in counts.items():
        print(f"  {k:15} {v:>10,}")


def replace(tmp: str, dest: str) -> None:
    """Put the new sidecar in place. On Windows a program that has the old
    one open (an AutoRundown left running from before an update, 2026-10-23)
    blocks this: wait a little, then say what to do rather than a traceback."""
    import time
    for i in range(10):
        try:
            os.replace(tmp, dest)
            return
        except PermissionError:
            time.sleep(1 + i)
    sys.exit(f"STOP: {dest} is open in another program, so it can't be replaced. "
             "Quit AutoRundown (tray icon > Quit) and any other copy of it that is "
             "still running (Task Manager: pythonw.exe), then run the import again; "
             f"the new file is kept at {tmp}.")


def dotted(code: str) -> str:
    """'201034' -> '201.034', the form variants.jsonl uses."""
    code = (code or "").strip()
    return f"{code[:3]}.{code[3:]}" if len(code) == 6 and code.isdigit() else code


def labour(c, counts):
    """ASRA labour times (flat rates) -- see the module docstring."""
    print("labour times")
    lang = "02"                          # ASRA writes English as "02"
    c.executescript("""
    CREATE TABLE labour_car(baumuster TEXT, engine TEXT, sparte TEXT, pl TEXT, family TEXT, type TEXT,
                            PRIMARY KEY (baumuster, engine, sparte, pl, family, type));
    CREATE TABLE labour_book(sparte TEXT, pl TEXT, family TEXT, type TEXT, family_name TEXT,
                             type_name TEXT, PRIMARY KEY (sparte, pl, family, type));
    CREATE TABLE labour_group(kg TEXT PRIMARY KEY, name TEXT);
    CREATE TABLE labour_subgroup(sparte TEXT, kg TEXT, entry TEXT, name TEXT,
                                 PRIMARY KEY (sparte, kg, entry));
    CREATE TABLE labour_op(sparte TEXT, kg TEXT, op TEXT, entry TEXT, category TEXT, text TEXT,
                           PRIMARY KEY (sparte, kg, op));
    CREATE TABLE labour_time(sparte TEXT, pl TEXT, family TEXT, kg TEXT, op TEXT, typenart TEXT,
                             raw INTEGER, PRIMARY KEY (sparte, pl, family, kg, op, typenart));
    CREATE TABLE labour_formula(region TEXT, sparte TEXT, pl TEXT, family TEXT, kg TEXT,
                                category TEXT, expr TEXT,
                                PRIMARY KEY (region, sparte, pl, family, kg, category));
    CREATE INDEX labour_car_bm ON labour_car(baumuster);
    """)
    cars = {(dotted(r["TypBM"]), dotted(r["MotorBM"]), r["SparteAbk"], r["ProduktionslandAbk"],
             r["FamilieAbk"], r["TypkennzahlAbk"])
            for r in tsv(VEH, "ASRA_Fahrzeug") if r.get("TypBM") and r.get("FamilieAbk")}
    c.executemany("INSERT INTO labour_car VALUES (?,?,?,?,?,?)", sorted(cars))
    fam = {(r["SparteAbk"], r["ProduktionslandAbk"], r["FamilieAbk"]): fix_mojibake(r["FamilieText"]).strip()
           for r in tsv(VEH, "ASRA_Familie") if r.get("SprachAbk") == lang}
    books = set()
    for r in tsv(VEH, "ASRA_Typkennzahl"):
        if r.get("SprachAbk") != lang:
            continue
        k = (r["SparteAbk"], r["ProduktionslandAbk"], r["FamilieAbk"])
        books.add((*k, r["TypkennzahlAbk"], fam.get(k, ""), fix_mojibake(r["TypkennzahlName"]).strip()))
    c.executemany("INSERT OR IGNORE INTO labour_book VALUES (?,?,?,?,?,?)", sorted(books))
    c.executemany("INSERT OR IGNORE INTO labour_group VALUES (?,?)", sorted(
        (r["KonstruktionsgruppeAbk"], fix_mojibake(r["KonstruktionsgruppeName"]).strip())
        for r in tsv(VEH, "ASRA_Konstruktionsgruppe") if r.get("SprachAbk") == lang))
    c.executemany("INSERT OR IGNORE INTO labour_subgroup VALUES (?,?,?,?)", sorted(
        (r["SparteAbk"], r["KonstruktionsgruppeAbk"], r["Einstiegsnummer"],
         fix_mojibake(r["KonstruktionsuntergruppeName"]).strip())
        for r in tsv(VEH, "ASRA_Konstruktionsuntergruppe") if r.get("SprachAbk") == lang))
    fields = ("ArbeitspositionTeil", "ArbeitspositionTaetigkeit", "ArbeitspositionBauzustand",
              "ArbeitspositionVerbundzustand")
    c.executemany("INSERT OR IGNORE INTO labour_op VALUES (?,?,?,?,?,?)", sorted(
        (r["SparteAbk"], r["KonstruktionsgruppeAbk"], r["ArbeitspositionNummer"], r["Einstiegsnummer"],
         r["ArbeitspositionKategorie"],
         " ".join(fix_mojibake(" ".join(r.get(f) or "" for f in fields)).split()))
        for r in tsv(VEH, "ASRA_Arbeitsposition") if r.get("SprachAbk") == lang))
    c.executemany("INSERT OR IGNORE INTO labour_time VALUES (?,?,?,?,?,?,?)", (
        (r["SparteAbk"], r["ProduktionslandAbk"], r["FamilieAbk"], r["KonstruktionsgruppeAbk"],
         r["ArbeitspositionNummer"], r["TypenArt"], num(r["Arbeitswert"]))
        for r in tsv(VEH, "ASRA_Arbeitswert") if num(r.get("Arbeitswert")) is not None))
    expr = {r["FormelID"]: r["FormelAusdruck"].strip() for r in tsv(VEH, "ASRA_Formel")}
    c.executemany("INSERT OR IGNORE INTO labour_formula VALUES (?,?,?,?,?,?,?)", (
        (r["RegionAbk"], r["SparteAbk"], r["ProduktionslandAbk"], r["FamilieAbk"],
         r["KonstruktionsgruppeAbk"], r["ArbeitspositionKategorie"], expr[r["FormelID"]])
        for r in tsv(VEH, "ASRA_Formelverwendung") if r.get("FormelID") in expr))
    for t in ("labour_car", "labour_book", "labour_op", "labour_time", "labour_formula"):
        counts[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]


if __name__ == "__main__":
    main()
