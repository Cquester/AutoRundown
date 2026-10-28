"""Download one car from Operation CHARM's torrent and write it as a LEMON-style
folder that `ingest.py --importer lemon` reads.

    python charm_download.py --make "Mercedes Benz" --year 1987 \\
        --car "190E 2.3-16 (201.034) L4-2.3L (102.983)" --plan
    python charm_download.py --make ... --year ... --model 190E --engine "2.3" --plan

--plan sizes the job and changes nothing outside the work folder: it fetches
the car's text and the image index (~200 MB for a car) and prints a line
`PLAN {json}` with the download size, the free space needed and what stays.
Without --plan it downloads (text, then images unless --text-only), writes
<out>/<doc_id>/ (index.html, pages/, images/), and deletes the torrent data.

Rules (the user's, 2026-10-09 for LEMON; the same for CHARM): only the
swarm -- never charm.li over HTTP, the .torrent is the one file from the
site --; never seed; upload capped; the session closed when done; refused
without the free space; afterwards only the car's folder stays.

Exit codes: 0 done, 2 no such car (candidates printed), 3 cancelled,
4 not enough free space, 5 already in the index, 1 anything else.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import unquote

from autorundown import config
from autorundown.charm import (Charm, Missing, BadData, car_key, year_key, listing_names,
                               match_car, image_refs, image_ext, image_file, page_html,
                               SPECIAL)
from autorundown.charm_fetch import Cancelled, LocalSwarm, TorrentSwarm
from autorundown.importers.lemon import _slug
from autorundown.recycle import DOWNLOAD_MARK

IMAGE_BATCH = 32          # pieces per image batch (512 MB): progress and cancel points
AVG_IMAGE = 35_000        # bytes; 698 GB / 20.25 M images, for "what stays"


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def cancelled() -> bool:
    f = os.environ.get("AR_CANCEL_FILE")
    if f and Path(f).exists():
        return True
    ppid = os.environ.get("AR_PARENT_PID")
    if ppid and not _alive(int(ppid)):
        return True
    return False


def _alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259                                     # STILL_ACTIVE
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class Job:
    def __init__(self, swarm, phase: str = ""):
        from autorundown.charm import Store
        self.swarm = swarm
        self.store = Store(swarm.root, swarm.layout, swarm.have())
        self.charm = None
        self.fetched = 0
        self.phase = phase

    def progress(self, done, total, rate, peers, uploaded) -> None:
        log(f"pieces {done}/{total}  {rate / 1024:.0f} KB/s  {peers} peers  "
            f"uploaded {uploaded / 1024:.0f} KB")

    def need(self, fn, *args):
        """Run a read; when it needs pieces, fetch them and run it again."""
        while True:
            try:
                return fn(*args)
            except Missing as m:
                if cancelled():
                    raise Cancelled()
                before = len(m.pieces)
                self.swarm.fetch(m.pieces, self.progress, cancelled)
                self.fetched += before
                self.store.have = self.swarm.have()

    def open(self) -> Charm:
        self.charm = self.need(lambda: Charm(self.store))
        return self.charm


def find_car(job: Job, make: str, year: str, car: str, model: str, engine: str) -> str:
    """The CHARM name of the car; exits 2 with candidates if it isn't exact."""
    c = job.charm
    name = car or (f"{model} {engine}".strip() if engine else model)
    if name and job.need(c.pages.get, car_key(make, year, name)) is not None:
        return name
    entry = job.need(c.pages.get, year_key(make, year))
    names: list[str] = []
    if entry is not None:
        listing = job.need(c.pages.get, job.need(c.pages.value, entry))
        if listing is not None:
            names = listing_names(job.need(c.pages.value, listing))
    exact, candidates = match_car(names, model or car, engine)
    if exact:
        return exact
    print("NOT_FOUND " + json.dumps({"make": make, "year": year, "asked": name,
                                     "candidates": candidates, "cars_that_year": len(names)}),
          flush=True)
    if not names:
        log(f"CHARM has no {year} {make}.")
    else:
        log(f"CHARM has no {year} {make} named {name!r}; closest: "
            + "; ".join(candidates[:5]))
    raise SystemExit(2)


def car_text(job: Job, key: bytes):
    """paths -> article key, and article key -> leaf entry, for one car."""
    c = job.charm
    end = key[:-1] + b"0"            # "/" + 1: everything under key/
    paths: dict[bytes, bytes] = {}

    def scan_paths():
        paths.clear()
        for k, v in c.pages.scan(key, end):
            paths[k] = c.pages.value(v)
    job.need(scan_paths)
    arts: dict[bytes, tuple] = {}
    prefixes = sorted({a.split(b",")[0] + b"," for a in paths.values()
                       if not a.startswith(SPECIAL) and b"," in a})
    for pre in prefixes:
        def scan_arts():
            for k, v in c.pages.scan(pre, pre[:-1] + b"-"):   # "," + 1
                arts[k] = v
        job.need(scan_arts)
    for a in {a for a in paths.values() if a.startswith(SPECIAL)}:
        e = job.need(c.pages.get, a)
        if e is not None:
            arts[a] = e
    return paths, arts


def image_pieces(job: Job, keys: list[bytes]) -> set[int]:
    """The pieces of the leaves holding these images (each image's data was
    measured to sit beside its leaf), read from branch pages only."""
    c = job.charm

    def every_key():
        # all keys in one pass, so a level's missing pages are fetched together:
        # one key at a time waited for one piece per key (hours for a car)
        out: set[int] = set()
        need: set[int] = set()
        for k in sorted(keys):
            try:
                out |= c.images.children_pieces(k, k + b"\0", 0)
            except Missing as m:
                need.update(m.pieces)
        if need:
            raise Missing(need)
        return out
    return job.need(every_key)


def doc_id_of(make: str, year: str, car: str) -> str:
    return _slug(f"{year}-{make}-{car}")


def in_index(index: str, doc: str) -> bool:
    p = Path(index)
    if not p.is_file():
        return False
    try:
        con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
        try:
            return con.execute("SELECT 1 FROM vehicles WHERE doc_id=?", (doc,)).fetchone() is not None
        finally:
            con.close()
    except sqlite3.Error:
        return False


def export(job: Job, out_dir: Path, make: str, year: str, car: str, paths, arts,
           images: dict[bytes, str]) -> int:
    c = job.charm
    pdir = out_dir / "pages"
    pdir.mkdir(parents=True, exist_ok=True)
    key = car_key(make, year, car)
    order = [key] + sorted(k for k in paths if k != key)
    numbers = {k.decode(): i + 1 for i, k in enumerate(order)}
    written = 0
    for k in order:
        a = paths.get(k)
        e = arts.get(a) if a else None
        body = job.need(c.pages.value, e) if e else b""
        if body.startswith(SPECIAL):      # a listing page whose own text is another key
            e2 = arts.get(body) or job.need(c.pages.get, body)
            body = job.need(c.pages.value, e2) if e2 else b""
        parts = [unquote(p) for p in k.decode().strip("/").split("/")]
        n = numbers[k.decode()]
        (pdir / f"{n}.html").write_text(page_html(parts, numbers, body, images, n),
                                        encoding="utf-8")
        written += 1
    (out_dir / "index.html").write_text(
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>'
        '<a href="pages/1.html">Open the manual</a><p>From Operation CHARM (charm.li), '
        'downloaded by AutoRundown.</p></body></html>', encoding="utf-8")
    mark(out_dir, make, year, car, complete=True)
    return written


def mark(out_dir: Path, make: str, year: str, car: str, complete: bool) -> None:
    """Marks the folder as ours from the moment it exists: removing the car
    from the index deletes it (recycle.downloaded_car), and a later try may
    replace an unfinished one -- never a folder without the mark."""
    (out_dir / DOWNLOAD_MARK).write_text(json.dumps(
        {"source": "Operation CHARM", "make": make, "year": year, "car": car,
         "complete": complete, "when": time.strftime("%Y-%m-%d %H:%M:%S")}), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--make", required=True)
    ap.add_argument("--year", required=True)
    ap.add_argument("--car", default="", help="CHARM's own name for the car")
    ap.add_argument("--model", default="")
    ap.add_argument("--engine", default="")
    ap.add_argument("--plan", action="store_true", help="size the job only")
    ap.add_argument("--text-only", action="store_true")
    ap.add_argument("--torrent", default="", help="operation-charm.torrent")
    ap.add_argument("--local-source", default="", help="a complete copy (tests, dev.py)")
    ap.add_argument("--work", default=str(config.APP_DIR / "charm_download"))
    ap.add_argument("--out", default=str(config.APP_DIR / "manuals"))
    ap.add_argument("--index", default="")
    ap.add_argument("--keep-data", action="store_true",
                    help="keep the torrent data afterwards (tests)")
    a = ap.parse_args()
    if not (a.car or a.model):
        ap.error("give --car, or --model (and --engine)")
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    if a.local_source:
        swarm = LocalSwarm(Path(a.local_source), work)
    elif a.torrent:
        log("== CHARM: opening the torrent (checking the pieces already on disk)")
        swarm = TorrentSwarm(Path(a.torrent), work, log)
    else:
        log("STOP: no operation-charm.torrent set (Shop setup > LEMON tab)")
        return 1
    job = Job(swarm)
    try:
        return run(a, job, work)
    except Cancelled:
        log("cancelled; the pieces fetched so far are kept for the next try")
        return 3
    except BadData as exc:
        log(f"STOP: the CHARM data is not what was expected ({exc}); nothing was imported")
        return 1
    finally:
        swarm.close()
        log("torrent session closed")


def run(a, job: Job, work: Path) -> int:
    log("== CHARM: reading the database index")
    job.open()
    make, year = a.make, str(a.year)
    car = find_car(job, make, year, a.car, a.model, a.engine)
    doc = doc_id_of(make, year, car)
    log(f"car: {year} {make} {car}  (doc_id {doc})")
    if a.index and in_index(a.index, doc):
        log(f"STOP: {year} {make} {car} is already in the index; remove it first to download it again")
        return 5
    log("== CHARM: the car's pages")
    paths, arts = car_text(job, car_key(make, year, car))
    if not paths:
        log("STOP: CHARM lists the car but has no pages for it")
        return 1
    c = job.charm
    over = set()
    for e in arts.values():
        over |= c.pages.value_pieces(e)
    over -= job.swarm.have()
    log(f"{len(paths):,} pages, {len(arts):,} articles; text needs {len(over)} more pieces")
    job.swarm.fetch(over, job.progress, cancelled)
    job.fetched += len(over)
    job.store.have = job.swarm.have()
    keys: set[bytes] = set()
    text_bytes = 0
    for e in arts.values():
        body = job.need(c.pages.value, e)
        text_bytes += len(body)
        keys.update(image_refs(body))
    log(f"text {text_bytes / 2**20:.1f} MB; {len(keys):,} images")
    pl = job.swarm.layout.piece_length
    if a.text_only:
        img_pieces: set[int] = set()
    else:
        log("== CHARM: the image index")
        img_pieces = image_pieces(job, list(keys))
    have = job.swarm.have()
    todo = img_pieces - have
    # the torrent's 385 all-zero pieces match the sparse files with nothing
    # fetched and take no disk: count only the real ones
    have -= job.swarm.zero_pieces()
    plan = {
        "car": car, "make": make, "year": year, "doc_id": doc,
        "pages": len(paths), "articles": len(arts), "images": 0 if a.text_only else len(keys),
        "text_only": a.text_only,
        "fetched_pieces": len(have), "fetched_bytes": len(have) * pl,
        "image_pieces": len(img_pieces), "download_bytes": len(todo) * pl,
        # the torrent data is on disk until the end, then deleted
        "disk_needed_bytes": (len(have) + len(todo)) * pl + text_bytes * 3
                             + (0 if a.text_only else len(keys) * AVG_IMAGE),
        "stays_bytes": text_bytes * 3 + (0 if a.text_only else len(keys) * AVG_IMAGE),
        # AUTORUNDOWN_CHARM_FREE: a test's pretend free space
        "free_bytes": int(os.environ.get("AUTORUNDOWN_CHARM_FREE") or shutil.disk_usage(work).free),
    }
    (work / f"plan-{doc}.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")
    print("PLAN " + json.dumps(plan), flush=True)
    if a.plan:
        log(f"plan: {len(todo)} more pieces ({plan['download_bytes'] / 2**30:.1f} GB) to download; "
            f"{plan['disk_needed_bytes'] / 2**30:.1f} GB of disk needed; "
            f"{plan['stays_bytes'] / 2**20:.0f} MB stays")
        return 0
    more = plan["download_bytes"] + plan["stays_bytes"]
    if plan["free_bytes"] < more:
        log(f"STOP: not enough free space: {more / 2**30:.1f} GB more needed, "
            f"{plan['free_bytes'] / 2**30:.1f} GB free")
        return 4
    out_dir = Path(a.out) / doc
    if out_dir.exists():
        if not (out_dir / DOWNLOAD_MARK).is_file():
            log(f"STOP: {out_dir} exists and was not written by this downloader; leaving it alone")
            return 1
        shutil.rmtree(out_dir)           # an earlier, unfinished try of ours
    out_dir.mkdir(parents=True)
    mark(out_dir, make, year, car, complete=False)
    images: dict[bytes, str] = {}
    if not a.text_only:
        log(f"== CHARM: images ({len(todo)} pieces)")
        todo_list = sorted(todo)
        for i in range(0, len(todo_list), IMAGE_BATCH):
            job.swarm.fetch(todo_list[i:i + IMAGE_BATCH], job.progress, cancelled)
            log(f"pieces {min(i + IMAGE_BATCH, len(todo_list))}/{len(todo_list)}")
        job.store.have = job.swarm.have()
        missing = 0
        for n, k in enumerate(sorted(keys)):
            e = job.need(c.images.get, k)
            if e is None:
                missing += 1
                continue
            data = job.need(c.images.value, e)
            ext = image_ext(data)
            if not ext:
                missing += 1
                continue
            rel = image_file(k, ext)
            dst = out_dir / "images" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(data)
            images[k] = rel
            if n % 500 == 0:
                log(f"images {n + 1}/{len(keys)}")
        log(f"{len(images):,} images written" + (f", {missing} not in CHARM" if missing else ""))
    log("== CHARM: writing the manual folder")
    n = export(job, out_dir, make, year, car, paths, arts, images)
    log(f"wrote {n:,} pages to {out_dir}")
    if not a.keep_data:
        job.swarm.close()
        job.store.close()                # Windows won't delete a file still open
        data = Path(job.swarm.root)
        shutil.rmtree(data, ignore_errors=True)
        for f in ("resume.dat", "local_have.json", f"plan-{doc}.json"):
            (work / f).unlink(missing_ok=True)
        if data.exists():
            log(f"! could not delete all of the torrent data in {data}; delete it by hand")
        else:
            log("torrent data deleted; the car's folder stays")
    print("CHARM_DONE " + json.dumps({"doc_id": doc, "folder": str(out_dir)}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
