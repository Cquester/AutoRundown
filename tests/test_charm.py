"""One car from Operation CHARM -- FREE, no network, ~10 s.

    python tests/test_charm.py

Builds the sample CHARM torrent data (tools/make_sample_charm.py) in a temp
folder and checks, with charm_fetch.LocalSwarm standing in for the swarm:
the reader (SquashFS + LMDB) finds a car, its pages, articles and images;
a plan sizes the job and fetches only some pieces; a download writes a
LEMON-style folder that `ingest.py --importer lemon` imports as the car,
then deletes the torrent data; a name CHARM doesn't have gets candidates,
never a guess; a car already in the index, too little space, and a cancel
are refused; bad data and unsafe names are refused, never trusted; the job
modes charm_plan / charm_car through the API (admin only, plan first);
removing a downloaded car recycles its folder but never one of the user's.
Uses a throwaway AUTORUNDOWN_HOME, so nothing real is touched.
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo and tools/ on sys.path)
import json
import os
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_charm_"))
os.environ["AUTORUNDOWN_HOME"] = str(TMP / "home")    # before importing autorundown
os.environ["AUTORUNDOWN_RECYCLE_TO"] = str(TMP / "bin")

import make_sample_charm  # noqa: E402
from autorundown import charm, recycle  # noqa: E402
from autorundown.charm import (BadData, Charm, Layout, Missing, Store, car_key, image_file,  # noqa: E402
                               image_refs, listing_names, match_car, page_html, year_key)

fails = 0
CAR = ("Mercedes Benz", "1987", "190E 2.3-16 (201.034) L4-2.3L (102.983)")
DOC = "1987-mercedes-benz-190e-2-3-16-201-034-l4-2-3l-102-983"


def check(cond: bool, what: str) -> None:
    global fails
    print(("  ok   " if cond else "  FAIL ") + what)
    fails += not cond


def download(*args, env=None, work=None, out=None):
    """charm_download.py as the job runs it; (exit code, output lines)."""
    cmd = [sys.executable, str(Path(ROOT) / "charm_download.py"), "--local-source", str(SRC),
           "--work", str(work or TMP / "work"), "--out", str(out or TMP / "manuals"), *args]
    e = {**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})}
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=e, cwd=ROOT)
    return r.returncode, (r.stdout + r.stderr).splitlines()


def tagged(lines, tag):
    for l in lines:
        if l.startswith(tag + " "):
            return json.loads(l[len(tag) + 1:])
    return None


SRC = TMP / "src"
info = make_sample_charm.build(SRC)
TOTAL = info["pieces"]
CAR_ARGS = ["--make", CAR[0], "--year", CAR[1], "--car", CAR[2]]

print("reader")
meta = json.loads((SRC / "layout.json").read_text())
lay = Layout(meta["piece_length"], [tuple(f) for f in meta["files"]])
c = Charm(Store(SRC, lay, range(lay.num_pieces)))
check(c.pages.get(car_key(*CAR)) is not None, "the car's own key is found")
paths = dict(c.pages.scan(car_key(*CAR), car_key(*CAR)[:-1] + b"0"))
check(len(paths) == 12, f"its 12 paths, and no other car's: {len(paths)}")
arts = dict(c.pages.scan(b"46795,", b"46795-"))
check(len(arts) == 8, f"its articles, one per page that has one: {len(arts)}")
bodies = [c.pages.value(v) for v in arts.values()]
check(any(len(b) > 1000 for b in bodies), "an overflow-sized article reads whole")
refs = {r for b in bodies for r in image_refs(b)}
check(len(refs) == 15 and all(r.startswith(b"/SAMPLE/grp1/") for r in refs), f"its image keys: {len(refs)}")
e = c.images.get(sorted(refs)[-1])
check(e is not None and charm.image_ext(c.images.value(e)) == "png", "an image reads back as a PNG")
yl = c.pages.get(c.pages.value(c.pages.get(year_key(CAR[0], CAR[1]))))
names = listing_names(c.pages.value(yl))
check(names == sorted([CAR[2], "300D (124.133) L6-3.0L DSL Turbo (603.960)"]), f"the year's cars: {names}")
check(match_car(names, "190E 2.3-16 (201.034)", "L4-2.3L (102.983)")[0] == CAR[2],
      "a catalogue model + engine that spell CHARM's name is taken")
exact, cands = match_car(names, "190E 2.3-16", "102.983")
check(exact is None and cands[:1] == [CAR[2]], f"an inexact one is offered, not taken: {cands}")
part = Store(SRC, lay, {0})
try:
    Charm(part)
    check(False, "a read of a piece not downloaded raises Missing")
except Missing as m:
    check(m.pieces and 0 not in m.pieces, f"a read of a piece not downloaded raises Missing {m.pieces[:3]}")

print("untrusted data is checked, never trusted")
for bad in (b"/../../etc/x/", b"/a/../b/", b"/a\\b/c/", b"/C:/x/"):
    try:
        image_file(bad, "png")
        check(False, f"image key {bad!r} refused")
    except BadData:
        check(True, f"image key {bad!r} refused")
check(image_file(b"/IMP49Q313/euro210/151068321/", "png") == "IMP49Q313/euro210/151068321.png",
      "a CHARM image key becomes the LEMON zip's own file name")
html = page_html(["Make", "1999", "Car <b>", "Engine"], {"/Make/1999/Car%20%3Cb%3E/": 1},
                 b"<p>x</p><a href='javascript:alert(1)'>j</a><a href='../Other/'>o</a>"
                 b"<img src='/images/../../x/'>", {}, 2)
check("Car &lt;b&gt;" in html and "<b>" not in html.split("main")[0], "names are escaped")
check("../404.html" in html and "/images/../" not in html, "an unknown link or image goes nowhere")
lmdb_bad = bytearray((SRC / charm.IMAGES_FILE).read_bytes())
struct.pack_into("<Q", lmdb_bad, 16 + 24 + 48 + 40, 10**9)            # root page past the end
struct.pack_into("<Q", lmdb_bad, 4096 + 16 + 24 + 48 + 40, 10**9)
bl = TMP / "bad.mdb"
bl.write_bytes(bytes(lmdb_bad))
lb = charm.Lmdb(lambda off, n: bytes(lmdb_bad[off:off + n]), len(lmdb_bad), lambda pg: [0])
try:
    lb.get(b"/x/")
    check(False, "a root page past the end of the file is refused")
except BadData:
    check(True, "a root page past the end of the file is refused")
bsrc = TMP / "badsrc"
shutil.copytree(SRC, bsrc)
sq = bsrc / charm.PAGES_FILE
raw = bytearray(sq.read_bytes())
raw[0:4] = b"XXXX"
sq.write_bytes(bytes(raw))
r = subprocess.run([sys.executable, str(Path(ROOT) / "charm_download.py"), "--local-source", str(bsrc),
                    "--work", str(TMP / "badwork"), "--out", str(TMP / "badout"), *CAR_ARGS, "--plan"],
                   capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
check(r.returncode == 1 and "STOP: the CHARM data is not what was expected" in r.stdout,
      "a broken pages image stops the run with a reason")

print("plan")
rc, out = download(*CAR_ARGS, "--plan", "--keep-data")
plan = tagged(out, "PLAN")
check(rc == 0 and plan is not None, "a plan runs\n" + "\n".join(out[-5:]))
if plan:
    check(plan["doc_id"] == DOC and plan["pages"] == 12 and plan["images"] == 15,
          f"it names the car, its pages and images: {plan['doc_id']}, {plan['pages']}, {plan['images']}")
    check(0 < plan["fetched_pieces"] < TOTAL, f"it fetched only some pieces: {plan['fetched_pieces']} of {TOTAL}")
    check(plan["disk_needed_bytes"] >= plan["stays_bytes"] > 0 and plan["free_bytes"] > 0,
          "it says the disk needed, what stays and what is free")
check(not (TMP / "manuals").exists(), "a plan writes no manual")
check((TMP / "work" / f"plan-{DOC}.json").is_file(), "the plan is kept for the download")

print("download")
rc, out = download(*CAR_ARGS, "--keep-data")
have = json.loads((TMP / "work" / "local_have.json").read_text())
check(rc == 0 and tagged(out, "CHARM_DONE")["doc_id"] == DOC, "the download finishes\n" + "\n".join(out[-5:]))
check(len(have) < TOTAL, f"the whole car took {len(have)} of {TOTAL} pieces")
folder = TMP / "manuals" / DOC
pages = sorted(folder.glob("pages/*.html"))
imgs = list((folder / "images").rglob("*.png"))
check(len(pages) == 12 and (folder / "index.html").is_file(), f"a LEMON-style folder: {len(pages)} pages")
check(len(imgs) == 14 and any("1 not in CHARM" in l for l in out),
      f"its images ({len(imgs)}), and the one CHARM lacks is said, not invented")
mark = json.loads((folder / recycle.DOWNLOAD_MARK).read_text())
check(mark["complete"] is True and mark["car"] == CAR[2], "the folder is marked as downloaded and complete")
p1 = (folder / "pages" / "1.html").read_text(encoding="utf-8")
check('breadcrumb-part' in p1 and f">{CAR[2]}<" in p1, "page 1 is the car, with LEMON's breadcrumbs")
some = next(p.read_text(encoding="utf-8") for p in pages if "big-img" in p.read_text(encoding="utf-8"))
check("src='../images/SAMPLE/grp1/" in some and "/images/SAMPLE" not in some.replace("../images/SAMPLE", ""),
      "images point at the folder's own files")

print("import (ingest.py --importer lemon)")
db = TMP / "index.db"
r = subprocess.run([sys.executable, str(Path(ROOT) / "ingest.py"), "--archive", str(folder), "--index", str(db),
                    "--importer", "lemon"], capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
check(r.returncode == 0, "the LEMON importer reads the folder unchanged")
with sqlite3.connect(db) as con:
    row = con.execute("SELECT doc_id, make, model, year_range, engine FROM vehicles").fetchone()
    n_img = con.execute("SELECT COUNT(*) FROM pages WHERE images != ''").fetchone()[0]
check(row == (DOC, CAR[0], CAR[2], CAR[1], "L4-2.3L (102.983)"),
      f"as the same car a LEMON zip of it gives: {row}")
check(n_img > 0, "its pages carry their images")
rc, out = download(*CAR_ARGS, "--index", str(db), work=TMP / "work2", out=TMP / "manuals2")
check(rc == 5 and any("already in the index" in l for l in out), "a car already in the index is refused")

print("refusals")
rc, out = download("--make", CAR[0], "--year", CAR[1], "--model", "190E 2.3", "--engine", "", "--plan",
                   work=TMP / "work3")
nf = tagged(out, "NOT_FOUND")
check(rc == 2 and nf and nf["candidates"][0] == CAR[2] and nf["cars_that_year"] == 2,
      f"a name CHARM doesn't have gets candidates, best first: {nf and nf['candidates']}")
rc, out = download("--make", "Nobody", "--year", "1999", "--car", "X", "--plan", work=TMP / "work3")
check(rc == 2 and tagged(out, "NOT_FOUND")["candidates"] == [], "a make CHARM doesn't have: no candidates")
rc, out = download(*CAR_ARGS, env={"AUTORUNDOWN_CHARM_FREE": "1"}, work=TMP / "work4", out=TMP / "m4")
check(rc == 4 and not (TMP / "m4" / DOC / "pages").exists(), "too little free space is refused before writing")
(TMP / "cancel").write_text("x")
rc, out = download(*CAR_ARGS, env={"AR_CANCEL_FILE": str(TMP / "cancel")}, work=TMP / "work5")
check(rc == 3, "a cancel stops it")
(TMP / "cancel").unlink()
(TMP / "m6" / DOC).mkdir(parents=True)
(TMP / "m6" / DOC / "mine.txt").write_text("the user's own")
rc, out = download(*CAR_ARGS, work=TMP / "work6", out=TMP / "m6")
check(rc == 1 and (TMP / "m6" / DOC / "mine.txt").exists(),
      "a folder it did not write is never replaced")

print("afterwards only the car's folder stays")
rc, out = download(*CAR_ARGS, work=TMP / "work7", out=TMP / "m7")
check(rc == 0 and not (TMP / "work7" / "data").exists() and not (TMP / "work7" / "local_have.json").exists(),
      "the torrent data and its state are deleted")
check((TMP / "m7" / DOC / "pages" / "1.html").is_file(), "the car's folder stays")

print("the job modes (charm_plan, charm_car) through the API")
os.environ["AUTORUNDOWN_CHARM_SOURCE"] = str(SRC)
from fastapi.testclient import TestClient  # noqa: E402
from autorundown import config, importer_jobs, server  # noqa: E402

idb = TMP / "api.db"
APP = server.create_app(str(idb))
pc = TestClient(APP, base_url="http://localhost:8756", client=("127.0.0.1", 50000))
phone = TestClient(APP, base_url="http://192.168.1.10:8756", client=("192.168.1.50", 50000))
same = {"Origin": "http://localhost:8756"}


def run_job(body):
    r = pc.post("/api/import", json=body, headers=same)
    if r.status_code != 200:
        return r, None
    for _ in range(240):
        s = pc.get("/api/import").json()
        if s["state"] != "running":
            return r, s
        time.sleep(0.25)
    return r, s


st = pc.get("/api/import/charm").json()
check(st["ok"] and st["local"], f"the sample source is seen: {st}")
check(phone.get("/api/import/charm").status_code == 403, "a phone can't see it")
check(phone.post("/api/import", json={"path": "", "mode": "charm_plan", "car": list(CAR)},
                 headers={"Origin": "http://192.168.1.10:8756"}).status_code == 403, "nor start one")
for bad in ([CAR[0]], [CAR[0], "19x7", CAR[2]], ["", "1987", "x"]):
    check(pc.post("/api/import", json={"path": "", "mode": "charm_plan", "car": bad},
                  headers=same).status_code == 400, f"a bad car is refused: {bad}")
r = pc.post("/api/import", json={"path": "", "mode": "charm_car", "car": list(CAR)}, headers=same)
check(r.status_code == 400 and "size first" in r.json()["detail"], "no download before its size is checked")
r, s = run_job({"path": "", "mode": "charm_plan", "car": [CAR[0], CAR[1], "190E 2.3-16 (201.034)",
                                                          "L4-2.3L (102.983)"]})
check(s and s["state"] == "done" and s["plan"]["car"] == CAR[2],
      "a plan from a catalogue path names CHARM's car\n" + "\n".join((s or {}).get("log", [])[-4:]))
check(s and s["progress"]["phase"].startswith("CHARM"), f"the progress bar names the step: {s and s['progress']}")
r, s = run_job({"path": "", "mode": "charm_plan", "car": [CAR[0], CAR[1], "190E"]})
check(s and s["state"] == "failed" and s["not_found"]["candidates"], "a near name fails with its candidates")
r, s = run_job({"path": "", "mode": "charm_car", "car": list(CAR)})
check(s and s["state"] == "done", "the download job runs\n" + "\n".join((s or {}).get("log", [])[-6:]))
check(s and any("ingest.py" in l and "--importer lemon" in l for l in s["log"]),
      "then imports the folder with the LEMON importer")
with sqlite3.connect(idb) as con:
    got = con.execute("SELECT file_path FROM vehicles WHERE doc_id=?", (DOC,)).fetchone()
dl = config.APP_DIR / "manuals" / DOC
check(got is not None and Path(got[0]) == dl, f"the car is in the index from {dl}")
check(not (importer_jobs.charm_work() / "data").exists(), "the torrent data is gone")

print("removing a downloaded car recycles its folder; a user's folder stays")
check(recycle.allowed(dl) == "", "its folder may be recycled")
own = config.APP_DIR / "manuals" / "my-own"
own.mkdir()
check(recycle.allowed(own) != "", "a folder there without the mark may not")
elsewhere = TMP / "elsewhere" / DOC
shutil.copytree(dl, elsewhere)
check(recycle.allowed(elsewhere) != "", "nor a marked folder anywhere else")
r, s = run_job({"path": "", "mode": "remove", "vehicles": [DOC]})
check(s and s["state"] == "done" and any("--recycle" in l for l in s["log"]),
      "Remove sends it to the Recycle Bin\n" + "\n".join((s or {}).get("log", [])[-4:]))
check(not dl.exists() and own.exists(), "the downloaded car's folder went; the user's stayed")

print("progress lines")
pr = importer_jobs.Progress()
pr.feed("[01:02:03] == CHARM: images (40 pieces)")
pr.feed("[01:02:09] pieces 12/40  310 KB/s  9 peers  uploaded 3 KB")
check(pr.phase == "CHARM: images (40 pieces)" and (pr.done, pr.total, pr.unit) == (12, 40, "pieces")
      and "9 peers" in pr.activity, f"pieces and peers fill the bar: {pr.as_dict()}")
pr.feed("[01:02:03] == CHARM: opening the torrent (checking the pieces already on disk)")
pr.feed("[01:02:33] checking the pieces on disk: 6%")
check("6%" in pr.detail, f"the first file check shows how far it is: {pr.as_dict()}")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'all passed' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
