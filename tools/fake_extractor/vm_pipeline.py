"""A stand-in for extractor/vm_pipeline.py -- FREE, for tests and test boxes.

Never talks to VirtualBox or a database. It takes the same arguments as the
real pipeline, prints the same kind of lines (the ones the Shop setup
progress bar reads: "== images: N graphics ...", "catalog X (i/N, ...",
"$ python parse.py", "STOP: ..."), sleeps a little between them, and writes
a tiny invented corpus where the real one would go, so the job's next step
(rebuild_wis.py / ingest.py) has something real to import.

    Shop setup -> Advanced -> Mercedes extractor folder = <repo>/tools/fake_extractor
    python dev.py --fake-vm     (does that for you)

`--list-vms` lists pretend VMs; the name picks the path:

    Mercedes EWA (fake)            every step succeeds
    Fails in documents (fake)      the WIS document pull stops (exit 1)
    Fails in EPC (fake)            WIS works, the EPC pull stops (exit 1)
    Windows won't start (fake)     boot check / run: Windows never comes up ->
                                   a picture of the screen (VM_SCREEN), state saved
    No network (fake)              --check fails: no host-only adapter, until
                                   --fix-network (Shop setup's "Fix it")

`--check` / `--check --boot` print CHECK lines like the real one. A cancel
(AR_CANCEL_FILE appears) stops it with exit 3 and "shutting ... down", as
the real one does. (AR_PARENT_PID is not watched here.)

AR_FAKE_VM_DELAY  seconds between progress lines (default 0.15)
AR_FAKE_VM_OUT    where the corpus goes (default: <temp>/ar_fake_vm_out; never
                  AR_OUT_ROOT, which may name the real corpus)

Not shipped (tools/ is not in the MSI) and never found unless the extractor
folder setting points here.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from wis_paths import out_root  # noqa: E402

VMS = [("Mercedes EWA (fake)", "poweroff"), ("Fails in documents (fake)", "poweroff"),
       ("Fails in EPC (fake)", "poweroff"), ("Windows won't start (fake)", "poweroff"),
       ("No network (fake)", "poweroff")]
ALL_STEPS = ["docs", "applic", "vehicles", "images", "build", "links", "epc"]
DELAY = float(os.environ.get("AR_FAKE_VM_DELAY") or 0.15)
CANCEL_FILE = os.environ.get("AR_CANCEL_FILE", "")
VM = ""


def cancelled() -> bool:
    return bool(CANCEL_FILE) and Path(CANCEL_FILE).exists()


def stop_if_cancelled() -> None:
    if cancelled():
        print(f"[{time.strftime('%H:%M:%S')}] STOP: cancelled; what was pulled so far is kept, "
              "a re-run resumes", flush=True)
        if VM:
            print(f"[{time.strftime('%H:%M:%S')}] shutting {VM} down", flush=True)
        raise SystemExit(3)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)
    time.sleep(DELAY)
    stop_if_cancelled()


def sub(msg: str) -> None:
    """A line from a child script, the way the real pipeline relays it."""
    print(f"    [{time.strftime('%H:%M:%S')}] {msg}", flush=True)
    time.sleep(DELAY)
    stop_if_cancelled()


def check(key: str, level: str, msg: str) -> str:
    print(f"CHECK\t{key}\t{level}\t{msg}", flush=True)
    return level


# a tiny valid PNG (8x8 blue), standing in for VBoxManage screenshotpng
def write_png(path: Path) -> None:
    import struct
    import zlib
    raw = b"".join(b"\x00" + b"\x10\x40\xa0" * 8 for _ in range(8))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


# pretend snapshots, one JSON file per run's output folder
def _snap_file(out: Path) -> Path:
    return out / "fake_snapshots.json"


def fake_snaps(out: Path) -> dict:
    try:
        return json.loads(_snap_file(out).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def snaps_for(out: Path, vm: str) -> list[dict]:
    return fake_snaps(out).get(vm) or [{"name": "Baseline (fake)", "current": True}]


def save_snaps(out: Path, vm: str, snaps: list[dict]) -> None:
    allv = fake_snaps(out)
    allv[vm] = snaps
    out.mkdir(parents=True, exist_ok=True)
    _snap_file(out).write_text(json.dumps(allv), encoding="utf-8")


def take_snap(out: Path, vm: str, name: str) -> None:
    snaps = snaps_for(out, vm)
    for s in snaps:
        s["current"] = False
    snaps.append({"name": name, "current": True})
    save_snaps(out, vm, snaps)


def network_fixed(out: Path) -> bool:
    try:
        return (out / "fake_network_fixed").read_text(encoding="utf-8") == VM
    except OSError:
        return False


def auto_snapshot(out: Path) -> None:
    ours = [s["name"] for s in snaps_for(out, VM) if s["name"].startswith("AutoRundown-auto-")]
    if ours and time.time() - os.path.getmtime(_snap_file(out)) < 6 * 3600:
        log(f"snapshot: using {ours[-1]} (taken in the last 6 hours)")
        return
    name = "AutoRundown-auto-" + time.strftime("%Y%m%d-%H%M%S")
    take_snap(out, VM, name)
    log(f"SNAPSHOT {name}")


def windows_wont_start(out: Path) -> None:
    log("database not answering yet. Once, inside the VM: log in to Windows, start "
        "'Mercedes WSM EPC' (it starts the database), put the network adapter on "
        "host-only and turn Windows Firewall off. Still checking...")
    out.mkdir(parents=True, exist_ok=True)
    shot = out / "vm_screen.png"
    write_png(shot)
    log(f"VM_SCREEN {shot}")
    log(f"saving {VM}'s state (Windows did not start); open it in VirtualBox to see it as it is")
    die(f"Windows in {VM} did not start within 10 min -- see the picture of its screen.")


def die(msg: str) -> None:
    log("STOP: " + msg)
    raise SystemExit(1)


def wis_record(dok_id: int, title: str, body: str, series: list[str]) -> dict:
    return {
        "dok_id": dok_id, "dok_nr_id": 1000000 + dok_id, "dok_type": 1, "lang": "en",
        "title": title, "fzg_art_bits": "0010" + "0" * 28, "fzg_art_indices": [2],
        "kurzgueltigkeit_id": 98393, "cluster": 8, "body_format": "sids",
        "applicability_notes": ["MODEL " + ", ".join(series)], "cross_refs": [],
        "image_refs": [], "figures": [], "body_text": body,
        "sids_header": 'ASCII-SIDS2.00,"18.06.2016","HP 9000/700"',
        "model_series": series, "baumuster": [], "model_year_from": None,
        "model_year_to": None, "applies_all_series": False,
    }


def write_corpus(cdir: Path, source: str, records: list[dict]) -> None:
    cdir.mkdir(parents=True, exist_ok=True)
    with (cdir / "records.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (cdir / "manifest.json").write_text(json.dumps({
        "schema_version": "1.3", "source_type": source, "source_id": "fake-vm",
        "language": {"iso": "en"}, "record_count": len(records),
        "records_file": "records.jsonl"}), encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vm")
    ap.add_argument("--list-vms", action="store_true")
    ap.add_argument("--out-root", default=str(out_root()))
    ap.add_argument("--steps", default="docs,applic,vehicles,images,build")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--boot", action="store_true")
    ap.add_argument("--list-snapshots", action="store_true")
    ap.add_argument("--restore", default="")
    ap.add_argument("--fix-network", action="store_true")
    args, _ = ap.parse_known_args(argv)
    global VM
    if args.list_vms:
        for name, state in VMS:
            print(f"{name}\t{state}")
        return 0
    if args.vm not in {n for n, _ in VMS}:
        die(f"no VM named {args.vm!r}; --list-vms shows them")
    VM = args.vm
    snap_out = Path(args.out_root)
    if args.list_snapshots:
        for s in snaps_for(snap_out, VM):
            ours = s["name"].startswith(("AutoRundown-auto-", "AutoRundown-before-rollback-"))
            print("\t".join(["SNAP", s["name"], "ours" if ours else "", "current" if s["current"] else "",
                             "a pretend snapshot"]))
        return 0
    if args.restore:
        snaps = snaps_for(snap_out, VM)
        if args.restore not in {s["name"] for s in snaps}:
            die(f"{VM} has no snapshot named {args.restore!r}")
        keep = "AutoRundown-before-rollback-" + time.strftime("%Y%m%d-%H%M%S")
        take_snap(snap_out, VM, keep)
        log(f"SNAPSHOT {keep} (the state before the rollback, to undo it)")
        snaps = snaps_for(snap_out, VM)
        for s in snaps:
            s["current"] = s["name"] == args.restore
        save_snaps(snap_out, VM, snaps)
        log(f"rolled {VM} back to {args.restore}")
        return 0
    if args.fix_network:
        # remembered beside the pretend snapshots, so the next --check sees it
        snap_out.mkdir(parents=True, exist_ok=True)
        (snap_out / "fake_network_fixed").write_text(VM, encoding="utf-8")
        log(f"NETWORK {VM}: Adapter 1 is now on the host-only network "
            "'VirtualBox Host-Only Ethernet Adapter'")
        return 0
    steps = [s for s in args.steps.split(",") if s]
    bad = [s for s in steps if s not in ALL_STEPS]
    if bad:
        ap.error(f"unknown step(s) {bad}")
    out = Path(args.out_root)
    online = [s for s in steps if s != "build"]
    if args.check:
        levels = [check("vm", "ok", f"{VM} is off and ready to start.")]
        if online:
            levels.append(check("snapshot", "ok", "A snapshot of the VM is taken before it starts, "
                                                  "to roll back to if Windows breaks."))
            levels.append(check("network", "fail", f"{VM} has no host-only network adapter: this PC "
                                                   "cannot reach its database. Fix it sets Adapter 1 to "
                                                   "Host-only Adapter.")
                          if "No network" in VM and not network_fixed(out)
                          else check("network", "ok", "Host-only network adapter."))
            levels += [check("java", "ok", "Java: fake-java"), check("jdbc", "ok", "Transbase JDBC driver found.")]
        levels += [check("scripts", "ok", "The extractor has every script these steps need."),
                   check("disk", "ok", f"500 GB free for {out}.")]
        if args.boot and "fail" not in levels:
            auto_snapshot(out)
            log(f"starting {VM} headless")
            if "won't start" in VM:
                try:
                    windows_wont_start(out)
                except SystemExit:
                    stop_if_cancelled()
                    levels.append(check("db_wis", "fail", "The WIS database did not answer -- see the "
                                                          "log and the picture of the VM's screen."))
            else:
                for name, port in ([("WIS", 2054)] if [s for s in online if s != "epc"] else []) + \
                                  ([("EPC", 2034)] if "epc" in steps else []):
                    log(f"database answering at 192.168.56.101:{port}; letting it settle 30 s")
                    levels.append(check(f"db_{name.lower()}", "ok", f"Windows started and the {name} "
                                                                   f"database answers at 192.168.56.101:{port}."))
                log(f"shutting {VM} down")
        log("check: " + ("ready" if "fail" not in levels else "NOT ready -- see the CHECK lines"))
        return 0 if "fail" not in levels else 2
    if online:
        log(f"VM {args.vm}; Java fake-java; output {out}")
        auto_snapshot(out)
        log(f"starting {args.vm} headless")
        if "won't start" in args.vm:
            windows_wont_start(out)
        log("database answering at 192.168.56.101:2054; letting it settle 30 s")
    if "docs" in steps:
        log("== documents (resumable; the DB sometimes drops -- the VM is power-cycled and the pull resumes)")
        log("$ java -cp tbjdbc.jar jdk.nashorn.tools.Shell pull.js")
        sub("PULL start url=jdbc:transbase://192.168.56.101:2054/wisnet lang=2 out=x watermark=-1 (fresh)")
        sub("total docs for lang 2 = 25000")
        for n in range(5000, 25001, 5000):
            if "Fails in documents" in args.vm and n == 15000:
                sub("ABORT body query failed at DokID 1015000: java.sql.SQLException: connection reset")
                log("document pull stopped (attempt 1); power-cycling and resuming")
                log(f"shutting {args.vm} down")
                die("document pull kept stopping; re-run to resume")
            sub(f"progress: {n} pulled, 3 no-body, at DokID {1000000 + n}")
        sub("PULL done: pulled=25000 no_body=12 lastDokID=1025000")
    if "applic" in steps:
        log("== model applicability")
        log("$ java -cp tbjdbc.jar jdk.nashorn.tools.Shell pull_applic.js")
        sub("W_Baumusterzeile rows = 33085")
        sub("W_Dok2Reihe rows = 812345")
        log("$ java -cp tbjdbc.jar jdk.nashorn.tools.Shell pull_applic2.js")
        for t in ("W_DokWS", "W_DokWC"):
            sub(f"{t} rows = 120000")
    if "vehicles" in steps:
        log("== vehicle names and VIN tables")
        log("$ java -cp tbjdbc.jar jdk.nashorn.tools.Shell pull_vehicles.js")
        for t in ("ASRA_FahrzeugII", "USTypBM2TypBM", "FIN_Prefix"):
            sub(f"{t} rows = 7577  (6 columns)")
    if "images" in steps:
        log("== images: 3,000 graphics, 0 already pulled; 1,000 per batch, VM off between batches")
        for b in range(3):
            log("$ java -cp tbjdbc.jar jdk.nashorn.tools.Shell pull_images.js")
            for ok in (400, 800):
                sub(f"progress: row {b * 1000 + ok}/3000 ok={ok} missing=0 MB={ok // 10}")
            sub("batch limit 1000 reached")
            log(f"batch: +1,000 -> {(b + 1) * 1000:,} / 3,000")
            if b < 2:
                log(f"shutting {args.vm} down")
                log(f"starting {args.vm} headless")
    if "epc" in steps:
        log("== EPC parts catalogue (all,engines); resumes per catalog, drawings already on disk are reused")
        log("$ java -Xmx1024m -cp tbjdbc.jar jdk.nashorn.tools.Shell pull_epc.js")
        sub("catalogs to pull: 6")
        for i, cat in enumerate(("040", "050", "060", "300", "301", "302"), 1):
            if "Fails in EPC" in args.vm and i == 4:
                sub("java.lang.OutOfMemoryError: Java heap space")
                die("EPC pull failed with OutOfMemoryError -- a problem in the pull itself")
            sub(f"catalog {cat} ({i}/6, series 201): 40 records, 900 parts; images {{}}; 2 s")
        sub("SA options: 400")
        for i in (200, 400):
            sub(f"  SA {i}/400: {i // 4} records so far")
        sub("DONE: 340 records in records.jsonl")
        write_corpus(out / "epc_extract" / "epc_en", "mercedes-epc", [{
            "dok_id": 9000001, "lang": "en", "body_format": "sids",
            "title": "Parts: model 201 (EPC catalog 040), group 18 / subgroup 015 OIL FILTER",
            "body_text": "Parts: oil filter\n\nCallout | Part number | Name | Fits | Notes\n"
                         "010 | A 102 180 00 09 (A1021800009, 1021800009) | OIL FILTER | qty 1, all models |",
            "model_series": ["201"], "baumuster": [], "engine_series": [],
            "applicability_notes": ["MODEL 201"], "cross_refs": [], "image_refs": [],
            "figures": [], "image_files": [], "source_type": "mercedes-epc"}])
        log(f"EPC corpus ready: {out / 'epc_extract'}")
    if online:
        log(f"shutting {args.vm} down")
    if "build" in steps:
        log("== offline build")
        log("$ python parse.py")
        print("parse: out=x lang=2\n  25000 bodies\n  25000 docs", flush=True)
        for i in (5000, 10000, 15000, 20000, 25000):
            print(f"  parsed {i}...", flush=True)
            time.sleep(DELAY)
        log("$ python apply_applic.py --rebuild-join")
        log("$ python parse_vehicles.py")
        log("$ python parse_links.py")
        log("$ python convert_images.py --jobs 3")
        print("0 already converted, 3000 to do, 3 workers", flush=True)
        for n in (1000, 2000, 3000):
            print(f"  {n}/3000  0 failed  500/s  eta 0 min", flush=True)
            time.sleep(DELAY)
        log("$ python apply_images.py")
        # the dev box's own sample corpus, if dev.py made one: the same doc_ids
        # as its index, so the import resumes instead of adding cars twice
        sample = HERE.parents[1] / "sample_corpus" / "wis_en"
        if (sample / "manifest.json").is_file():
            import shutil
            dst = out / "wis_extract" / "wis_en"
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(sample, dst)
        else:
            write_corpus(out / "wis_extract" / "wis_en", "mercedes-wis-virtualbox", [
                wis_record(1002296, "Remove/install front brake pads",
                           "Unscrew the brake caliper. Tighten the caliper bolts to 30 Nm.", ["201"]),
                wis_record(1002297, "Engine oil and filter change",
                           "Drain the engine oil. Drain plug 25 Nm.", ["201"])])
        log(f"corpus ready: {out / 'wis_extract'}")
    log("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
