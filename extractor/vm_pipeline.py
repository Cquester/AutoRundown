"""Mercedes WIS virtual machine -> AutoRundown corpus, end to end.

    python vm_pipeline.py --vm "Mercedes_Ultimate_207" [--out-root DIR] [--steps ...]
    python vm_pipeline.py --list-vms

Runs the same pieces the manual procedure does (CLAUDE.md in AutoRundown),
in order, each resumable -- a re-run skips what is already pulled:

  online (VM up):   docs      pull.js          document bodies -> bodies.pack
                    applic    pull_applic.js + pull_applic2.js  model links
                    vehicles  pull_vehicles.js names, VIN tables, docnrs,
                                               SA codes, oil capacities, labour
                    links     pull_links.js    wiring-diagram component codes,
                                               positions and names (batched)
                    images    pull_images.js   graphics, 15k per batch,
                                               VM power-cycled between batches
  offline:          build     parse.py -> apply_applic.py -> parse_vehicles.py
                              -> parse_links.py -> convert_images.py
                              -> apply_images.py

What it will NOT do to the VM: take or restore snapshots, change network
adapters, or touch the guest's firewall. It only starts it headless, stops
it, and reads the database (SELECT only). If the database isn't reachable it
says what to set up, once, by hand (see README.md "Bring-up").

Two properties of this setup shape the batching (see CLAUDE.md): the in-VM
database resets its connection after ~19.5k graphics in one session, and the
host has hard-powered-off under sustained heavy I/O -- so images go in 15k
batches with the VM off in between, and conversion runs on 3 workers.
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
from wis_paths import home, out_root, check_outside_repo  # noqa: E402
DEFAULT_OUT = out_root()
STEPS = ("docs", "applic", "vehicles", "links", "images", "build")
BATCH = 15000
GZIP_MAGIC = bytes((0x1F, 0x8B))     # a SIDS body; anything else is an RTF/XML stub


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def die(msg: str) -> "None":
    log("STOP: " + msg)
    sys.exit(1)


# -- tools -------------------------------------------------------------------

def find_vbox() -> str:
    for c in (shutil.which("VBoxManage"), r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"):
        if c and Path(c).is_file():
            return c
    die("VBoxManage not found. Install VirtualBox, or add it to PATH.")


def find_java(explicit: str = "") -> str:
    if explicit and Path(explicit).is_file():
        return explicit
    # the 32-bit JRE bundled with the EWA install: its Nashorn runs the .js pulls
    for base in (home() / "ewa_extract", home()):
        for p in base.glob("**/sun_jre/bin/java.exe"):
            return str(p)
    j = shutil.which("java")
    if j:
        return j
    die("Java not found. Pass --java <path to the EWA install's sun_jre\\bin\\java.exe>.")


def find_jar(explicit: str = "") -> str:
    for c in ([explicit] if explicit else []) + [str(p) for p in home().glob("**/tbjdbc.jar")]:
        if c and Path(c).is_file():
            return c
    die("tbjdbc.jar (Transbase JDBC driver) not found; pass --jar.")


def vbox(*args: str, check: bool = True) -> str:
    r = subprocess.run([VBOX, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"VBoxManage {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def list_vms() -> list[tuple[str, str]]:
    """[(name, state)] for every VirtualBox VM on this PC."""
    running = {l.split('"')[1] for l in vbox("list", "runningvms").splitlines() if '"' in l}
    return [(name, "running" if name in running else "off")
            for name in (l.split('"')[1] for l in vbox("list", "vms").splitlines() if '"' in l)]


def vm_state(vm: str) -> str:
    for line in vbox("showvminfo", vm, "--machinereadable").splitlines():
        if line.startswith("VMState="):
            return line.split("=", 1)[1].strip('"')
    return "unknown"


# -- the VM and its database ---------------------------------------------------

def port_open(ip: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((ip, port), timeout):
            return True
    except OSError:
        return False


def find_db(vm: str, port: int, subnet: str) -> str | None:
    for n in range(4):   # Guest Additions report the guest's addresses
        out = vbox("guestproperty", "get", vm, f"/VirtualBox/GuestInfo/Net/{n}/V4/IP", check=False)
        ip = out.replace("Value:", "").strip()
        if ip.count(".") == 3 and port_open(ip, port):
            return ip
    for h in list(range(100, 111)) + list(range(2, 100)):   # host-only DHCP
        ip = f"{subnet}.{h}"
        if port_open(ip, port, 0.25):
            return ip
    return None


def vm_up(vm: str, port: int, subnet: str, wait_s: int) -> str:
    """Boot (if needed) and return the DB's address; a second cold boot if the
    first one never brings the database up. Seen once in the from-scratch
    run: after 12 clean batches, one boot left the DB dark for 10 minutes."""
    for attempt in (1, 2):
        ip = _vm_up_once(vm, port, subnet, wait_s, final=attempt == 2)
        if ip:
            return ip
        log("database never came up; powering off for one more cold boot")
        vm_down(vm)
        time.sleep(60)
    die("unreachable")   # _vm_up_once(final=True) already stopped with the reason


def _vm_up_once(vm: str, port: int, subnet: str, wait_s: int, final: bool) -> str | None:
    state = vm_state(vm)
    if state == "saved":
        # a state saved under another VirtualBox version may not resume; a
        # cold boot is what a power-off would give the guest anyway
        log("VM has a saved state; discarding it for a clean boot")
        vbox("discardstate", vm)
        state = "poweroff"
    if state not in ("running",):
        log(f"starting {vm} headless")
        vbox("startvm", vm, "--type", "headless")
        STARTED.add(vm)
    deadline = time.time() + wait_s
    told = False
    while time.time() < deadline:
        ip = find_db(vm, port, subnet)
        if ip:
            log(f"database answering at {ip}:{port}; letting it settle 30 s")
            time.sleep(30)
            return ip
        if not told and time.time() > deadline - wait_s + 120:
            log("database not answering yet. Once, inside the VM: log in to Windows, start "
                "'Mercedes WSM EPC' (it starts the database), put the network adapter on "
                "host-only and turn Windows Firewall off. Still checking...")
            told = True
        time.sleep(5)
    if final:
        die(f"database not reachable on port {port} within {wait_s} s on two boots "
            f"-- see the message above.")
    return None


def vm_down(vm: str) -> None:
    if vm_state(vm) != "running":
        return
    log(f"shutting {vm} down")
    vbox("controlvm", vm, "acpipowerbutton", check=False)
    for _ in range(24):
        if vm_state(vm) == "poweroff":
            return
        time.sleep(5)
    log("no clean shutdown after 2 min; powering off")
    vbox("controlvm", vm, "poweroff", check=False)


# -- running the pieces ----------------------------------------------------------

def run(cmd: list[str], env: dict, cwd: Path) -> int:
    log("$ " + " ".join(Path(c).name if i == 0 else c for i, c in enumerate(cmd)))
    p = subprocess.Popen(cmd, cwd=cwd, env={**os.environ, **env, "PYTHONIOENCODING": "utf-8"},
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", bufsize=1)
    for line in p.stdout:
        line = line.rstrip()
        # Nashorn stack traces are dozens of lines of noise; keep the message
        if line.startswith(("\tat ", "Caused by")) or line.startswith("at "):
            continue
        print("    " + line, flush=True)
    return p.wait()


def stage_scripts(jar: str) -> Path:
    """Nashorn is fussy about spaces in paths: run the .js from a clean dir."""
    stage = Path(tempfile.gettempdir()) / "ar_extract_stage"
    stage.mkdir(exist_ok=True)
    shutil.copy2(jar, stage / "tbjdbc.jar")
    for js in HERE.glob("*.js"):
        shutil.copy2(js, stage / js.name)
    return stage


def nashorn(script: str, env: dict) -> int:
    return run([JAVA, "-cp", "tbjdbc.jar", "jdk.nashorn.tools.Shell", script], env, STAGE)


def build_image_list(extract: Path, images: Path) -> int:
    """Every distinct (graphic, cluster) the pulled documents cite."""
    src = extract / "figures.tsv"
    if not src.is_file():
        die(f"{src} missing -- run the docs step first")
    seen = {}
    with src.open(encoding="utf-8", errors="replace") as f:
        next(f)
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 5 and p[1].isdigit() and p[4].isdigit():
                seen.setdefault((int(p[1]), int(p[4])), (p[0], p[2], p[3]))
    images.mkdir(parents=True, exist_ok=True)
    with (images / "list.tsv").open("w", encoding="utf-8", newline="\n") as w:
        w.write("dok_id\tgraphik_id\tgraphik_nr\tklasse\tcluster\n")
        for (gid, cl), (dok, nr, kl) in sorted(seen.items(), key=lambda x: (x[0][1], x[0][0])):
            w.write(f"{dok}\t{gid}\t{nr}\t{kl}\t{cl}\n")
    return len(seen)


def build_links_list(extract: Path, links: Path) -> int:
    """The documents whose diagram tables pull_links.js reads: every body in
    bodies.pack that is not SIDS (gzip). Those are the RTF stubs that carry
    wiring diagrams and picture menus; a SIDS page has no DXF rows."""
    import struct
    pack = extract / "bodies.pack"
    if not pack.is_file():
        die(f"{pack} missing -- run the docs step first")
    ids, size = [], pack.stat().st_size
    with pack.open("rb") as f:
        off = 0
        while off + 8 <= size:
            f.seek(off)
            dok, ln = struct.unpack(">ii", f.read(8))
            if off + 8 + ln > size:
                break
            if f.read(2) != GZIP_MAGIC:
                ids.append(dok)
            off += 8 + ln
    links.mkdir(parents=True, exist_ok=True)
    (links / "links_list.txt").write_text(
        "".join(f"{i}\n" for i in sorted(ids)), encoding="utf-8")
    return len(ids)


def pulled_count(images: Path) -> int:
    idx = images / "pull_images.tsv"
    if not idx.is_file():
        return 0
    with idx.open(encoding="utf-8", errors="replace") as f:
        return sum(1 for l in f if l.rstrip().endswith("\tok"))


def main(argv=None) -> int:
    global VBOX, JAVA, STAGE
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vm", help="VirtualBox VM name")
    ap.add_argument("--list-vms", action="store_true")
    ap.add_argument("--out-root", default=str(DEFAULT_OUT))
    ap.add_argument("--steps", default=",".join(STEPS),
                    help=f"comma list of {', '.join(STEPS)}")
    ap.add_argument("--lang", default="2", help="WIS language code (2 = English)")
    ap.add_argument("--port", type=int, default=2054)
    ap.add_argument("--db", default="wisnet")
    ap.add_argument("--subnet", default="192.168.56")
    ap.add_argument("--wait", type=int, default=600, help="seconds to wait for the database")
    ap.add_argument("--java", default="")
    ap.add_argument("--jar", default="")
    args = ap.parse_args(argv)

    VBOX = find_vbox()
    if args.list_vms:
        for name, state in list_vms():
            print(f"{name}\t{state}")
        return 0
    if not args.vm:
        ap.error("--vm is required (see --list-vms)")
    if args.vm not in {n for n, _ in list_vms()}:
        die(f"no VM named {args.vm!r}; --list-vms shows them")
    steps = [s for s in args.steps.split(",") if s]
    bad = [s for s in steps if s not in STEPS]
    if bad:
        ap.error(f"unknown step(s) {bad}")

    out = Path(args.out_root)
    check_outside_repo(out)
    extract, vehicles, applic2, images, links = (
        out / "wis_extract", out / "wis_vehicles", out / "wis_applic2",
        out / "wis_images", out / "wis_links")
    for d in (extract, vehicles, applic2, images, links):
        d.mkdir(parents=True, exist_ok=True)
    online = [s for s in steps if s != "build"]
    started_here = vm_state(args.vm) != "running"

    if online:
        JAVA, STAGE = find_java(args.java), stage_scripts(find_jar(args.jar))
        log(f"VM {args.vm}; Java {JAVA}; output {out}")
        ip = vm_up(args.vm, args.port, args.subnet, args.wait)
        base = {"AR_HOST": ip, "AR_PORT": str(args.port), "AR_DB": args.db, "AR_LANG": args.lang}

        def cycle() -> str:
            vm_down(args.vm)
            time.sleep(60)          # let the host settle between heavy runs
            return vm_up(args.vm, args.port, args.subnet, args.wait)

        def pull(script: str, out_dir: Path, what: str) -> None:
            """Run a pull; on a dropped connection power-cycle and run it again.
            The in-VM database resets connections under sustained load (the
            first from-scratch run lost it on the last table of pull_applic2)."""
            for attempt in range(6):
                if nashorn(script, {**base, "AR_OUT": str(out_dir)}) == 0:
                    return
                log(f"{what} stopped (attempt {attempt + 1}); power-cycling and resuming")
                base["AR_HOST"] = cycle()
            die(f"{what} kept stopping; re-run to resume")

        if "docs" in steps:
            log("== documents (resumable; the DB sometimes drops -- the VM is power-cycled and the pull resumes)")
            pull("pull.js", extract, "document pull")
        if "applic" in steps:
            log("== model applicability")
            pull("pull_applic.js", extract, "applicability pull")
            pull("pull_applic2.js", applic2, "applicability-role pull")
        if "vehicles" in steps:
            log("== vehicle names and VIN tables")
            pull("pull_vehicles.js", vehicles, "vehicle-table pull")
        if "links" in steps:
            if not (links / "links_list.txt").is_file():
                log(f"== diagram links: {build_links_list(extract, links):,} documents listed")
            else:
                log("== diagram links (resuming)")
            pull("pull_links.js", links, "diagram-link pull")
        if "images" in steps:
            total = build_image_list(extract, images)
            log(f"== images: {total:,} graphics, {pulled_count(images):,} already pulled; "
                f"{BATCH:,} per batch, VM off between batches")
            stalls = 0
            while pulled_count(images) < total:
                before = pulled_count(images)
                nashorn("pull_images.js", {**base, "AR_OUT": str(images),
                                           "AR_SAMPLE": str(images / "list.tsv"),
                                           "AR_SLEEP": "25", "AR_LIMIT": str(BATCH)})
                got = pulled_count(images) - before
                log(f"batch: +{got:,} -> {pulled_count(images):,} / {total:,}")
                stalls = stalls + 1 if got == 0 else 0
                if stalls >= 3:
                    die("three batches in a row pulled nothing; re-run to resume")
                if pulled_count(images) < total:
                    base["AR_HOST"] = cycle()
        if started_here or "images" in steps:
            vm_down(args.vm)

    if "build" in steps:
        log("== offline build")
        py = sys.executable
        env = {"AR_OUT": str(extract), "AR_LANG": args.lang, "AR_VEH": str(vehicles),
               "AR_LINKS": str(links),
               "AR_HOST": "", "AR_DB": args.db, "AR_OUT_ROOT": str(out)}
        for cmd in (
            [py, "parse.py"],
            [py, "apply_applic.py", "--extract", str(extract), "--applic", str(applic2),
             "--applic1", str(extract), "--rebuild-join"],
            [py, "parse_vehicles.py"],
            [py, "parse_links.py"],
            [py, "convert_images.py", "--raw", str(images / "raw"),
             "--out", str(extract / "images"), "--jobs", "3"],
            [py, "apply_images.py", "--extract", str(extract)],
        ):
            if cmd[1] == "convert_images.py" and not (images / "raw").is_dir():
                log("no pulled images; skipping conversion")
                continue
            if run(cmd, env, HERE) != 0:
                die(f"{cmd[1]} failed")
        log(f"corpus ready: {extract}")
    log("done")
    return 0


VBOX = JAVA = ""
STAGE = Path(".")
STARTED: set[str] = set()     # VMs this run booted -- shut down however it ends

if __name__ == "__main__":
    try:
        rc = main()
    finally:
        # a failed step must not leave the VM running (the first from-scratch
        # run did, after the DB dropped mid-pull)
        for name in STARTED:
            try:
                vm_down(name)
            except Exception as exc:
                log(f"could not shut {name} down: {exc}")
    raise SystemExit(rc)
