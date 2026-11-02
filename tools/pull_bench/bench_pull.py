"""Which pull settings get the Mercedes data out fastest, counting the VM
restarts the database's dropped connections cost? A throwaway benchmark, not
part of the app: it reads from the real VM, writes nothing but its results.

    python tools/pull_bench/bench_pull.py --vm Mercedes_Ultimate_207 [--only a,b]

Each setting starts from a cold VM boot (not timed), then times pulling the same
items, including every restart (a clean shutdown, 60 s, a boot -- what the app
does). The VM is only ever shut down cleanly (vm_pipeline.vm_down: never a
power-off), a snapshot is taken first, and its memory is put back at the end.
Results: tools/pull_bench/results/results.jsonl and bench.log.
"""
from __future__ import annotations

import argparse
import json
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "extractor"))
import vm_pipeline as vmp  # noqa: E402

RESULTS = HERE / "results"
LIST = vmp.home() / "autorundown_out" / "wis_images" / "list.tsv"

IMG = dict(kind="images", start=100001, count=60000)
DOC = dict(kind="docs", start=7000000, count=60000)
# name: settings. sleep_ms per item (or per sleep_every), fresh = new connection
# every N, retries = reconnect attempts on a drop before a VM restart, reboot =
# planned VM restart every N items (the app's images: 15,000 with 25 ms each)
CONFIGS = {
    "img-app":         {**IMG, "sleep_ms": 25, "reboot": 15000, "retries": 0},
    "img-fast":        {**IMG, "sleep_ms": 0, "retries": 5},
    "img-fast-fresh":  {**IMG, "sleep_ms": 0, "fresh": 5000, "retries": 5},
    "img-paced5":      {**IMG, "sleep_ms": 5, "retries": 5},
    "doc-app":         {**DOC, "sleep_ms": 300, "sleep_every": 5000, "retries": 0},
    "doc-reconnect":   {**DOC, "sleep_ms": 300, "sleep_every": 5000, "retries": 5},
    "doc-chunked":     {**DOC, "sleep_ms": 300, "sleep_every": 5000, "retries": 0, "cursor": 2000},
    # second set (2026-11-01). A reconnect after a drop never worked (5 tries
    # over 3 min), so a drop goes straight to a VM restart (retries 0); a long
    # session that dropped left Windows unable to boot, so restarts are planned.
    "doc-chunked-fresh": {**DOC, "sleep_ms": 0, "retries": 0, "cursor": 2000, "fresh": 5000},
    # images none of the first set touched (no help from this PC's file cache)
    "img-fast-cold":   {**IMG, "start": 200001, "sleep_ms": 0, "fresh": 5000, "retries": 0},
    # the likely winner over every image and every document
    "img-cand-all":    {**IMG, "start": 1, "count": 272185, "sleep_ms": 0, "fresh": 5000,
                        "reboot": 60000, "retries": 0},
    "doc-cand-all":    {**DOC, "start": 0, "count": 272607, "sleep_ms": 0, "retries": 0,
                        "cursor": 2000, "fresh": 5000, "reboot": 60000},
    "img-fast-all":    {**IMG, "start": 1, "count": 272185, "sleep_ms": 0, "retries": 5},
    # a few minutes: does it all work
    "smoke-img":       {**IMG, "count": 3000, "sleep_ms": 0, "retries": 2},
    "smoke-doc":       {**DOC, "count": 3000, "sleep_ms": 0, "retries": 2, "cursor": 1000},
}


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with (RESULTS / "bench.log").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def memory(vm: str) -> int:
    for line in vmp.vbox("showvminfo", vm, "--machinereadable").splitlines():
        if line.startswith("memory="):
            return int(line.split("=", 1)[1])
    return 0


def set_memory(vm: str, mb: int) -> None:
    if memory(vm) == mb:
        return
    vmp.vm_down(vm)
    if vmp.vm_state(vm) != "poweroff":
        raise SystemExit(f"{vm} did not shut down cleanly ({vmp.vm_state(vm)}); not changing its memory")
    vmp.vbox("modifyvm", vm, "--memory", str(mb))
    log(f"VM memory set to {mb} MB")


def boot(vm: str) -> str:
    return vmp.vm_up(vm, 2054, "192.168.56", 600)


def restart(vm: str) -> str:
    vmp.vm_down(vm)
    time.sleep(60)
    return boot(vm)


def run_java(ip: str, cfg: dict, start: int, count: int, quiet_s: int = 600) -> tuple[dict, list[str]]:
    env = {"AR_HOST": ip, "AR_PORT": "2054", "AR_DB": "wisnet", "B_KIND": cfg["kind"],
           "B_LIST": str(LIST), "B_FROM": str(start), "B_COUNT": str(count),
           "B_SLEEP_MS": str(cfg.get("sleep_ms", 0)), "B_SLEEP_EVERY": str(cfg.get("sleep_every", 1)),
           "B_FRESH_EVERY": str(cfg.get("fresh", 0)), "B_CURSOR_ROWS": str(cfg.get("cursor", 0)),
           "B_RETRIES": str(cfg.get("retries", 0)), "B_RETRY_WAIT_MS": str(cfg.get("retry_wait_ms", 5000))}
    p = subprocess.Popen([vmp.JAVA, "-cp", "tbjdbc.jar", "jdk.nashorn.tools.Shell", "bench_pull.js"],
                         cwd=vmp.STAGE, env={**__import__("os").environ, **env}, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                         creationflags=vmp.NO_WINDOW)
    q: queue.Queue = queue.Queue()
    threading.Thread(target=lambda: [q.put(l) for l in p.stdout] + [q.put(None)], daemon=True).start()
    events, end, last = [], None, time.time()
    while True:
        try:
            line = q.get(timeout=5)
        except queue.Empty:
            if time.time() - last > quiet_s:          # hung, not dropped: treat as dead
                p.kill()
                events.append(f"EV - hung no output for {quiet_s} s")
                log(f"  hung for {quiet_s} s; killed")
                break
            continue
        if line is None:
            break
        last = time.time()
        line = line.rstrip()
        if line.startswith("EV "):
            events.append(line)
            if " progress " not in line:
                log("  " + line)
        elif line.startswith("END "):
            st, items, nbytes, ms, nxt = line.split()[1:6]
            end = dict(status=st, items=int(items), bytes=int(nbytes), ms=int(ms), next=int(nxt))
        elif line and not line.startswith(("\tat ", "at ")):
            log("  java: " + line[:200])
    p.wait()
    return end or dict(status="dead", items=0, bytes=0, ms=0, next=start), events


def bench(vm: str, name: str, cfg: dict, mem: int) -> dict:
    log(f"== {name} (VM {mem} MB): {cfg}")
    set_memory(vm, mem)
    vmp.vm_down(vm)
    ip = boot(vm)                                       # cold start, not timed
    t0 = time.time()
    pos, left, items, nbytes, pull_ms = cfg["start"], cfg["count"], 0, 0, 0
    drops = reconnects = planned = forced = dead_streak = 0
    while left > 0:
        chunk = min(left, cfg.get("reboot") or left)
        end, events = run_java(ip, cfg, pos, chunk)
        drops += sum(" drop " in e for e in events)
        reconnects += sum(" reconnected " in e for e in events)
        items += end["items"]
        nbytes += end["bytes"]
        pull_ms += end["ms"]
        left -= end["items"]
        pos = end["next"]
        if cfg["kind"] == "docs" and end["status"] == "ok" and end["items"] < chunk:
            break                                       # ran out of documents
        if end["status"] == "dead":
            dead_streak = dead_streak + 1 if end["items"] == 0 else 0
            if dead_streak >= 3:
                log("  three restarts in a row pulled nothing; giving up on this setting")
                break
            forced += 1
            log(f"  database gone: VM restart #{forced} (at {items} items)")
            ip = restart(vm)
        elif left > 0:
            planned += 1
            log(f"  planned VM restart #{planned} (at {items} items)")
            ip = restart(vm)
    total = time.time() - t0
    r = dict(name=name, memory_mb=mem, **cfg, items=items, mb=round(nbytes / 1048576, 1),
             total_s=round(total), pulling_s=round(pull_ms / 1000), drops=drops, reconnected=reconnects,
             forced_restarts=forced, planned_restarts=planned,
             per_min=round(items / (total / 60)) if total else 0, when=time.strftime("%Y-%m-%d %H:%M"))
    with (RESULTS / "results.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(r) + "\n")
    log(f"== {name}: {items} items in {total / 60:.1f} min ({r['per_min']}/min), {drops} drops, "
        f"{reconnects} reconnected, {forced} forced + {planned} planned VM restarts")
    return r


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vm", required=True)
    ap.add_argument("--only", default="", help="comma list of setting names (default: all)")
    ap.add_argument("--memory", default="8192", help="VM memory in MB; a comma list runs each")
    args = ap.parse_args()
    RESULTS.mkdir(exist_ok=True)
    vmp.VBOX = vmp.find_vbox()
    vmp.JAVA = vmp.find_java()
    vmp.STAGE = vmp.stage_scripts(vmp.find_jar())
    shutil.copy2(HERE / "bench_pull.js", vmp.STAGE / "bench_pull.js")
    vmp.DB_NAMES.update({2054: "wisnet"})
    names = [n for n in args.only.split(",") if n] or [n for n in CONFIGS if not n.startswith(("smoke", "img-fast-cold", "img-fast-all"))]
    original = memory(args.vm)
    log(f"VM {args.vm}: memory {original} MB, state {vmp.vm_state(args.vm)}; settings {names}")
    vmp.auto_snapshot(args.vm, "a pull benchmark")
    try:
        for mem in (int(m) for m in args.memory.split(",")):
            for n in names:
                bench(args.vm, n, CONFIGS[n], mem)
    finally:
        vmp.vm_down(args.vm)
        if vmp.vm_state(args.vm) == "poweroff" and memory(args.vm) != original:
            vmp.vbox("modifyvm", args.vm, "--memory", str(original))
            log(f"VM memory put back to {original} MB")
        log(f"VM state at the end: {vmp.vm_state(args.vm)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
