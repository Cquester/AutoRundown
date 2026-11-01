"""Does the VM's Windows hang at boot more often at 8 GB than at 12? A
throwaway test next to bench_pull.py: per memory size, N cycles of boot to a
working database, a short pull (5,000 images, so the database has worked as in
a real run), a clean shutdown. A boot that hangs stops that memory size's
cycles: the VM's state is saved (never a power-off) and it is rolled back to
--snapshot, the snapshot taken before the benchmark, to go on with the next size.

    python tools/pull_bench/boot_test.py --vm Mercedes_Ultimate_207 --snapshot <name>
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import bench_pull as bp

vmp = bp.vmp


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vm", required=True)
    ap.add_argument("--snapshot", required=True, help="roll back to this after a hang")
    ap.add_argument("--memory", default="12266,8192")
    ap.add_argument("--cycles", type=int, default=15)
    args = ap.parse_args()
    bp.RESULTS.mkdir(exist_ok=True)
    vmp.VBOX = vmp.find_vbox()
    vmp.JAVA = vmp.find_java()
    vmp.STAGE = vmp.stage_scripts(vmp.find_jar())
    bp.shutil.copy2(bp.HERE / "bench_pull.js", vmp.STAGE / "bench_pull.js")
    vmp.DB_NAMES.update({2054: "wisnet"})
    original = bp.memory(args.vm)
    pull = {"kind": "images", "sleep_ms": 0, "retries": 0}
    try:
        for mem in (int(m) for m in args.memory.split(",")):
            bp.set_memory(args.vm, mem)
            boots, hangs, times = 0, 0, []
            for c in range(args.cycles):
                t = time.time()
                try:
                    ip = bp.boot(args.vm)
                except SystemExit:
                    hangs += 1
                    bp.log(f"boot test {mem} MB: cycle {c + 1} HUNG; rolling back to {args.snapshot}")
                    vmp.restore_snapshot(args.vm, args.snapshot)
                    break
                boots += 1
                times.append(round(time.time() - t))
                end, _ = bp.run_java(ip, pull, 100001 + c * 5000, 5000)
                bp.log(f"boot test {mem} MB: cycle {c + 1} booted in {times[-1]} s, pulled {end['items']} ({end['status']})")
                vmp.vm_down(args.vm)
                if vmp.vm_state(args.vm) != "poweroff":
                    bp.log(f"boot test {mem} MB: no clean shutdown ({vmp.vm_state(args.vm)}); stopping")
                    return 1
            r = dict(test="boot", memory_mb=mem, cycles=args.cycles, booted=boots, hung=hangs,
                     boot_s=times, when=time.strftime("%Y-%m-%d %H:%M"))
            with (bp.RESULTS / "results.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(r) + "\n")
            bp.log(f"== boot test {mem} MB: {boots} good boots, {hangs} hung")
    finally:
        vmp.vm_down(args.vm)
        if vmp.vm_state(args.vm) == "poweroff" and bp.memory(args.vm) != original:
            vmp.vbox("modifyvm", args.vm, "--memory", str(original))
            bp.log(f"VM memory put back to {original} MB")
        bp.log(f"VM state at the end: {vmp.vm_state(args.vm)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
