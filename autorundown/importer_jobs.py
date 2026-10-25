"""Run an import from the web UI: detect what a folder holds, then run the
same command-line tool a person would, in the background, and keep its log.

The import itself is `ingest.py` (or `rebuild_wis.py`) in a child process --
exactly what the CLI does, so there is one code path for building an index,
and a crash in an import can't take the web server down with it. One job at
a time: two writers on one SQLite index would only slow each other down.
"""
from __future__ import annotations

import collections
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

# The installed app runs without a console (pythonw), so every console
# program a job starts -- Python, and under it VBoxManage and Java -- opened
# a terminal window of its own (the user, 2026-10-23). A hidden console here
# is inherited by everything the job starts in turn. 0 off Windows.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

from . import importers

ROOT = Path(__file__).resolve().parents[1]
FOLDER_SCAN_CAP = 5000   # stop counting files here: a folder import of a whole
                         # drive must not make the preview hang


def can_rebuild_wis(index_path: str | Path) -> bool:
    """A WIS rebuild replaces the WIS already in the index (rebuild_wis.py
    removes it in place, then re-ingests); with none there, it is an add."""
    return manuals(index_path)["wis"]["entries"] > 0


def extractor_candidates() -> list[Path]:
    """Where the WIS extractor is looked for, in order: config.extractor_dir,
    then the copy in this repo (`extractor/`). There is deliberately no
    fallback to the old standalone copy beside a source checkout: two copies
    drift. The installed app (Windows MSI) has no `extractor/` -- it never
    ships, see packaging/windows -- so only there it looks for the user's
    copy on the Desktop."""
    from . import config
    # its Python gets run: never from a network share or a relative path,
    # whatever an older config holds (server.py checks new settings)
    c = config.load().extractor_dir
    ok = c and not c.startswith(("\\\\", "//")) and Path(c).is_absolute()
    found = ([Path(c)] if ok else []) + [ROOT / "extractor"]
    if not (ROOT / "extractor").is_dir():
        sub = Path("Mercedes Documents") / "autorundown_extractor"
        home = Path.home()
        found += [home / "Desktop" / sub, home / "OneDrive" / "Desktop" / sub]
    return found


def find_extractor() -> Path | None:
    """The WIS extractor folder (holds vm_pipeline.py), or None."""
    for c in extractor_candidates():
        if (c / "vm_pipeline.py").is_file():
            return c
    return None


def epc_problem(ext: Path | None) -> str:
    """Why this extractor cannot pull the EPC parts catalogue ("" = it can).
    The installed app uses the user's own copy on the Desktop, which may
    predate EPC: without this the job died on argparse's "unknown step"."""
    if ext is None:
        return "The Mercedes extractor (vm_pipeline.py) was not found."
    missing = [f for f in ("pull_epc.js", "epc_fv4.js") if not (ext / f).is_file()]
    if missing or "epc" not in (ext / "vm_pipeline.py").read_text(encoding="utf-8", errors="replace"):
        return (f"The Mercedes extractor at {ext} is older than the parts-catalogue (EPC) "
                f"import. Copy a current extractor folder over it, or point Shop setup → "
                f"Advanced → Mercedes extractor folder at one.")
    return ""


def extractor_out_root(ext: Path) -> Path:
    """Where that extractor writes (autorundown_out) -- outside the repo.

    Asks the extractor's own wis_paths.py, so the site and the scripts agree;
    an older copy without it wrote beside itself.
    """
    import importlib.util
    paths = ext / "wis_paths.py"
    if not paths.is_file():
        return ext.parent / "autorundown_out"
    spec = importlib.util.spec_from_file_location("_ar_wis_paths", paths)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        return mod.out_root()
    except SystemExit as e:              # an AR_OUT_ROOT inside the repo
        raise RuntimeError(str(e)) from None


# One VirtualBox query at a time: two VBoxManage calls on one VM collide
# ("already has a lock request pending") -- the page asks for the VM's check
# and its snapshots together (seen on the real VM, 2026-10-16)
VBOX_LOCK = threading.RLock()


def list_vms() -> dict:
    """VirtualBox VMs on this PC, via the extractor's own helper."""
    with VBOX_LOCK:
        return _list_vms()


def _list_vms() -> dict:
    ext = find_extractor()
    if not ext:
        return {"extractor": "", "vms": [],
                "error": "The Mercedes extractor (vm_pipeline.py) was not found. Looked in: "
                         + "; ".join(str(c) for c in extractor_candidates())
                         + ". Set its folder under Settings (the gear) -> Mercedes extractor folder."}
    r = subprocess.run([sys.executable, str(ext / "vm_pipeline.py"), "--list-vms"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=60, creationflags=NO_WINDOW)
    if r.returncode != 0:
        return {"extractor": str(ext), "vms": [],
                # a string: the UI showed "(s ?? "").replace is not a function"
                # for the list this used to be
                "error": "\n".join((r.stdout + r.stderr).strip().splitlines()[-3:])
                         or "VirtualBox was not found on this PC."}
    vms = [dict(zip(("name", "state"), l.split("\t"))) for l in r.stdout.splitlines() if "\t" in l]
    try:
        out_root = extractor_out_root(ext)
    except RuntimeError as e:
        return {"extractor": str(ext), "vms": vms, "error": str(e)}
    return {"extractor": str(ext), "vms": vms, "out_root": str(out_root), "chosen": chosen_vm()}


# The VM is chosen as a file, like a folder for an import (the user,
# 2026-10-23: "it should be a file explorer option, where i choose what vm to
# use"), not from VirtualBox's list. The file names the machine; runs still
# address it by that name, so it must be added to VirtualBox -- this never
# registers one (no changes to VirtualBox's settings).
VBOX_MAX = 5_000_000      # a .vbox is a few KB of XML


def vbox_machine(path: Path) -> dict:
    """{"name", "uuid"} of the VM a VirtualBox .vbox file describes."""
    import xml.etree.ElementTree as ET
    if path.suffix.lower() != ".vbox" or not path.is_file():
        raise ValueError(f"{path.name} is not a VirtualBox machine file (.vbox)")
    if path.stat().st_size > VBOX_MAX:
        raise ValueError(f"{path.name} is too big to be a VirtualBox machine file")
    try:
        m = ET.parse(path).getroot().find("{*}Machine")
    except ET.ParseError as exc:
        raise ValueError(f"{path.name} could not be read: {exc}") from None
    if m is None or not m.get("name"):
        raise ValueError(f"{path.name} does not describe a virtual machine")
    return {"name": m.get("name"), "uuid": (m.get("uuid") or "").strip("{}")}


def chosen_vm() -> dict | None:
    """The VM file picked in Shop setup, if it is still there."""
    from . import config
    f = config.load().vm_file
    if not f:
        return None
    try:
        return {"file": f, **vbox_machine(Path(f))}
    except (ValueError, OSError):
        return None


def browse_vbox(path: str) -> dict:
    """A folder's subfolders and .vbox files, for choosing the VM. Starts
    where the chosen file is, else VirtualBox's own "VirtualBox VMs"."""
    from . import config
    if path:
        p = Path(path).expanduser()
        if not p.is_dir():
            raise FileNotFoundError(f"Folder not found: {p}")
    else:
        f = config.load().vm_file
        # AUTORUNDOWN_VMS_DIR: dev.py --fake-vm's pretend machine files
        vms_dir = os.environ.get("AUTORUNDOWN_VMS_DIR") or Path.home() / "VirtualBox VMs"
        p = next((c for c in (Path(f).parent.parent if f else None, Path(vms_dir), Path.home())
                  if c and c.is_dir()), Path.home())
    dirs, files = [], []
    try:
        for c in p.iterdir():
            if c.name.startswith("."):
                continue
            if c.is_dir():
                dirs.append(c.name)
            elif c.suffix.lower() == ".vbox":
                files.append(c.name)
    except OSError:
        pass
    return {"path": str(p), "parent": str(p.parent) if p.parent != p else "",
            "dirs": sorted(dirs, key=str.lower)[:500], "files": sorted(files, key=str.lower)}


def pick_vm(file: str) -> dict:
    """Remember the VM file the user chose, once VirtualBox knows its VM."""
    from . import config
    m = vbox_machine(Path(file))
    info = list_vms()
    if info.get("error") and not info.get("vms"):
        raise ValueError(info["error"])
    if m["name"] not in {v["name"] for v in info.get("vms", [])}:
        raise ValueError(f"VirtualBox doesn't have the VM {m['name']!r} added yet. Open VirtualBox, "
                         f"use Machine > Add… and choose this file, then choose it here again.")
    cfg = config.load()
    cfg.vm_file = str(Path(file))
    cfg.save()
    return {"file": cfg.vm_file, **m}


VM_STEPS ={"wis": "docs,applic,vehicles,images,build", "epc": "epc",
            "both": "docs,applic,vehicles,images,build,epc"}


def parse_checks(text: str) -> list[dict]:
    """The pipeline's `CHECK <key> <level> <message>` lines."""
    out = []
    for line in text.splitlines():
        p = line.split("\t", 3)
        if len(p) == 4 and p[0] == "CHECK" and p[2] in ("ok", "warn", "fail"):
            out.append({"key": p[1], "level": p[2], "message": p[3]})
    return out


def check_vm(vm: str, steps: str) -> dict:
    with VBOX_LOCK:
        return _check_vm(vm, steps)


def _check_vm(vm: str, steps: str) -> dict:
    """Will a run on this VM work? The extractor's own `--check` (no boot):
    the VM, its network, Java, the JDBC driver, the scripts the steps need,
    disk space. An extractor too old to have --check says so as a failure --
    it is also the one without the EPC puller and the safe shutdown."""
    info = list_vms()
    if info.get("error"):
        return {"ready": False, "checks": [{"key": "extractor", "level": "fail", "message": str(info["error"])}]}
    if vm not in {v["name"] for v in info["vms"]}:
        return {"ready": False, "checks": [{"key": "vm", "level": "fail",
                                            "message": f"There is no VirtualBox VM named {vm!r}."}]}
    ext = Path(info["extractor"])
    try:
        r = subprocess.run([sys.executable, str(ext / "vm_pipeline.py"), "--vm", vm, "--steps", steps,
                            "--out-root", str(info["out_root"]), "--check"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"}, creationflags=NO_WINDOW)
        checks = parse_checks(r.stdout)
    except subprocess.TimeoutExpired:
        checks = [{"key": "extractor", "level": "fail", "message": "The check took over 90 s; is VirtualBox busy?"}]
    if not checks:
        checks = [{"key": "extractor", "level": "fail",
                   "message": f"The Mercedes extractor at {ext} is too old to check a VM before a run "
                              f"(and to stop one safely). Copy a current extractor folder over it, or "
                              f"point Shop setup → Advanced → Mercedes extractor folder at one."}]
    return {"ready": all(c["level"] != "fail" for c in checks), "checks": checks,
            "out_root": info.get("out_root", "")}


def list_snapshots(vm: str) -> dict:
    with VBOX_LOCK:
        return _list_snapshots(vm)


def _list_snapshots(vm: str) -> dict:
    """The VM's snapshots (vm_pipeline.py --list-snapshots), oldest first:
    which are ours (taken before a run, or before a rollback) and which is
    the current one."""
    info = list_vms()
    if info.get("error"):
        return {"snapshots": [], "error": str(info["error"])}
    if vm not in {v["name"] for v in info["vms"]}:
        return {"snapshots": [], "error": f"There is no VirtualBox VM named {vm!r}."}
    r = subprocess.run([sys.executable, str(Path(info["extractor"]) / "vm_pipeline.py"), "--vm", vm,
                        "--list-snapshots"], capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=60, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                       creationflags=NO_WINDOW)
    snaps = []
    for line in r.stdout.splitlines():
        p = line.split("\t")
        if len(p) >= 5 and p[0] == "SNAP" and p[1]:
            snaps.append({"name": p[1], "ours": p[2] == "ours", "current": p[3] == "current",
                          "description": p[4]})
    if r.returncode != 0 or (not snaps and "SNAP" not in r.stdout and r.stdout.strip()):
        return {"snapshots": snaps, "error": "This extractor copy can't list snapshots; update it."}
    return {"snapshots": snaps}


def describe(path: Path, index_path: str | Path) -> dict:
    """What an import of `path` would do, cheaply -- no full scan."""
    try:
        if not path.is_dir():
            return {"importer": "", "summary": "Not a folder."}
        # a corpus folder names itself; don't let the other detectors walk its
        # images/ (272k files on the WIS corpus took 15 s)
        imp = (importers.get("records")
               if (path / "manifest.json").is_file() and (path / "records.jsonl").is_file()
               else importers.autodetect(path))
    except Exception as exc:  # a broken folder must not break browsing
        return {"importer": "", "summary": f"Could not read this folder: {exc}"}
    name = imp.name
    if name == "records":
        corp = imp._corpus_dirs(path)
        bits, types = [], []
        for cdir in corp[:3]:
            try:
                m = json.loads((cdir / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            img = m.get("images") or {}
            types.append(m.get("source_type") or "")
            bits.append(
                f"{m.get('source_type', 'records')} ({(m.get('language') or {}).get('iso', '?')}), "
                f"{m.get('record_count', '?'):,} documents, schema {m.get('schema_version', '?')}"
                + (f", {img.get('converted', 0):,} images" if img else ""))
        # rebuild only from a WIS corpus: rebuild_wis.py refuses any other
        wis = bool(types) and all(t.startswith("mercedes-wis") for t in types)
        return {"importer": name, "rebuildable": wis and can_rebuild_wis(index_path),
                "summary": "Normalized corpus: " + ("; ".join(bits) or "found")}
    if name == "lemon":
        n = len(imp._vehicle_dirs(path))
        return {"importer": name, "summary": f"HTML manual archive: {n} vehicle(s)"}
    n = 0
    for p in path.rglob("*"):
        if p.is_file() and p.suffix.lower() in (".html", ".htm", ".txt", ".pdf"):
            n += 1
            if n >= FOLDER_SCAN_CAP:
                break
    if not n:
        return {"importer": "", "summary": "No manuals found here (HTML, text or PDF)."}
    return {"importer": "folder",
            "summary": f"Generic folder: {n:,}{'+' if n >= FOLDER_SCAN_CAP else ''} "
                       f"HTML / text / PDF files"}


def lemon_vehicles(path: Path, index_path: str | Path) -> dict:
    """The LEMON manuals in `path`, one row per vehicle folder, and whether
    each is already in the index -- what the LEMON tab's folder section lists.

    Reads each vehicle's pages/1.html for its name (the same breadcrumb the
    importer files it under), so the doc_id here is the one ingest.py uses.
    The index is opened read-only: listing must never write to it.
    """
    from .importers.lemon import LemonImporter
    if not path.is_dir():
        return {"path": str(path), "vehicles": [], "summary": "Not a folder."}
    try:
        found = list(LemonImporter().discover(path))
    except Exception as exc:  # a broken folder must not break browsing
        return {"path": str(path), "vehicles": [], "summary": f"Could not read this folder: {exc}"}
    have: dict[str, tuple[int, int]] = {}
    if found and Path(index_path).is_file():
        import sqlite3
        try:
            con = sqlite3.connect(f"file:{Path(index_path).as_posix()}?mode=ro", uri=True)
            try:
                have = {d: (n or 0, c or 0) for d, n, c in con.execute(
                    "SELECT doc_id, indexed_pages, complete FROM vehicles")}
            finally:
                con.close()
        except sqlite3.Error:
            have = {}
    rows = []
    for v in found:
        n, complete = have.get(v.doc_id, (0, 0))
        try:
            rel = Path(v.file_path).relative_to(path).as_posix()
        except ValueError:
            rel = v.file_path
        rows.append({"folder": rel, "doc_id": v.doc_id, "label": v.label,
                     "pages": v.page_count, "indexed_pages": n,
                     "state": "imported" if complete else "partial" if v.doc_id in have else "new"})
    rows.sort(key=lambda r: r["label"].lower())
    new = sum(r["state"] != "imported" for r in rows)
    summary = (f"{len(rows)} LEMON vehicle(s), {len(rows) - new} already imported"
               if rows else "No LEMON manuals here (a vehicle folder holds index.html and pages/1.html).")
    return {"path": str(path), "vehicles": rows, "summary": summary}


def manuals(index_path: str | Path) -> dict:
    """What is in the index, grouped the way the Import dialog's tabs remove
    it: LEMON and folder manuals one by one, Mercedes WIS as a whole
    (remove_manuals.py says why). Read-only."""
    out: dict = {"lemon": [], "folder": [], "wis": {"entries": 0, "pages": 0, "sources": []},
                 "epc": {"entries": 0, "pages": 0}}
    p = Path(index_path)
    if not p.is_file():
        return out
    import sqlite3
    try:
        con = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
        try:
            rows = con.execute("SELECT doc_id, source_name, year_range, make, model, file_path, "
                               "indexed_pages, complete FROM vehicles").fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return out
    wis_sources = set()
    corpus = {"wis": set(), "epc": set()}
    for d, src, year, make, model, fp, n, complete in rows:
        src = src or ""
        if src.startswith("mercedes-wis"):
            out["wis"]["entries"] += 1
            out["wis"]["pages"] += n or 0
            wis_sources.add(src)
            if fp:
                corpus["wis"].add(fp)
            continue
        if src.startswith("mercedes-epc"):
            # the EPC parts catalogue: one corpus, replaced and removed whole
            out["epc"]["entries"] += 1
            out["epc"]["pages"] += n or 0
            if fp:
                corpus["epc"].add(fp)
            continue
        group = "lemon" if src == "lemon" else "folder"
        label = " ".join(x for x in (year, make, model) if x) or d
        out[group].append({"doc_id": d, "label": label, "source": src, "pages": n or 0,
                           "file_path": fp or "", "complete": bool(complete)})
    for g in ("lemon", "folder"):
        out[g].sort(key=lambda r: r["label"].lower())
    out["wis"]["sources"] = sorted(wis_sources)
    for k in ("wis", "epc"):
        out[k]["folders"] = [str(f) for f in extract_dirs(k, corpus[k], p)]
    return out


def extract_dirs(kind: str, corpus_dirs, index_path: Path) -> list[Path]:
    """The folders "Remove all Mercedes WIS / EPC data" sends to the Recycle
    Bin: the corpus folder each manual was imported from (vehicles.file_path)
    and, beside a `wis_extract`, the extractor's other WIS outputs. Taken
    from the index, not from the extractor's settings, so a test's throwaway
    corpus can never point at the real autorundown_out. Only extract-folder
    names (recycle.EXTRACT_NAMES) that exist, never one holding the index."""
    from . import recycle
    names = recycle.WIS_DIRS if kind == "wis" else recycle.EPC_DIRS
    out: list[Path] = []
    for c in sorted(corpus_dirs):
        c = Path(c)
        cands = [c.parent / n for n in names] if c.name == names[0] else [c]
        for f in cands:
            if f not in out and not recycle.allowed(f, keep=[index_path]):
                out.append(f)
    return out


_STAMP = re.compile(r"^\[\d\d:\d\d:\d\d\]\s*")
_SCRIPT = re.compile(r"([\w.-]+\.(?:py|js))$")
# what the build step's scripts are doing, in the order vm_pipeline.py runs them
_BUILD = {"parse.py": "Build: reading documents", "apply_applic.py": "Build: model links",
          "parse_vehicles.py": "Build: vehicles", "parse_links.py": "Build: diagram links",
          "convert_images.py": "Build: converting images", "apply_images.py": "Build: placing images"}


class Progress:
    """How far a job has got, read from the lines it logs -- the same lines a
    person watching the log reads. Never invents a total: `total` is set only
    when a script printed one (images "a / b", EPC "catalog (i/N", ingest
    "found N vehicle(s)", ...); otherwise the UI shows the phase and the count
    with an indeterminate bar. A line it doesn't know changes nothing."""

    def __init__(self) -> None:
        self.reset("Starting")

    def reset(self, phase: str) -> None:
        self.phase, self.done, self.total, self.unit = phase, None, None, ""
        self.detail = self.activity = self.error = ""
        self._base = 0           # images pulled before the running batch
        self._fresh_total = None  # pull.js: only a fresh pull has a real total
        self._idx = 0            # ingest.py: vehicles started

    def as_dict(self) -> dict:
        return {"phase": self.phase, "done": self.done, "total": self.total, "unit": self.unit,
                "detail": self.detail, "activity": self.activity, "error": self.error}

    def _set(self, done, total=None, unit=None) -> None:
        self.done = int(done)
        if total is not None:
            self.total = int(total) or None
        if unit is not None:
            self.unit = unit
        if self.total and self.done > self.total:
            self.done = self.total

    def feed(self, raw: str) -> None:
        try:
            self._feed(raw)
        except Exception:            # progress is a nicety; it must never break a job
            pass

    def _feed(self, raw: str) -> None:
        line = raw.strip()
        while _STAMP.match(line):
            line = _STAMP.sub("", line, count=1)
        if not line:
            return
        num = lambda s: int(s.replace(",", ""))      # noqa: E731
        if line.startswith("$ "):
            toks = line[2:].split()
            script = next((m.group(1) for t in toks if (m := _SCRIPT.search(t))), "")
            if script in _BUILD:
                self.reset(_BUILD[script])
            elif script == "vm_pipeline.py":
                self.reset("Starting the VM")
            elif script == "rebuild_wis.py":
                self.reset("Rebuilding the Mercedes index")
            elif script == "ingest.py":
                self.reset("Adding to the index")
            elif script == "remove_manuals.py":
                self.reset("Removing the old data from the index")
            else:                       # a pull starting (java ... pull.js): the VM is up
                self.activity = ""
            return
        if line.startswith("STOP: "):
            self.error = line[6:]
            return
        if line.startswith("starting ") and line.endswith(" headless"):
            self.activity = "starting the VM"
        elif line.startswith("database answering"):
            self.activity = "database up"
        elif line.startswith("database not answering") or line.startswith("database never came up"):
            self.activity = "waiting for the database"
        elif line.startswith("shutting ") and line.endswith(" down"):
            self.activity = "shutting the VM down"
        elif "power-cycling and resuming" in line:
            self.activity = "connection dropped; restarting the VM and resuming"
        elif line.startswith("== documents"):
            self.reset("Documents")
            self.unit = "documents"
        elif line.startswith("== model applicability"):
            self.reset("Model links")
        elif line.startswith("== vehicle names"):
            self.reset("Vehicles & VIN tables")
            self.unit = "tables"
        elif line.startswith("== diagram links"):
            self.reset("Diagram links")
        elif m := re.match(r"== images: ([\d,]+) graphics, ([\d,]+) already pulled", line):
            self.reset("Images")
            self._base = num(m.group(2))
            self._set(self._base, num(m.group(1)), "graphics")
        elif line.startswith("== EPC parts catalogue"):
            self.reset("Parts catalogues")
            self.unit = "catalogs"
        elif line.startswith("== offline build"):
            self.reset("Building the corpus")
        # -- pull.js (documents)
        elif m := re.match(r"PULL start .*\((fresh|resume)\)", line):
            self.detail = "fresh pull" if m.group(1) == "fresh" else "resuming"
        elif m := re.match(r"total docs for lang \S+ = (\d+)", line):
            self._fresh_total = int(m.group(1))     # printed only on a fresh pull
            self._set(0, self._fresh_total)
            self.detail = ""
        elif m := re.match(r"progress: (\d+) pulled", line):
            self._set(m.group(1))
            if not self._fresh_total:               # a resume: pulled this run, of an unknown rest
                self.detail = "this run (resumed)"
        elif m := re.match(r"PULL done: pulled=(\d+)", line):
            self._set(m.group(1))
        # -- applicability / vehicle tables
        elif m := re.match(r"(W_Baumusterzeile|W_Dok2Reihe) rows = (\d+)", line):
            self.detail = f"{m.group(1)}: {int(m.group(2)):,} rows"
        elif m := re.match(r"dok2reihe (\d+)\.\.\.", line):
            self.detail = f"W_Dok2Reihe: {int(m.group(1)):,} rows so far"
        elif m := re.match(r"(\w+) rows = (\d+)", line):
            self._set((self.done or 0) + 1, unit="tables")
            self.detail = f"{m.group(1)}: {int(m.group(2)):,} rows"
        # -- diagram links
        elif m := re.match(r"batch (\d+)/(\d+)\s", line + " "):
            self._set(m.group(1), m.group(2), "batches")
        # -- images: vm_pipeline's per-batch line, pull_images.js within a batch
        elif m := re.match(r"batch: \+[\d,]+ -> ([\d,]+) / ([\d,]+)", line):
            self._base = num(m.group(1))
            self._set(self._base, num(m.group(2)), "graphics")
        elif m := re.match(r"progress: row \d+/\d+ ok=(\d+)", line):
            if self.total:
                self._set(self._base + int(m.group(1)))
        # -- pull_epc.js
        elif m := re.match(r"catalogs to pull: (\d+)", line):
            self._set(0, m.group(1), "catalogs")
        elif m := re.match(r"resume: (\d+) catalogs already done", line):
            self.detail = f"{int(m.group(1)):,} catalogs done in an earlier run"
        elif m := re.match(r"catalog (\S+) \((\d+)/(\d+)", line):
            self._set(m.group(2), m.group(3), "catalogs")
            self.detail = f"catalog {m.group(1)}"
        elif m := re.match(r"SA options: (\d+)", line):
            self.reset("Option codes")
            self._set(0, m.group(1), "options")
        elif m := re.match(r"SA (\d+)/(\d+):", line):
            self._set(m.group(1), m.group(2), "options")
        elif m := re.match(r"DONE: (\d+) records", line):
            self.detail = f"{int(m.group(1)):,} records"
        # -- build scripts
        elif m := re.match(r"(\d+) docs$", line):
            self._set(0, m.group(1), "documents")
        elif m := re.match(r"parsed (\d+)\.\.\.", line):
            self._set(m.group(1))
        elif m := re.match(r"(\d+) already converted, (\d+) to do", line):
            self._set(0, m.group(2), "images")
        elif m := re.match(r"(\d+)/(\d+)\s+\d+ failed", line):
            self._set(m.group(1), m.group(2), "images")
        elif line.startswith(("corpus ready:", "EPC corpus ready:")):
            self.detail = "corpus ready"
        # -- ingest.py / rebuild_wis.py
        elif m := re.match(r"found (\d+) vehicle\(s\)", line):
            self._set(0, m.group(1), "vehicles")
        elif m := re.match(r"=== (.+) ===$", line):
            self._idx += 1
            self._set(self._idx - 1)
            self.detail = m.group(1)
        elif m := re.match(r"done: \d+ new pages", line):
            self._set(self._idx)
        elif m := re.match(r"(\d+) pages\s+\d+ chunks", line):
            self.activity = f"{int(m.group(1)):,} pages"
        elif m := re.match(r"usage log restored", line):
            self.detail = "spend log kept"
        # -- remove_manuals.py
        elif m := re.match(r"removing: (\d+) manual", line):
            self._set(0, m.group(1), "entries")
        elif m := re.match(r"\[(\d+)/(\d+)\]", line):
            self._set(m.group(1), m.group(2), "entries")


class ImportJobs:
    # seconds between the two parts of a "both" run: the WIS part ends with
    # rebuilding the index (heavy disk), and this PC has crashed under
    # sustained I/O -- vm_pipeline.py pauses 60 s between heavy runs too.
    # `dev.py --fake-vm` shortens it (AUTORUNDOWN_PART_PAUSE) for test runs.
    PART_PAUSE = float(os.environ.get("AUTORUNDOWN_PART_PAUSE") or 60)

    # a cancelled extractor gets this long to stop its pull and shut the VM
    # down cleanly before it is ended (the VM may then still be running)
    CANCEL_GRACE = float(os.environ.get("AUTORUNDOWN_CANCEL_GRACE") or 900)

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._job: dict | None = None
        self._log: collections.deque[str] = collections.deque(maxlen=400)
        self._progress = Progress()
        self._proc: subprocess.Popen | None = None
        self._cancel_at = 0.0

    @staticmethod
    def cancel_file() -> Path:
        from . import config
        return config.APP_DIR / "import_cancel"

    def cancel(self) -> dict:
        """Stop the running job. The extractor sees the cancel file, stops its
        pull and shuts the VM down cleanly; an index step (ingest, rebuild,
        remove) is left to finish -- ending one half-way could leave the
        index without the manuals it was replacing -- and nothing after it
        starts."""
        with self._lock:
            if not self._job or self._job["state"] != "running":
                raise RuntimeError("Nothing is running.")
            self._job["cancelling"] = True
            self._cancel_at = time.time()
        try:
            self.cancel_file().parent.mkdir(parents=True, exist_ok=True)
            self.cancel_file().write_text("cancel\n", encoding="utf-8")
        except OSError:
            pass
        self._note("== cancel asked: stopping after the current step (the VM is shut down cleanly)")
        return self.status()

    def _cancelled(self) -> bool:
        with self._lock:
            return bool(self._job and self._job.get("cancelling"))

    def status(self) -> dict:
        with self._lock:
            if not self._job:
                return {"state": "idle"}
            j = dict(self._job)
            j["parts"] = [dict(p) for p in j.get("parts", [])]
            j["log"] = list(self._log)[-120:]
            j["progress"] = self._progress.as_dict()
            j["checks"] = parse_checks("\n".join(self._log))
            # a picture of the VM's screen, when the extractor took one
            j["screen"] = bool(j.get("screen_path") and Path(j["screen_path"]).is_file())
            j.pop("screen_path", None)
            end = j.get("finished") or time.time()
            j["elapsed"] = round(end - j["started"])
            j["ago"] = round(time.time() - j["finished"]) if j.get("finished") else 0
            return j

    @staticmethod
    def _epc_cmds(path: Path, index_path: str, vm: str) -> list[list[str]]:
        """Pull the EPC parts catalogue into <out_root>/epc_extract, then
        replace the EPC part of the index with it (an older EPC import is
        removed first, so the index matches the corpus exactly). Built when
        the part starts: in a "both" run the WIS part before it may have
        changed what the index holds."""
        py = [sys.executable, "-u"]
        ext = find_extractor()
        cmds = [[*py, str(ext / "vm_pipeline.py"), "--vm", vm,
                 "--out-root", str(path.parent), "--steps", "epc"]]
        if manuals(index_path)["epc"]["entries"]:
            cmds.append([*py, str(ROOT / "remove_manuals.py"), "--index", str(index_path),
                         "--source", "mercedes-epc"])
        cmds.append([*py, str(ROOT / "ingest.py"), "--archive", str(path),
                     "--index", str(index_path), "--importer", "records"])
        return cmds

    def start(self, path: Path, index_path: str, mode: str, vm: str = "",
              steps: str = "", only: list[str] | None = None) -> dict:
        """`parts` are run one after the other, each a list of commands (or a
        function giving them, called when that part starts); the first
        failing command stops the job, and later parts are never started."""
        with self._lock:
            if self._job and self._job["state"] == "running":
                raise RuntimeError("An import is already running.")
            py = [sys.executable, "-u"]
            rebuild = [*py, str(ROOT / "rebuild_wis.py"), "--archive", str(path),
                       "--index", str(index_path)]
            add = [*py, str(ROOT / "ingest.py"), "--archive", str(path),
                   "--index", str(index_path), "--importer", "auto"]
            parts = None
            if mode in ("vm", "vm_both"):
                # extract from the VM into <out_root>/wis_extract, then build the
                # index from it -- the same two things done by hand
                ext = find_extractor()
                pipe = [*py, str(ext / "vm_pipeline.py"), "--vm", vm,
                        "--out-root", str(path.parent)]
                if steps:
                    pipe += ["--steps", steps]
                cmds = [pipe, rebuild]
                if mode == "vm_both":
                    # WIS, then the EPC parts catalogue from the same VM: the
                    # two existing jobs back to back, EPC only if WIS succeeded
                    epc = path.parent / "epc_extract"
                    parts = [("wis", "WIS", cmds),
                             ("epc", "EPC parts", lambda: self._epc_cmds(epc, index_path, vm))]
            elif mode == "vm_epc":
                cmds = self._epc_cmds(path, index_path, vm)
            elif mode == "vm_rollback":
                # a person asked to roll the VM back to a snapshot; the
                # extractor snapshots the current state first, so it can be undone
                ext = find_extractor()
                cmds = [[*py, str(ext / "vm_pipeline.py"), "--vm", vm, "--restore", steps]]
            elif mode == "vm_check":
                # boot the VM and see Windows start and the databases answer
                # (vm_pipeline.py --check --boot); changes nothing, imports nothing
                ext = find_extractor()
                cmds = [[*py, str(ext / "vm_pipeline.py"), "--vm", vm, "--out-root", str(path.parent),
                         "--steps", steps or VM_STEPS["wis"], "--check", "--boot"]]
            elif mode == "lemon":
                # the picked vehicle folders only, read as LEMON whatever else
                # the folder holds
                cmd = [*py, str(ROOT / "ingest.py"), "--archive", str(path),
                       "--index", str(index_path), "--importer", "lemon"]
                for o in only or []:
                    cmd += ["--only", o]
                cmds = [cmd]
            elif mode in ("remove", "remove_wis", "remove_epc"):
                # remove_manuals.py: LEMON / folder manuals by doc_id, WIS and EPC
                # whole. A Remove button removes completely (the user,
                # 2026-10-23): the index always shrinks, and WIS / EPC's
                # extracted files go to the Recycle Bin (a LEMON or folder
                # manual's files are the user's own and stay)
                cmd = [*py, str(ROOT / "remove_manuals.py"), "--index", str(index_path), "--compact"]
                if mode in ("remove_wis", "remove_epc"):
                    kind = mode[len("remove_"):]
                    cmd += ["--source", f"mercedes-{kind}"]
                    for f in manuals(index_path)[kind]["folders"]:
                        cmd += ["--recycle", f]
                for o in only or []:
                    cmd += ["--doc-id", o]
                cmds = [cmd]
            elif mode == "rebuild_wis":
                # removes WIS in place, re-ingests; every other manual and the
                # spend log stay -- see rebuild_wis.py
                cmds = [rebuild]
            else:
                cmds = [add]
            if parts is None:
                parts = [("", "", cmds)]
            self._log.clear()
            self._progress = Progress()
            try:
                self.cancel_file().unlink(missing_ok=True)   # a cancel is for one job
            except OSError:
                pass
            self._job = {"state": "running", "path": str(path), "mode": mode, "vm": vm,
                         "started": time.time(), "finished": None, "returncode": None,
                         # what the UI's progress bar names: "Step 1 of 2: WIS"
                         "parts": [{"key": k, "label": lb, "state": "pending"} for k, lb, _ in parts],
                         "part": 0, "failed_part": None}
            self._file_start(mode, vm)
        threading.Thread(target=self._run_all, args=([c for _, _, c in parts],),
                         daemon=True).start()
        return self.status()

    # The log is also written to <app dir>/import_job.log as it arrives: the
    # in-memory one dies with the server. On 2026-10-15 the Claude desktop app
    # restarted mid-EPC-pull, taking the dev server (started in its terminal)
    # and the whole job with it, and nothing was left to say how far it got.
    def _file_start(self, mode: str, vm: str) -> None:
        from . import config
        self._file = config.APP_DIR / "import_job.log"
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            self._file.write_text(f"== job {mode}{' ' + vm if vm else ''} started "
                                  f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n", encoding="utf-8")
        except OSError:
            self._file = None

    def _note(self, line: str) -> None:
        self._log.append(line)
        self._progress.feed(line)
        m = re.search(r"VM_SCREEN (.+\.png)\s*$", line)
        if m:
            with self._lock:
                if self._job:
                    self._job["screen_path"] = m.group(1).strip()
        m = re.search(r"\] SNAPSHOT (\S+)", line)
        if m:
            with self._lock:
                if self._job:         # the snapshot this run took, to roll back to
                    self._job["snapshot"] = m.group(1)
        f = getattr(self, "_file", None)
        if f:
            try:
                with f.open("a", encoding="utf-8") as w:
                    w.write(line + "\n")
            except OSError:
                pass

    def screen_path(self) -> Path | None:
        """The picture of the VM's screen the running / last job took."""
        with self._lock:
            p = (self._job or {}).get("screen_path")
        return Path(p) if p and Path(p).is_file() else None

    def _set_part(self, i: int, state: str) -> None:
        with self._lock:
            self._job["parts"][i]["state"] = state
            if state == "running":
                self._job["part"] = i
            if state == "failed":
                self._job["failed_part"] = i
                for p in self._job["parts"][i + 1:]:
                    p["state"] = "not started"

    def _run_all(self, parts: list) -> None:
        rc = 0
        many = len(parts) > 1
        for i, cmds in enumerate(parts):
            label = self._job["parts"][i]["label"]
            if self._cancelled():
                break
            if many and i:
                self._note(f"== waiting {self.PART_PAUSE:g} s before step {i + 1} of {len(parts)} "
                           f"({label}): letting the PC settle after the index rebuild")
                self._progress.reset(f"Waiting {self.PART_PAUSE:g} s before starting")
                end = time.time() + self.PART_PAUSE
                while time.time() < end and not self._cancelled():
                    time.sleep(0.5)
                if self._cancelled():
                    break
            if many:
                self._note(f"== step {i + 1} of {len(parts)}: {label}")
            self._set_part(i, "running")
            if callable(cmds):
                try:
                    cmds = cmds()
                except Exception as exc:
                    self._note(f"! could not start {label}: {exc}")
                    cmds, rc = [], -1
            for cmd in cmds:
                if self._cancelled():
                    rc = 3
                    break
                rc = self._run(cmd)
                if rc != 0:
                    break
            if self._cancelled():
                rc = 3
                self._set_part(i, "cancelled")
                break
            if rc != 0:
                self._set_part(i, "failed")
                if many:
                    rest = ", ".join(p["label"] for p in self._job["parts"][i + 1:])
                    self._note(f"== step {i + 1} of {len(parts)} ({label}) failed"
                               + (f"; not starting {rest}" if rest else ""))
                break
            self._set_part(i, "done")
        cancelled = self._cancelled()
        if cancelled:
            with self._lock:
                for p in self._job["parts"]:
                    if p["state"] == "pending":
                        p["state"] = "not started"
        end_state = "cancelled" if cancelled else "done" if rc == 0 else "failed"
        # the closing line first: once the state says finished, a reader of the
        # file (or a test) must find it complete
        self._note(f"== job {end_state} (exit {rc}) {time.strftime('%Y-%m-%d %H:%M:%S')}")
        try:
            self.cancel_file().unlink(missing_ok=True)
        except OSError:
            pass
        with self._lock:
            self._job["returncode"] = rc
            self._job["finished"] = time.time()
            self._job["state"] = end_state

    def _run(self, cmd: list[str]) -> int:
        shown = [Path(cmd[2]).name, *cmd[3:]] if cmd[1:2] == ["-u"] and len(cmd) > 2 else cmd
        self._note("$ " + " ".join(f'"{c}"' if " " in c else c for c in shown))
        try:
            # a piped child on Windows prints in the ANSI code page and dies on
            # "Coupé"; force UTF-8 both ways
            # the extractor stops (and shuts the VM down cleanly) when the cancel
            # file appears, or when this server's process goes away
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1",
                   "AR_CANCEL_FILE": str(self.cancel_file()), "AR_PARENT_PID": str(os.getpid())}
            proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, env=env,
                                    encoding="utf-8", errors="replace", bufsize=1,
                                    creationflags=NO_WINDOW)
            self._proc = proc
            pipeline = Path(cmd[2]).name == "vm_pipeline.py" if len(cmd) > 2 else False

            def grace() -> None:
                # an extractor that ignores the cancel (or hangs) is ended after
                # CANCEL_GRACE; an index step is never ended half-way
                while proc.poll() is None:
                    if pipeline and self._cancelled() and time.time() - self._cancel_at > self.CANCEL_GRACE:
                        self._note(f"! the extractor did not stop within {self.CANCEL_GRACE:g} s; "
                                   "ending it (the VM may still be running)")
                        proc.kill()
                        return
                    time.sleep(1)
            threading.Thread(target=grace, daemon=True).start()
            for line in proc.stdout:
                self._note(line.rstrip("\n"))
            rc = proc.wait()
            self._proc = None
        except Exception as exc:  # failed to launch
            self._note(f"! could not start: {exc}")
            rc = -1
        return rc
