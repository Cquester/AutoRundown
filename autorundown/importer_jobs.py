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
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import importers

ROOT = Path(__file__).resolve().parents[1]
FOLDER_SCAN_CAP = 5000   # stop counting files here: a folder import of a whole
                         # drive must not make the preview hang


def wis_backup(index_path: str | Path) -> Path:
    """The pre-WIS backup sits next to the index it belongs to (rebuild_wis.py)."""
    return Path(index_path).with_name("index.before-wis.db")


def can_rebuild_wis(index_path: str | Path) -> bool:
    return wis_backup(index_path).is_file()


def find_extractor() -> Path | None:
    """The WIS extractor folder (holds vm_pipeline.py), or None."""
    from . import config
    for c in (config.load().extractor_dir,
              str(ROOT.parent / "Mercedes Documents" / "autorundown_extractor")):
        if c and (Path(c) / "vm_pipeline.py").is_file():
            return Path(c)
    return None


def list_vms() -> dict:
    """VirtualBox VMs on this PC, via the extractor's own helper."""
    ext = find_extractor()
    if not ext:
        return {"extractor": "", "vms": [],
                "error": "The Mercedes extractor (vm_pipeline.py) was not found. Set its folder "
                         "as extractor_dir in ~/.autorundown/config.json."}
    r = subprocess.run([sys.executable, str(ext / "vm_pipeline.py"), "--list-vms"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=60)
    if r.returncode != 0:
        return {"extractor": str(ext), "vms": [],
                "error": (r.stdout + r.stderr).strip().splitlines()[-1:] or ["VirtualBox not found"]}
    vms = [dict(zip(("name", "state"), l.split("\t"))) for l in r.stdout.splitlines() if "\t" in l]
    return {"extractor": str(ext), "vms": vms, "out_root": str(ext.parent / "autorundown_out")}


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
        bits = []
        for cdir in corp[:3]:
            try:
                m = json.loads((cdir / "manifest.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            img = m.get("images") or {}
            bits.append(
                f"{m.get('source_type', 'records')} ({(m.get('language') or {}).get('iso', '?')}), "
                f"{m.get('record_count', '?'):,} documents, schema {m.get('schema_version', '?')}"
                + (f", {img.get('converted', 0):,} images" if img else ""))
        return {"importer": name, "rebuildable": can_rebuild_wis(index_path),
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


class ImportJobs:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._job: dict | None = None
        self._log: collections.deque[str] = collections.deque(maxlen=400)

    def status(self) -> dict:
        with self._lock:
            if not self._job:
                return {"state": "idle"}
            j = dict(self._job)
            j["log"] = list(self._log)[-120:]
            end = j.get("finished") or time.time()
            j["elapsed"] = round(end - j["started"])
            return j

    def start(self, path: Path, index_path: str, mode: str, vm: str = "",
              steps: str = "") -> dict:
        with self._lock:
            if self._job and self._job["state"] == "running":
                raise RuntimeError("An import is already running.")
            py = [sys.executable, "-u"]
            rebuild = [*py, str(ROOT / "rebuild_wis.py"), "--archive", str(path),
                       "--index", str(index_path)]
            add = [*py, str(ROOT / "ingest.py"), "--archive", str(path),
                   "--index", str(index_path), "--importer", "auto"]
            if mode == "vm":
                # extract from the VM into <out_root>/wis_extract, then build the
                # index from it -- the same two things done by hand
                ext = find_extractor()
                pipe = [*py, str(ext / "vm_pipeline.py"), "--vm", vm,
                        "--out-root", str(path.parent)]
                if steps:
                    pipe += ["--steps", steps]
                cmds = [pipe, rebuild if can_rebuild_wis(index_path) else add]
            elif mode == "rebuild_wis":
                # restores index.before-wis.db over the live index, carries the
                # spend log across, re-ingests -- see rebuild_wis.py
                cmds = [rebuild]
            else:
                cmds = [add]
            self._log.clear()
            self._job = {"state": "running", "path": str(path), "mode": mode, "vm": vm,
                         "started": time.time(), "finished": None, "returncode": None}
        threading.Thread(target=self._run_all, args=(cmds,), daemon=True).start()
        return self.status()

    def _run_all(self, cmds: list[list[str]]) -> None:
        rc = 0
        for cmd in cmds:
            rc = self._run(cmd)
            if rc != 0:
                break
        with self._lock:
            self._job["returncode"] = rc
            self._job["finished"] = time.time()
            self._job["state"] = "done" if rc == 0 else "failed"

    def _run(self, cmd: list[str]) -> int:
        self._log.append("$ " + " ".join(f'"{c}"' if " " in c else c for c in cmd[1:]))
        try:
            # a piped child on Windows prints in the ANSI code page and dies on
            # "Coupé"; force UTF-8 both ways
            env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"}
            proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, env=env,
                                    encoding="utf-8", errors="replace", bufsize=1)
            for line in proc.stdout:
                self._log.append(line.rstrip("\n"))
            rc = proc.wait()
        except Exception as exc:  # failed to launch
            self._log.append(f"! could not start: {exc}")
            rc = -1
        return rc
