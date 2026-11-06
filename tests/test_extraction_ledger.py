"""What has been extracted, so a re-run does only what is new -- FREE, ~2 s.

    python tests/test_extraction_ledger.py

No VM, no Java: the extractor's decisions are plain functions on folders.

- vehicle tables: pulled one by one; a table is pulled again only when its
  .tsv or .sql is missing or vehicle_tables.json asks for another query;
- pulls (docs, applic, links, epc): done once recorded, until the script or
  the files on disk change (deleted data is pulled again);
- plan_pulls: nothing new -> no online step at all (the VM is not started);
  images only when some are missing; a full run takes everything;
- offline_build: a script runs when its input or code changed or a script
  before it that writes the same files ran; parse_vehicles / parse_links
  always; a failed script is never recorded as done;
- corpus_stamp.py: the index is left alone only while the corpus (and WIS's
  docnr table) is unchanged and the index still holds it complete; a new
  labour table in wis_links.sqlite needs no re-import.
"""
from __future__ import annotations

from _paths import ROOT
import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(ROOT / "extractor"))
import ledger  # noqa: E402
import vm_pipeline as vp  # noqa: E402
import corpus_stamp  # noqa: E402

FAILS = 0


def check(name: str, ok: bool, got=None) -> None:
    global FAILS
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"\n       got: {got!r}"))
    FAILS += not ok


def touch(p: Path, text: str = "x") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def bump(p: Path) -> None:
    """Change a file the way a new pull does: other size, later time."""
    p.write_text(p.read_text(encoding="utf-8") + "more\n", encoding="utf-8")
    t = time.time() + 5
    os.utime(p, (t, t))


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()) as out:
        r = fn(*a, **k)
    return r, out.getvalue()


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="ar_ledger_"))
    out = tmp / "autorundown_out"
    d = ledger.Dirs(out)

    print("-- vehicle tables, one by one --")
    tables = ledger.vehicle_tables(2)
    names = [n for n, _ in tables]
    check("the list has every table, each once", len(names) == len(set(names)) and len(names) >= 36, len(names))
    check("... including the labour scope, bulletins and ASSYST tables",
          {"ASRAW_ID2Umfassttext", "Dok2AendText", "W_AssystDok2LandIntervall"} <= set(names))
    q = dict(tables)
    check("a language table is filtered to the language pulled",
          q["SACode"] == "SELECT * FROM SACode WHERE SprachCode = 2"
          and q["ASRA_Familie"] == "SELECT * FROM ASRA_Familie WHERE SprachAbk = '02'", (q["SACode"], q["ASRA_Familie"]))
    check("... and a whole table has no filter", q["W_Dokumente"] == "SELECT * FROM W_Dokumente")
    d.vehicles.mkdir(parents=True)
    check("nothing pulled: every table is wanted", ledger.vehicle_tables_needed(d.vehicles, 2) == names)
    for n, sql in tables:
        touch(d.vehicles / f"{n}.tsv", "a\tb\n")
        touch(d.vehicles / f"{n}.sql", sql)
    check("all pulled with their queries: none wanted", ledger.vehicle_tables_needed(d.vehicles, 2) == [])
    (d.vehicles / "Gruppen.sql").unlink()
    touch(d.vehicles / "SACode.sql", "SELECT * FROM SACode WHERE SprachCode = 13")
    (d.vehicles / "FzgArt.tsv").unlink()
    check("a table pulled before its .sql existed, another language, a lost .tsv: just those",
          ledger.vehicle_tables_needed(d.vehicles, 2) == ["FzgArt", "SACode", "Gruppen"],
          ledger.vehicle_tables_needed(d.vehicles, 2))
    spec = tmp / "spec"
    spec.mkdir()
    data = json.loads((ROOT / "extractor" / "vehicle_tables.json").read_text(encoding="utf-8"))
    data["tables"].append({"name": "W_NewTable"})
    (spec / "vehicle_tables.json").write_text(json.dumps(data), encoding="utf-8")
    for n in ("FzgArt", "SACode", "Gruppen"):
        touch(d.vehicles / f"{n}.tsv", "a\n")
        touch(d.vehicles / f"{n}.sql", q[n])
    check("a release that adds a table pulls only that one",
          ledger.vehicle_tables_needed(d.vehicles, 2, here=spec) == ["W_NewTable"])

    print("\n-- pulls --")
    state = ledger.load(out)
    check("no ledger yet: docs not done", ledger.pull_done(state, "docs", d) is None)
    for f in ("bodies.pack", "metadata.tsv", "figures.tsv"):
        touch(d.extract / f, "h\n")
    ledger.mark_pull(state, "docs", d)
    ledger.save(out, state)
    state = ledger.load(out)
    check("recorded and saved: done", bool(ledger.pull_done(state, "docs", d)))
    bump(d.extract / "metadata.tsv")
    check("the pulled files changed since: not done", ledger.pull_done(state, "docs", d) is None)
    ledger.mark_pull(state, "docs", d)
    (d.extract / "bodies.pack").unlink()
    check("the data was deleted: not done", ledger.pull_done(state, "docs", d) is None)
    touch(d.extract / "bodies.pack", "h\n")
    ledger.mark_pull(state, "docs", d)
    real = ledger.code_sig
    ledger.code_sig = lambda names, here=ledger.HERE: "another script"
    check("pull.js changed: not done", ledger.pull_done(state, "docs", d) is None)
    ledger.code_sig = real
    touch(d.epc / "records.jsonl", "{}\n")
    touch(d.epc / "manifest.json", "{}")
    touch(d.epc / "done_catalogs.txt", "1\n")
    ledger.mark_pull(state, "epc", d, "all,engines")
    check("EPC: done for the same catalogues", bool(ledger.pull_done(state, "epc", d, "all,engines")))
    check("... not for other ones", ledger.pull_done(state, "epc", d, "engines") is None)
    (out / ledger.NAME).write_text("{not json", encoding="utf-8")
    check("an unreadable ledger is an empty one, never an error", ledger.load(out)["pulls"] == {})

    print("\n-- plan_pulls: what a run does online --")
    state = ledger.load(out)
    for f in ("baumusterzeilen.tsv", "dok2reihe.tsv"):
        touch(d.extract / f, "h\n")
    touch(d.applic2 / "W_DokWS.tsv", "h\n")
    touch(d.extract / "figures.tsv", "a\tgraphik\tb\tc\tcluster\n1\t11\tx\ty\t8\n1\t12\tx\ty\t8\n")
    for step in ("docs", "applic"):
        ledger.mark_pull(state, step, d)
    ledger.save(out, state)            # as vm_pipeline does after each pull
    for n, sql in tables:
        touch(d.vehicles / f"{n}.tsv", "a\n")
        touch(d.vehicles / f"{n}.sql", sql)
    touch(d.images / "pull_images.tsv", "graphik\tcluster\tstatus\n11\t8\tok\n")
    steps = ["docs", "applic", "vehicles", "images", "build"]
    (online, need), log = quiet(vp.plan_pulls, steps, False, state, d, "2", "all,engines")
    check("one image missing: only images go online", online == ["images"] and need == [], (online, need))
    touch(d.images / "pull_images.tsv", "graphik\tcluster\tstatus\n11\t8\tok\n12\t8\tok\n")
    (online, need), log = quiet(vp.plan_pulls, steps, False, state, d, "2", "all,engines")
    check("nothing new: no online step, so the VM is not started", online == [] and need == [], (online, need))
    check("... and the log says so", "nothing new to pull from the VM; it is not started" in log, log)
    (d.vehicles / "Gruppen.sql").unlink()
    (online, need), _ = quiet(vp.plan_pulls, steps, False, state, d, "2", "all,engines")
    check("a missing vehicle table: the VM starts for that table only", online == ["vehicles"] and need == ["Gruppen"],
          (online, need))
    (online, need), _ = quiet(vp.plan_pulls, steps, True, state, d, "2", "all,engines")
    check("a full run takes every online step", online == ["docs", "applic", "vehicles", "images"], online)

    print("\n-- offline_build: only what changed --")
    calls: list[str] = []
    fail = {"script": ""}

    def fake_run(cmd, env, cwd):
        script = cmd[1]
        calls.append(script)
        if script == fail["script"]:
            return 1
        if script == "parse.py":
            touch(d.extract / "records.jsonl", "{}\n")
        if script == "convert_images.py":
            touch(d.extract / "images" / "catalog.tsv", "c\n")
        return 0

    real_run, vp.run = vp.run, fake_run
    try:
        (d.images / "raw").mkdir(parents=True, exist_ok=True)
        state = ledger.load(out)
        ran, _ = quiet(vp.offline_build, out, state, False, "2", "wisnet")
        check("first build: every script", ran == list(ledger.BUILD), ran)
        state = ledger.load(out)
        ran, log = quiet(vp.offline_build, out, state, False, "2", "wisnet")
        check("nothing new: only the two cheap ones", ran == ["parse_vehicles.py", "parse_links.py"], ran)
        check("... the rest say they were skipped", "parse.py: nothing new since" in log, log[:300])
        bump(d.images / "pull_images.tsv")
        ran, _ = quiet(vp.offline_build, out, state, False, "2", "wisnet")
        check("new images: converted and placed, the documents left alone",
              ran == ["parse_vehicles.py", "parse_links.py", "convert_images.py", "apply_images.py"], ran)
        bump(d.extract / "metadata.tsv")
        ran, _ = quiet(vp.offline_build, out, state, False, "2", "wisnet")
        check("new documents: parse and everything that rewrites its output, not the conversion",
              ran == ["parse.py", "apply_applic.py", "parse_vehicles.py", "parse_links.py", "apply_images.py"], ran)
        (d.extract / "records.jsonl").unlink()
        ran, _ = quiet(vp.offline_build, out, state, False, "2", "wisnet")
        check("records.jsonl gone: parsed again", "parse.py" in ran, ran)
        bump(d.applic2 / "W_DokWS.tsv")
        fail["script"] = "apply_applic.py"
        calls.clear()
        try:
            quiet(vp.offline_build, out, state, False, "2", "wisnet")
            check("a failing script stops the build", False, calls)
        except SystemExit:
            check("a failing script stops the build", calls[-1] == "apply_applic.py", calls)
        fail["script"] = ""
        state = ledger.load(out)
        ran, _ = quiet(vp.offline_build, out, state, False, "2", "wisnet")
        check("... and is never recorded as done: it runs next time", "apply_applic.py" in ran, ran)
        ran, _ = quiet(vp.offline_build, out, state, True, "2", "wisnet")
        check("a full run builds everything", ran == list(ledger.BUILD), ran)
    finally:
        vp.run = real_run

    print("\n-- what Shop setup shows --")
    st = ledger.status(out)
    check("each pull's date, tables pulled of wanted, each build",
          st["pulls"]["docs"]["at"] and st["vehicle_tables"]["wanted"] == len(names)
          and set(st["build"]) == set(ledger.BUILD), st)

    print("\n-- corpus_stamp.py: re-import only a changed corpus --")
    corpus = tmp / "wis_extract"
    corpus.mkdir()
    (corpus / "manifest.json").write_text(json.dumps({"source_type": "mercedes-wis-virtualbox"}), encoding="utf-8")
    touch(corpus / "records.jsonl", "{}\n")
    side = sqlite3.connect(corpus / "wis_links.sqlite")
    side.executescript("CREATE TABLE docnr(docnr TEXT, dok_nr_id INT, dok_id INT);"
                       "INSERT INTO docnr VALUES ('ar4210p0001a', 1, 100);")
    side.commit()
    side.close()
    index = tmp / "index.db"
    ix = sqlite3.connect(index)
    ix.executescript("CREATE TABLE vehicles(doc_id TEXT, source_name TEXT, complete INT, indexed_pages INT);"
                     "INSERT INTO vehicles VALUES ('mercedes-wis-virtualbox-en-100', 'mercedes-wis-virtualbox', 1, 50);"
                     "INSERT INTO vehicles VALUES ('lemon-1', 'lemon', 1, 9);")
    ix.commit()
    ix.close()
    stamp = lambda *a: quiet(corpus_stamp.main, [*a, "--index", str(index)])  # noqa: E731
    rc, msg = stamp("--check", str(corpus))
    check("never imported here: import", rc == 0 and "not imported" in msg, (rc, msg))
    rc, _ = stamp("--record", str(corpus))
    rc, msg = stamp("--check", str(corpus))
    check("recorded, nothing changed: unchanged (exit 10)", rc == corpus_stamp.UNCHANGED, (rc, msg))
    side = sqlite3.connect(corpus / "wis_links.sqlite")
    side.executescript("CREATE TABLE labour_scope(id INT); INSERT INTO labour_scope VALUES (1);")
    side.commit()
    side.close()
    rc, _ = stamp("--check", str(corpus))
    check("a new labour table in the sidecar needs no re-import", rc == corpus_stamp.UNCHANGED, rc)
    side = sqlite3.connect(corpus / "wis_links.sqlite")
    side.execute("INSERT INTO docnr VALUES ('ar4210p0002a', 2, 101)")
    side.commit()
    side.close()
    rc, msg = stamp("--check", str(corpus))
    check("a new cross-reference number (docnr) does", rc == 0 and "changed" in msg, (rc, msg))
    stamp("--record", str(corpus))
    touch(corpus / "variants.jsonl", '{"name": "190 E"}\n')
    rc, _ = stamp("--check", str(corpus))
    check("a new or changed corpus file does", rc == 0, rc)
    stamp("--record", str(corpus))
    touch(corpus / "parse.log", "a line")
    rc, _ = stamp("--check", str(corpus))
    check("a log file does not", rc == corpus_stamp.UNCHANGED, rc)
    ix = sqlite3.connect(index)
    ix.execute("UPDATE vehicles SET complete = 0 WHERE source_name LIKE 'mercedes-wis%'")
    ix.commit()
    rc, msg = stamp("--check", str(corpus))
    check("the index holds it only partly: import", rc == 0 and "no longer" in msg, (rc, msg))
    ix.execute("DELETE FROM vehicles WHERE source_name LIKE 'mercedes-wis%'")
    ix.commit()
    ix.close()
    rc, _ = stamp("--check", str(corpus))
    check("removed from the index: import", rc == 0, rc)
    other = tmp / "other.db"
    other.write_bytes(index.read_bytes())
    rc, _ = quiet(corpus_stamp.main, ["--check", str(corpus), "--index", str(other)])
    check("another index has its own stamp", rc == 0, rc)
    nested = tmp / "nested"
    (nested / "wis_en").mkdir(parents=True)
    (nested / "wis_en" / "manifest.json").write_text(json.dumps({"source_type": "mercedes-wis-virtualbox"}),
                                                     encoding="utf-8")
    check("a corpus one folder down (wis_extract/wis_en) is found", corpus_stamp.corpora(nested) == [nested / "wis_en"])
    rc, _ = quiet(corpus_stamp.main, ["--record", str(tmp / "empty"), "--index", str(index)])
    check("no corpus at all: nothing to record, not a failure", rc == 0, rc)

    print(f"\n{FAILS} failure(s)")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
