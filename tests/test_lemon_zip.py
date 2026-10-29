"""A LEMON car's zip, saved by the user from LEMON's site -- FREE, no network, ~10 s.

    python tests/test_lemon_zip.py

"Add this car" for a car not on this PC opens its page on LEMON's site (the
app never fetches from it); the user saves the zip in Downloads and the app
imports it (autorundown/lemon_zip.py, unzip_lemon.py, job mode lemon_zip).
Checks: the zip is found under LEMON's name, also as "name (1).zip" and with
a character Windows can't hold replaced, never while it is still a partial
download; unsafe zips are refused (a path out of the folder, an absolute or
drive path, two folders at the top, no index.html, a file inflating too far);
unpacking writes <app dir>/manuals/<doc_id>/ with the download mark and
never over a folder of the user's; through the API: "download" with the
LEMON (and CHARM) links, "waiting" until the zip is there, then the import
job, after which the car is in the index and the zip still in Downloads; a
phone can't; removing the car recycles its folder. Uses a throwaway
AUTORUNDOWN_HOME, Downloads and index, so nothing real is touched.
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo and tools/ on sys.path)
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_lemzip_"))
DL = TMP / "downloads"
DL.mkdir()
os.environ["AUTORUNDOWN_HOME"] = str(TMP / "home")        # before importing autorundown
os.environ["AUTORUNDOWN_DOWNLOADS"] = str(DL)
os.environ["AUTORUNDOWN_RECYCLE_TO"] = str(TMP / "bin")

from fastapi.testclient import TestClient  # noqa: E402

import ingest  # noqa: E402
import make_sample_lemon  # noqa: E402
from autorundown import config, importer_jobs, lemon_catalogue, lemon_zip, recycle, server  # noqa: E402

fails = 0


def check(cond: bool, what: str) -> None:
    global fails
    print(("  ok   " if cond else "  FAIL ") + what)
    fails += not cond


CAMRY = ["Toyota", "2008", "Camry", "LE, 2.4 E, Automatic"]   # the sample's name for it
DOC = lemon_catalogue.doc_id(*CAMRY)
NAME = lemon_catalogue.zip_name(*CAMRY)
TOP = f"2008 Toyota {lemon_catalogue.vehicle_name(*CAMRY[2:])}"   # LEMON's folder in the zip

archive = make_sample_lemon.make(TMP / "a" / "lemon")
db = TMP / "index.db"
rc = ingest.main(["--archive", str(archive), "--index", str(db), "--importer", "lemon",
                  "--only", "2015-ford-f-150"])
check(rc == 0, "the sample F-150 is imported")
# the Camry is not beside it, nor in the folder above (else "Add this car"
# imports the folder): it is a zip
camry = TMP / "camry_src"
shutil.move(str(archive / "2008-toyota-camry"), camry)


def make_zip(path: Path, entries: dict[str, bytes] | None = None) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        if entries is None:
            for f in sorted(camry.rglob("*")):
                if f.is_file():
                    z.write(f, f"{TOP}/{f.relative_to(camry).as_posix()}")
        else:
            for n, b in entries.items():
                z.writestr(n, b)
    return path


print("finding the zip")
spare = TMP / "spare"
spare.mkdir()
check(lemon_zip.find(NAME, spare) is None, "nothing there yet")
(spare / (NAME + ".crdownload")).write_bytes(b"PK partial")
(spare / NAME).write_bytes(b"PK\x03\x04 still being written")
check(lemon_zip.find(NAME, spare) is None, "a partial download or a half-written zip is not taken")
make_zip(spare / NAME.replace(".zip", " (1).zip"))
check(lemon_zip.find(NAME, spare) == spare / NAME.replace(".zip", " (1).zip"),
      "a browser's \"name (1).zip\" is found")
odd = ["Ford", "2016", "F-150", "4WD/AWD V8"]
make_zip(spare / lemon_catalogue.zip_name(*odd).replace("/", "_"))
check(lemon_zip.find(lemon_catalogue.zip_name(*odd), spare) is not None,
      "a \"/\" in the car's name, saved as \"_\", is found")
check(lemon_zip.find("LEMON 2008 Toyota Camry.zip", spare) is None, "another car's name is not")

print("unsafe zips are refused")
idx = b"<html>LEMON</html>"
for entries, what in [
    ({f"{TOP}/index.html": idx, f"{TOP}/../evil.txt": b"x"}, "a path out of the folder"),
    ({f"{TOP}/index.html": idx, "/etc/evil": b"x"}, "an absolute path"),
    ({f"{TOP}/index.html": idx, "C:/evil.txt": b"x"}, "a drive path"),
    ({f"{TOP}/index.html": idx, f"{TOP}\\..\\evil.txt": b"x"}, "a backslash path"),
    ({f"{TOP}/index.html": idx, "other/index.html": idx}, "two folders at the top"),
    ({f"{TOP}/index.html": idx, "loose.txt": b"x"}, "a file outside the car's folder"),
    ({f"{TOP}/pages/1.html": idx}, "no index.html"),
    ({f"{TOP}/index.html": idx, f"{TOP}/bomb.bin": b"\0" * (8 << 20)}, "a file inflating 1000x and more"),
]:
    z = make_zip(TMP / "bad.zip", entries)
    try:
        lemon_zip.check(z)
        check(False, f"{what} is refused")
    except lemon_zip.BadZip as e:
        check(True, f"{what} is refused: {e}")
(TMP / "bad.zip").write_bytes(b"not a zip")
try:
    lemon_zip.check(TMP / "bad.zip")
    check(False, "a file that is not a zip is refused")
except lemon_zip.BadZip:
    check(True, "a file that is not a zip is refused")

print("unpacking")
good = make_zip(TMP / "good.zip")
dest = TMP / "unpacked" / DOC
n = lemon_zip.unpack(good, dest)
mark = json.loads((dest / recycle.DOWNLOAD_MARK).read_text())
check(n > 3 and (dest / "index.html").is_file() and not (dest / TOP).exists(),
      f"the car's folder's contents, {n} files, straight into <doc_id>/")
check(mark["complete"] and mark["source"] == "lemon-zip", f"with the download mark: {mark}")
check(lemon_zip.unpack(good, dest) == n, "a second try replaces the folder it wrote")
users = TMP / "unpacked" / "users-own"
users.mkdir()
(users / "notes.txt").write_text("mine")
try:
    lemon_zip.unpack(good, users)
    check(False, "a folder AutoRundown didn't write is left alone")
except lemon_zip.BadZip:
    check((users / "notes.txt").read_text() == "mine", "a folder AutoRundown didn't write is left alone")

if os.name == "nt":                     # Windows' 260-character paths: refused up front
    deep = TMP / ("d" * 120) / ("e" * 100)
    try:
        lemon_zip.unpack(good, deep)
        check(False, "a folder too deep for Windows' path limit is refused")
    except lemon_zip.BadZip as e:
        check("Windows allows" in str(e) and not deep.exists(),
              f"a folder too deep for Windows' path limit is refused before writing: {str(e)[:60]}…")

print("through the API")
APP = server.create_app(str(db))
pc = TestClient(APP, base_url="http://localhost:8756", client=("127.0.0.1", 50000))
phone = TestClient(APP, base_url="http://192.168.1.10:8756", client=("192.168.1.50", 50000))
same = {"Origin": "http://localhost:8756"}
r = pc.post("/api/import/lemon-add", json={"path": CAMRY}, headers=same).json()
check(r.get("action") == "download" and r["zip_name"] == NAME and r["downloads"] == str(DL),
      f"not on this PC: download, under LEMON's zip name, into Downloads: {r}")
check(r["links"]["lemon"] == "https://lemon-manuals.la/Toyota/2008/Camry%20LE%2C%202.4%20E%2C%20Automatic/",
      f"LEMON's page for the car: {r['links']['lemon']}")
check(r["links"].get("charm") == "https://charm.li/Toyota/2008/", "and CHARM's year page as the backup")
check(phone.post("/api/import/lemon-zip", json={"path": CAMRY},
                 headers={"Origin": "http://192.168.1.10:8756"}).status_code == 403, "a phone can't")
check(pc.post("/api/import/lemon-zip", json={"path": ["Toyota", "2008"]}, headers=same).status_code == 400,
      "a folder, not a car, is refused")
check(pc.post("/api/import/lemon-zip", json={"path": ["Toyota", "2008", "Camry", "Nope"]},
              headers=same).status_code == 404, "a car not in the catalogue is refused")
check(pc.post("/api/import/lemon-zip", json={"path": CAMRY}, headers=same).json()["action"] == "waiting",
      "waiting while the zip isn't in Downloads")
bad = make_zip(DL / NAME, {f"{TOP}/../evil": b"x", f"{TOP}/index.html": idx})
r = pc.post("/api/import/lemon-zip", json={"path": CAMRY}, headers=same)
check(r.status_code == 400 and "can't be imported" in r.json()["detail"], f"an unsafe zip is refused: {r.json()}")
bad.unlink()
shutil.copy(good, DL / NAME)
r = pc.post("/api/import/lemon-zip", json={"path": CAMRY}, headers=same)
check(r.status_code == 200 and r.json()["action"] == "import", f"the zip arrives: the import starts: {r.json()}")
for _ in range(600):
    st = pc.get("/api/import").json()
    if st["state"] != "running":
        break
    time.sleep(0.05)
check(st["state"] == "done", f"the job finished: {st['state']}\n" + "\n".join(st.get("log", [])[-6:]))
check(any("unzip_lemon.py" in l for l in st["log"]) and any("--importer lemon" in l for l in st["log"]),
      "it unpacked the zip, then imported the folder as LEMON")
folder = config.APP_DIR / "manuals" / DOC
with sqlite3.connect(db) as con:
    got = con.execute("SELECT file_path FROM vehicles WHERE doc_id=? AND complete=1", (DOC,)).fetchone()
check(got is not None and Path(got[0]) == folder, f"the Camry is in the index from {folder}: {got}")
check((DL / NAME).is_file(), "the zip is still in Downloads (it is the user's file)")
check(pc.get("/api/catalogue/suggest", params={"q": "2008 camry le 2.4 automatic"}).json()["total"] == 0,
      "and it is no longer offered as not downloaded")

print("an import cut short (agent review, 2026-10-28)")
with sqlite3.connect(db) as con:          # as a killed ingest leaves it
    con.execute("UPDATE vehicles SET complete=0, indexed_pages=0 WHERE doc_id=?", (DOC,))
rows = pc.get("/api/catalogue/suggest", params={"q": "2008 camry le 2.4 automatic"}).json()["rows"]
check(len(rows) == 1 and rows[0]["partial"], f"a partly imported car is still offered, marked partial: {rows}")
m = next(x for x in importer_jobs.manuals(db)["lemon"] if x["doc_id"] == DOC)
check(not m["complete"] and m["pages"] > 0, f"Shop setup counts its pages, not 0: {m['pages']}")
check(importer_jobs.lemon_on_disk(DOC, db) is not None, "its finished folder is found, so Add this car resumes it")
lemon_zip.mark(folder, DL / NAME, complete=False)  # as a killed unzip leaves it
check(importer_jobs.lemon_on_disk(DOC, db) is None,
      "a folder whose unzip never finished is never imported as it stands (it had 2,738 of 8,001 pages)")
lemon_zip.mark(folder, DL / NAME, complete=True)
with sqlite3.connect(db) as con:
    con.execute("UPDATE vehicles SET complete=1 WHERE doc_id=?", (DOC,))

print("removing it")
check(recycle.allowed(folder) == "", "its folder may be recycled (download mark, in manuals/)")
r = pc.post("/api/import", json={"path": "", "mode": "remove", "vehicles": [DOC]}, headers=same)
for _ in range(600):
    st = pc.get("/api/import").json()
    if st["state"] != "running":
        break
    time.sleep(0.05)
check(st["state"] == "done" and not folder.exists(), "Remove recycles the unpacked folder")
check((DL / NAME).is_file(), "and leaves the zip")

print("progress lines")
from autorundown import importer_jobs  # noqa: E402
pr = importer_jobs.Progress()
pr.feed(f"[01:02:03] == Unpacking {NAME}")
pr.feed("[01:02:04] unzipped 500/2000 files")
check((pr.done, pr.total, pr.unit) == (500, 2000, "files"), f"unpacking fills the bar: {pr.as_dict()}")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'all passed' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
