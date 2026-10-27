"""Send a folder to the Recycle Bin (Windows) or the Trash -- the "Remove all
Mercedes WIS / EPC data" buttons' extracted files (the user, 2026-10-23: "if
i click a delete button on a local site, it should delete the data
completly"; the Recycle Bin, not a permanent delete, was their choice).

Only names in EXTRACT_NAMES are ever accepted, and a car AutoRundown
downloaded itself (charm_download.py: a folder directly in
<app dir>/manuals holding DOWNLOAD_MARK) -- the agreed LEMON/CHARM rule:
removing a downloaded car deletes its folder. Whatever path a caller
computes, nothing else -- a manual folder of the user's own, the index's
folder, a home directory -- can be thrown away by this.

AUTORUNDOWN_RECYCLE_TO=<folder> moves into that folder instead (tests, and
the Linux test boxes), so a test never fills the PC's Recycle Bin.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

# what the Mercedes extractor writes under autorundown_out
# (extractor/vm_pipeline.py and its pull/parse scripts)
WIS_DIRS = ("wis_extract", "wis_applic", "wis_applic2", "wis_vehicles", "wis_images", "wis_links")
EPC_DIRS = ("epc_extract",)
EXTRACT_NAMES = frozenset(WIS_DIRS + EPC_DIRS)
DOWNLOAD_MARK = ".autorundown-download"


def downloaded_car(path: Path) -> bool:
    """A car folder charm_download.py wrote: <app dir>/manuals/<doc_id>/."""
    from . import config
    p = Path(path)
    try:
        return (p.parent.resolve() == (config.APP_DIR / "manuals").resolve()
                and (p / DOWNLOAD_MARK).is_file())
    except OSError:
        return False


def allowed(path: Path, keep: list[Path] = ()) -> str:
    """Why `path` must not be thrown away, or "" when it may."""
    p = Path(path)
    if p.name not in EXTRACT_NAMES and not downloaded_car(p):
        return f"{p} is not an extract folder ({', '.join(sorted(EXTRACT_NAMES))})"
    if not p.is_dir():
        return f"{p} is not a folder"
    rp = p.resolve()
    for k in keep:                      # e.g. the index: never inside what goes
        try:
            Path(k).resolve().relative_to(rp)
            return f"{p} holds {k}"
        except ValueError:
            pass
    return ""


def recycle(path: Path, keep: list[Path] = ()) -> str:
    """Throw `path` away recoverably; returns where it went. Raises
    RuntimeError if refused or if it is still there afterwards."""
    p = Path(path)
    why = allowed(p, keep)
    if why:
        raise RuntimeError(why)
    to = os.environ.get("AUTORUNDOWN_RECYCLE_TO")
    if to:
        dest = Path(to) / f"{p.name}-{time.strftime('%Y%m%d-%H%M%S')}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), str(dest))
        return str(dest)
    if sys.platform == "win32":
        _win_recycle(p)
        where = "the Recycle Bin"
    else:
        where = str(_trash(p))
    if p.exists():
        raise RuntimeError(f"{p} is still there (not moved to {where})")
    return where


def _win_recycle(p: Path) -> None:
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                    ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                    ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
    FO_DELETE = 3
    # ALLOWUNDO = to the Recycle Bin; NOCONFIRMATION / SILENT / NOERRORUI: no
    # dialogs from a server. WANTNUKEWARNING overrides NOCONFIRMATION for one
    # case only: a folder too big for the Recycle Bin would be deleted for
    # good, and Windows asks the person first instead (No leaves it in place).
    flags = 0x0040 | 0x0010 | 0x0004 | 0x0400 | 0x4000
    op = SHFILEOPSTRUCTW(None, FO_DELETE, str(p.resolve()) + "\0\0", None, flags, False, None, None)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc or op.fAnyOperationsAborted:
        raise RuntimeError(f"Windows did not move {p} to the Recycle Bin "
                           f"({'cancelled' if op.fAnyOperationsAborted else f'error {rc:#x}'})")


def _trash(p: Path) -> Path:
    """macOS ~/.Trash; elsewhere the freedesktop.org Trash (files + info)."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    if sys.platform == "darwin":
        dest = Path.home() / ".Trash" / f"{p.name} {stamp}"
        shutil.move(str(p), str(dest))
        return dest.parent
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "Trash"
    name = f"{p.name}-{stamp}"
    (base / "files").mkdir(parents=True, exist_ok=True)
    (base / "info").mkdir(parents=True, exist_ok=True)
    (base / "info" / f"{name}.trashinfo").write_text(
        f"[Trash Info]\nPath={p.resolve()}\nDeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n",
        encoding="utf-8")
    shutil.move(str(p), str(base / "files" / name))
    return base
