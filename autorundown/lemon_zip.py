"""A LEMON car's "Download .zip for offline use", saved by the user from
LEMON's site in their own browser, imported by "Add this car".

The app never fetches from LEMON (the site asks not to be scraped): it opens
the car's page and then looks for the zip in the Downloads folder, under the
name LEMON gives it (lemon_catalogue.zip_name). A zip is untrusted data: it
is unpacked only if it holds one folder of plain relative paths within size
limits, into <app dir>/manuals/<doc_id>/ with DOWNLOAD_MARK -- the same rule
as a CHARM car: removing the car deletes that folder. The zip itself is the
user's file and stays where it is.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import time
import zipfile
from pathlib import Path, PurePosixPath

from .recycle import DOWNLOAD_MARK

MAX_ENTRIES = 1_000_000
MAX_BYTES = 40 << 30            # the F-150's zip unpacks to ~700 MB; 40 GB is no car
MAX_RATIO = 1000                # one entry may not inflate more than this
CHUNK = 1 << 20


class BadZip(Exception):
    pass


def downloads_dir() -> Path:
    """AUTORUNDOWN_DOWNLOADS (tests, the test boxes), else ~/Downloads."""
    d = os.environ.get("AUTORUNDOWN_DOWNLOADS")
    return Path(d) if d else Path.home() / "Downloads"


def find(name: str, folder: Path | None = None) -> Path | None:
    """The newest finished download of `name` ("x.zip", or the "x (1).zip"
    a browser makes when one is already there); None while it is still
    downloading (a browser writes .crdownload / .part and renames at the
    end) or when the file is not a complete zip yet."""
    folder = folder or downloads_dir()
    stem = name[:-4] if name.lower().endswith(".zip") else name
    # a character Windows can't hold in a file name ("4WD/AWD") is saved as
    # something else ("_" in Chrome and Edge, "-" or a space elsewhere)
    pat = re.compile("".join("." if c in '/\\:*?"<>|' else re.escape(c) for c in stem)
                     + r"( \(\d+\))?\.zip", re.I)
    try:
        hits = [p for p in folder.iterdir() if pat.fullmatch(p.name) and p.is_file()]
    except OSError:
        return None
    for p in sorted(hits, key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with zipfile.ZipFile(p) as z:       # the central directory is written last
                if z.infolist():
                    return p
        except (zipfile.BadZipFile, OSError):
            continue
    return None


def _parts(name: str) -> tuple[str, ...]:
    if "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise BadZip(f"an unsafe path in the zip: {name!r}")
    parts = PurePosixPath(name).parts
    if any(p in ("..", ".") for p in parts):
        raise BadZip(f"an unsafe path in the zip: {name!r}")
    if any(ord(c) < 32 or c in '<>:"|?*' for p in parts for c in p):
        raise BadZip(f"an unsafe file name in the zip: {name!r}")
    return parts


def check(path: Path) -> tuple[str, list[zipfile.ZipInfo], int]:
    """(its one top folder, its files, their unpacked size) -- or BadZip."""
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise BadZip(f"not a zip file: {e}")
    with z:
        infos = z.infolist()
    if not infos or len(infos) > MAX_ENTRIES:
        raise BadZip(f"{len(infos):,} entries in the zip")
    tops, files, total = set(), [], 0
    for i in infos:
        parts = _parts(i.filename)
        tops.add(parts[0])
        if i.is_dir():
            continue
        if len(parts) < 2:
            raise BadZip(f"a file outside the car's folder: {i.filename!r}")
        if stat.S_ISLNK(i.external_attr >> 16):
            raise BadZip(f"a link in the zip: {i.filename!r}")
        if i.file_size > CHUNK and i.file_size > MAX_RATIO * max(i.compress_size, 1):
            raise BadZip(f"{i.filename!r} inflates {i.file_size // max(i.compress_size, 1)}x")
        total += i.file_size
        files.append(i)
    if len(tops) != 1:
        raise BadZip(f"{len(tops)} folders at the top of the zip, not one car")
    if total > MAX_BYTES:
        raise BadZip(f"{total / 1e9:.1f} GB unpacked")
    if not (files and any(PurePosixPath(i.filename).parts[1:] == ("index.html",) for i in files)):
        raise BadZip("no index.html in the car's folder: not a LEMON manual")
    return tops.pop(), files, total


def mark(dest: Path, zip_path: Path, complete: bool) -> None:
    (dest / DOWNLOAD_MARK).write_text(json.dumps(
        {"source": "lemon-zip", "zip": zip_path.name, "complete": complete,
         "when": time.strftime("%Y-%m-%d %H:%M:%S")}), encoding="utf-8")


def unpack(zip_path: Path, dest: Path, progress=None) -> int:
    """Unpack the car's folder's contents into `dest` (made here, or one an
    earlier try marked); returns the number of files."""
    top, files, total = check(zip_path)
    if dest.exists() and not (dest / DOWNLOAD_MARK).is_file():
        raise BadZip(f"{dest} exists and was not written by AutoRundown: left alone")
    free = shutil.disk_usage(dest.parent if dest.parent.exists() else Path(dest.anchor)).free
    if total > free - (1 << 30):
        raise BadZip(f"needs {total / 1e9:.1f} GB, {free / 1e9:.1f} GB free")
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    mark(dest, zip_path, complete=False)
    root = dest.resolve()
    with zipfile.ZipFile(zip_path) as z:
        for n, i in enumerate(files, 1):
            out = root.joinpath(*_parts(i.filename)[1:])
            if root not in out.resolve().parents:
                raise BadZip(f"{i.filename!r} leaves the folder")
            out.parent.mkdir(parents=True, exist_ok=True)
            left = i.file_size
            with z.open(i) as src, open(out, "wb") as dst:
                while chunk := src.read(CHUNK):
                    left -= len(chunk)
                    if left < 0:              # more than the zip said: stop
                        raise BadZip(f"{i.filename!r} is larger than the zip says")
                    dst.write(chunk)
            if progress and (n % 500 == 0 or n == len(files)):
                progress(n, len(files))
    mark(dest, zip_path, complete=True)
    return len(files)
