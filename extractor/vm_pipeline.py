"""Mercedes WIS virtual machine -> AutoRundown corpus, end to end.

    python vm_pipeline.py --vm "Mercedes_Ultimate_207" [--out-root DIR] [--steps ...]
    python vm_pipeline.py --list-vms
    python vm_pipeline.py --vm X --steps ... --check          # will it work? (no boot)
    python vm_pipeline.py --vm X --steps ... --check --boot   # ... and boot it to see
    python vm_pipeline.py --vm X --fix-network                # Adapter 1 -> host-only

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
  EPC (not in the default run; --steps epc):
                    epc       pull_epc.js      the EPC parts catalogue (port 2034):
                                               passenger chassis + engine catalogs,
                                               drawings decoded to PNG, resumable
                                               per catalog -> <out>/epc_extract

What it does to the VM: before a run (or `--check --boot`) boots it, it takes
its own snapshot, "AutoRundown-auto-<date>-<time>" (`SNAPSHOT <name>`) --
unless it took one in the last 6 hours -- so a run that leaves Windows broken
can be rolled back (the user, 2026-10-16). It never deletes a snapshot
(deleting merges disk images: heavy disk I/O on a PC that has crashed under
it) and never restores one by itself: `--restore NAME` does, asked for
from Shop setup, after taking "AutoRundown-before-rollback-<...>" of the
current state so the rollback can be undone. `--list-snapshots` lists them.
Otherwise it only starts the VM headless, stops it, and reads the database
(SELECT only). It changes a network adapter only when asked: `--fix-network`
(Shop setup's "Fix it" on the network check, 2026-10-29) puts Adapter 1 on
the host-only network. Inside the guest it does one thing: when Windows is
up and signed in but the database doesn't answer, it types a Windows
Firewall rule letting ports 2020-2060 in from the host-only network
(`open_guest_firewall`, once per run). If the database still isn't
reachable it says what to set up, once, by hand.

It never pulls the plug on Windows (2026-10-16: a forced power-off after the
database didn't answer left the guest in Startup Repair). A shutdown that
doesn't finish saves the VM's state instead, and a saved state is resumed,
not discarded. If Windows itself doesn't come up, it saves a picture of the
VM's screen (`VM_SCREEN <path>`), saves the VM's state and stops, so the
cause is visible instead of guessed.

`--check` lists, without booting, what the run needs: VirtualBox, the VM, a
host-only network, Java, the JDBC driver, the scripts for the chosen steps,
free disk space -- one `CHECK <key> <ok|warn|fail> <message>` line each,
exit 0 / 2. `--check --boot` also boots the VM and checks Windows starts and
the databases answer, then shuts it down cleanly.

Cancelling: the caller creates the file named in AR_CANCEL_FILE; the run
stops the pull it is in (each one resumes), shuts the VM down cleanly and
exits 3. It does the same if the process in AR_PARENT_PID (the web server
that started it) goes away -- on 2026-10-16 a closed server left a run going.

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
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# no terminal window for VBoxManage / Java when run from the site (0 off Windows)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
sys.path.insert(0, str(HERE))  # the installed app's Python does not add a script's folder
from wis_paths import home, out_root, check_outside_repo  # noqa: E402
DEFAULT_OUT = out_root()
STEPS = ("docs", "applic", "vehicles", "links", "images", "build")   # the default: WIS
ALL_STEPS = STEPS + ("epc",)
BATCH = 15000
GZIP_MAGIC = bytes((0x1F, 0x8B))     # a SIDS body; anything else is an RTF/XML stub


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def die(msg: str) -> "None":
    log("STOP: " + msg)
    sys.exit(1)


# -- cancelling ----------------------------------------------------------------

CANCEL_FILE = os.environ.get("AR_CANCEL_FILE", "")
PARENT_PID = int(os.environ.get("AR_PARENT_PID") or 0)
EXIT_CANCELLED = 3
_cancel_why = ""


def _parent_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259      # STILL_ACTIVE
    try:
        os.kill(pid, 0)                            # POSIX only: signal 0 tests, it does not kill
        return True
    except OSError:
        return False


def cancelled() -> bool:
    """True once the caller asked to stop (AR_CANCEL_FILE) or went away."""
    global _cancel_why
    if not _cancel_why:
        if CANCEL_FILE and Path(CANCEL_FILE).exists():
            _cancel_why = "cancelled"
        elif PARENT_PID and not _parent_alive(PARENT_PID):
            _cancel_why = "the web server that started this run stopped"
    return bool(_cancel_why)


def stop_if_cancelled() -> None:
    if cancelled():
        log(f"STOP: {_cancel_why}; what was pulled so far is kept, a re-run resumes")
        sys.exit(EXIT_CANCELLED)


def sleep(seconds: float) -> None:
    """time.sleep that a cancel cuts short."""
    end = time.time() + seconds
    while time.time() < end:
        stop_if_cancelled()
        time.sleep(min(1.0, max(0.0, end - time.time())))


# -- tools -------------------------------------------------------------------

def find_vbox() -> str:
    for c in (shutil.which("VBoxManage"), r"C:\Program Files\Oracle\VirtualBox\VBoxManage.exe"):
        if c and Path(c).is_file():
            return c
    die("VBoxManage not found. Install VirtualBox, or add it to PATH.")


def java_path(explicit: str = "") -> str:
    """The EWA install's Java, or "" (then the run copies it out of the VM)."""
    if explicit and Path(explicit).is_file():
        return explicit
    # the 32-bit JRE bundled with the EWA install: its Nashorn runs the .js pulls
    # (not a Java on PATH: a newer one has no Nashorn)
    for base in (home() / "ewa_extract", home()):
        for p in base.glob("**/sun_jre/bin/java.exe"):
            return str(p)
    return ""


def jar_path(explicit: str = "") -> str:
    """The Transbase JDBC driver, or "" (then the run copies it out of the VM)."""
    for c in ([explicit] if explicit else []) + [str(p) for p in home().glob("**/tbjdbc.jar")]:
        if c and Path(c).is_file():
            return c
    return ""


def find_java(explicit: str = "") -> str:
    return java_path(explicit) or die(
        "Java not found. Pass --java <path to the EWA install's sun_jre\\bin\\java.exe>.")


def find_jar(explicit: str = "") -> str:
    return jar_path(explicit) or die("tbjdbc.jar (Transbase JDBC driver) not found; pass --jar.")


def vbox(*args: str, check: bool = True) -> str:
    # two VBoxManage calls on one VM at once (or the VirtualBox window
    # holding it) fail with "already has a lock request pending": a short
    # wait and another try (seen on the real VM, 2026-10-16)
    for attempt in range(6):
        r = subprocess.run([VBOX, *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
        if r.returncode == 0 or "lock request pending" not in r.stderr:
            break
        time.sleep(1 + attempt)
    if check and r.returncode != 0:
        raise RuntimeError(f"VBoxManage {' '.join(args)}: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def list_vms() -> list[tuple[str, str]]:
    """[(name, state)] for every VirtualBox VM on this PC."""
    running = {l.split('"')[1] for l in vbox("list", "runningvms").splitlines() if '"' in l}
    out = []
    for name in (l.split('"')[1] for l in vbox("list", "vms").splitlines() if '"' in l):
        st = "running" if name in running else vm_state(name)
        # "saved" matters to whoever picks the VM: it resumes where it was
        out.append((name, {"poweroff": "off", "aborted": "off (stopped abnormally)"}.get(st, st)))
    return out


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


def hostonly_ifs() -> list[tuple[str, str]]:
    """[(name, IPv4 address)] of this PC's VirtualBox host-only networks."""
    out, name = [], ""
    for line in vbox("list", "hostonlyifs", check=False).splitlines():
        if line.startswith("Name:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("IPAddress:") and name:
            out.append((name, line.split(":", 1)[1].strip()))
    return out


def fix_network(vm: str, subnet: str) -> None:
    """Put the VM's Adapter 1 on the host-only network -- only when a person
    asks (Shop setup's "Fix it" on the network check), the one VM setting
    this changes. No administrator rights needed: the host-only network
    itself already exists (VirtualBox makes one when it is installed)."""
    ifs = hostonly_ifs()
    if not ifs:
        die("this PC has no VirtualBox host-only network. In VirtualBox: File -> Tools -> Network "
            "Manager -> Host-only Networks -> Create (Windows asks for administrator rights).")
    name = next((n for n, ip in ifs if ip.startswith(subnet + ".")), ifs[0][0])
    state = vm_state(vm)
    if state == "running":
        vbox("controlvm", vm, "nic1", "hostonly", name)
    elif state in ("poweroff", "aborted"):
        vbox("modifyvm", vm, "--nic1", "hostonly", "--hostonlyadapter1", name)
    else:
        # a saved VM's adapters can't be changed, and discarding the state is a power-off
        die(f"{vm} is {state}: its network can't be changed now. Start it in VirtualBox, shut "
            f"Windows down from the Start menu, then try again.")
    log(f"NETWORK {vm}: Adapter 1 is now on the host-only network {name!r}")


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


# -- snapshots: ours, to roll back to -------------------------------------------

AUTO_PREFIX = "AutoRundown-auto-"
AUTO_EVERY_S = 6 * 3600       # at most one automatic snapshot per 6 hours
TOO_MANY = 5                  # more of ours than this: say to tidy up


def snapshots(vm: str) -> list[dict]:
    """[{name, uuid, description, current}] oldest first."""
    out, cur = {}, ""
    for line in vbox("snapshot", vm, "list", "--machinereadable", check=False).splitlines():
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip('"')
        if k == "CurrentSnapshotName":
            cur = v
        for field in ("SnapshotName", "SnapshotUUID", "SnapshotDescription"):
            if k == field or k.startswith(field + "-"):
                idx = k[len(field):]
                out.setdefault(idx, {})[field[8:].lower() or "name"] = v
    # keys SnapshotName, SnapshotName-1, SnapshotName-1-1 ...: listed parent
    # first, so this order is oldest first along the current branch
    snaps = list(out.values())
    for s in snaps:
        s["current"] = s.get("name") == cur
    return snaps


def _stamp(name: str) -> float:
    """The time in one of our names (AutoRundown-auto-20261016-131500), or 0."""
    try:
        return time.mktime(time.strptime(name.rsplit("-", 2)[-2] + name.rsplit("-", 1)[-1], "%Y%m%d%H%M%S"))
    except (ValueError, IndexError):
        return 0.0


def auto_snapshot(vm: str, why: str) -> str:
    """Our own snapshot before the run boots the VM (VirtualBox allows one
    of a running VM too: a few seconds' pause). Skipped if we took one in
    the last 6 hours -- a retry is not a new risk."""
    ours = [s["name"] for s in snapshots(vm) if s.get("name", "").startswith(AUTO_PREFIX)]
    recent = max((_stamp(n) for n in ours), default=0.0)
    if time.time() - recent < AUTO_EVERY_S:
        log(f"snapshot: using {max(ours, key=_stamp)} (taken in the last 6 hours)")
        return ""
    name = AUTO_PREFIX + time.strftime("%Y%m%d-%H%M%S")
    try:
        vbox("snapshot", vm, "take", name, "--description",
             f"Taken by AutoRundown before {why}. Roll back to it from Shop setup if the run "
             f"leaves Windows broken.")
    except RuntimeError as exc:
        die(f"could not take a snapshot of {vm} before the run: {exc}")
    log(f"SNAPSHOT {name}")
    return name


def restore_snapshot(vm: str, name: str) -> None:
    """Roll the VM back to `name` -- asked for by a person, never automatic.
    The current state is snapshotted first, so this can be undone."""
    if name not in {s.get("name") for s in snapshots(vm)}:
        die(f"{vm} has no snapshot named {name!r}")
    if vm_state(vm) == "running":
        die(f"{vm} is running: shut it down (or close its window, saving its state) before rolling back")
    keep = "AutoRundown-before-rollback-" + time.strftime("%Y%m%d-%H%M%S")
    vbox("snapshot", vm, "take", keep, "--description",
         f"The state before rolling back to {name}; restore this to undo the rollback.")
    log(f"SNAPSHOT {keep} (the state before the rollback, to undo it)")
    vbox("snapshot", vm, "restore", name)
    log(f"rolled {vm} back to {name}")


def logged_in(vm: str) -> bool:
    """Someone is signed in to Windows (Guest Additions say so): only then
    do typed keys reach a desktop, not the sign-in screen's password box."""
    out = vbox("guestproperty", "get", vm, "/VirtualBox/GuestInfo/OS/LoggedInUsers", check=False)
    return out.replace("Value:", "").strip() not in ("", "0", "No value set!")


# typed into the VM's keyboard (scancode set 1): VirtualBox needs no Windows
# logon for this, unlike guestcontrol, which refuses the VM's blank password
WIN_KEY = ("e0", "5b", "e0", "db")
CTRL_SHIFT_ENTER = ("1d", "2a", "1c", "9c", "aa", "9d")   # Start menu: run as administrator
ALT_Y = ("38", "15", "95", "b8")                           # UAC: Yes
ENTER = ("1c", "9c")
FW_RULE = "AutoRundownDB"
# Any port, from the host-only network only: WIS 2054 and EPC 2034 hand each
# connection on to a Transbase kernel on another port, so a rule for 2054,2034
# (and then 2020-2060) let the port answer and every login fail with "IO
# exception on socket" (2026-10-29). The host-only network reaches only this PC.
DB_PORTS = "any"
GUEST_SETUP: set[str] = set()                              # VMs this run typed the rule into


def open_guest_firewall(vm: str, subnet: str) -> None:
    """Let the two databases (and the servers they hand connections to)
    through Windows Firewall in the VM, from the
    host-only network only -- typed, once per run, when Windows is up and
    signed in but the database doesn't answer (2026-10-29: the VM reverted
    to its original snapshot had the firewall on, the one step every new
    user had to do inside the VM by hand). A rule, not the firewall off.
    Start menu -> cmd -> Ctrl+Shift+Enter -> Alt+Y (EMO is an administrator,
    so UAC asks only for Yes); tested on Mercedes_Ultimate_207. Where it
    doesn't fit (UAC asks for a password, another layout) nothing worse than
    a stray command window happens, and the message after it says what to do
    by hand. The run's own snapshot is taken before this."""
    GUEST_SETUP.add(vm)
    log(f"database not answering; typing a Windows Firewall rule into {vm}: TCP in from "
        f"{subnet}.x (this PC's host-only network) only, rule {FW_RULE!r}")

    def keys(codes):
        vbox("controlvm", vm, "keyboardputscancode", *codes, check=False)
    keys(WIN_KEY)
    sleep(2)
    vbox("controlvm", vm, "keyboardputstring", "cmd", check=False)
    sleep(2)
    keys(CTRL_SHIFT_ENTER)
    sleep(5)
    keys(ALT_Y)
    sleep(4)
    # delete first so a re-run doesn't pile up copies; '&' runs each in turn
    vbox("controlvm", vm, "keyboardputstring",
         f"netsh advfirewall firewall delete rule name={FW_RULE} & netsh advfirewall firewall add "
         f"rule name={FW_RULE} dir=in action=allow protocol=TCP localport={DB_PORTS} "
         f"remoteip={subnet}.0/24 & exit", check=False)
    keys(ENTER)


EWA_SHARE = "ar_ewa"
# inside the VM: where the EWA install keeps them (Mercedes_Ultimate_207 has
# C:\Program Files\EWA; (x86) in case another install put it there)
EWA_JRE, EWA_LIB = r"EWA\apps\jre\sun_jre", r"EWA\server\lib"
EWA_JAR = EWA_LIB + r"\tbjdbc.jar"


def ewa_copy_command(share: str = EWA_SHARE) -> str:
    """The line typed into a cmd window in the VM: copy the EWA install's Java
    and JDBC driver to the shared folder, keeping their paths under
    "Program Files" (what java_path/jar_path look for), then write
    copied.txt so this PC knows it finished, and close the window."""
    dst = rf"\\vboxsvr\{share}\Program Files"
    one = (rf'if exist "%~d\{EWA_JRE}\bin\java.exe" (xcopy "%~d\{EWA_JRE}" "{dst}\{EWA_JRE}\" /E /I /Y /Q'
           rf' & xcopy "%~d\{EWA_JAR}" "{dst}\{EWA_LIB}\" /Y /Q)')
    # the for loop in brackets: otherwise "& exit" would run inside it, after
    # the first folder
    return (rf'(for %d in ("C:\Program Files" "C:\Program Files (x86)") do {one})'
            rf' & echo done> "\\vboxsvr\{share}\copied.txt" & exit')


def copy_ewa_from_vm(vm: str, dest: Path, wait_s: int) -> None:
    """Copy the EWA install's Java (sun_jre, ~175 MB) and tbjdbc.jar out of
    the VM into `dest` (<Mercedes Documents>/ewa_extract), for a PC that has
    never had them: they are Mercedes software, so the app can't ship them,
    and before this a person copied them out of the VM's disk by hand.

    Typed into the VM's keyboard like the firewall rule (guestcontrol refuses
    the VM's blank password); the files go through a transient shared folder
    (Guest Additions), removed again afterwards. Read-only in the VM: xcopy
    from its Program Files to the share. The run's own snapshot is taken
    before this."""
    dest.mkdir(parents=True, exist_ok=True)
    marker = dest / "copied.txt"
    marker.unlink(missing_ok=True)
    log("this PC has no copy of the EWA install's Java and JDBC driver; copying them out of "
        f"{vm} into {dest} (about 175 MB, once)")
    if vm_state(vm) != "running":
        log(f"starting {vm} headless")
        try:
            vbox("startvm", vm, "--type", "headless")
        except RuntimeError as exc:
            die(f"VirtualBox could not start {vm}: {exc}")
        STARTED.add(vm)
    deadline = time.time() + wait_s
    while not (windows_up(vm) and logged_in(vm)):
        stop_if_cancelled()
        if time.time() > deadline:
            screenshot(vm)
            die(f"Windows in {vm} did not reach a signed-in desktop within {wait_s // 60} min, so "
                f"Java and the JDBC driver could not be copied out of it -- see the picture of its "
                f"screen. Sign in to Windows in the VM (VirtualBox: select it, Show), then run this "
                f"again.")
        time.sleep(5)
    sleep(20)                       # let the desktop finish starting before typing into it
    vbox("sharedfolder", "remove", vm, "--name", EWA_SHARE, "--transient", check=False)
    try:
        vbox("sharedfolder", "add", vm, "--name", EWA_SHARE, "--hostpath", str(dest), "--transient")
    except RuntimeError as exc:
        die(f"VirtualBox could not share a folder with {vm} ({exc}); it needs the Guest "
            f"Additions. Or copy sun_jre and tbjdbc.jar from the VM's C:\\Program Files\\EWA into "
            f"{dest} by hand, keeping their folders.")
    try:
        def keys(codes):
            vbox("controlvm", vm, "keyboardputscancode", *codes, check=False)
        keys(WIN_KEY)
        sleep(2)
        vbox("controlvm", vm, "keyboardputstring", "cmd", check=False)
        sleep(2)
        keys(ENTER)
        sleep(4)
        vbox("controlvm", vm, "keyboardputstring", ewa_copy_command(), check=False)
        keys(ENTER)
        end = time.time() + 600
        while not marker.is_file():
            stop_if_cancelled()
            if time.time() > end:
                screenshot(vm)
                die(f"Copying Java and the JDBC driver out of {vm} did not finish within 10 "
                    f"minutes -- see the picture of its screen.")
            time.sleep(3)
    finally:
        vbox("sharedfolder", "remove", vm, "--name", EWA_SHARE, "--transient", check=False)
    marker.unlink(missing_ok=True)
    if not (java_path() and jar_path()):
        screenshot(vm)
        die(f"{vm} has no EWA install where it was looked for (C:\\Program Files\\{EWA_JRE} "
            f"and {EWA_JAR}), so nothing was copied. Copy sun_jre and tbjdbc.jar from the EWA "
            f"install in the VM into {dest} by hand, keeping their folders, or pass --java "
            f"and --jar.")
    log(f"copied: Java {java_path()}, driver {jar_path()}")


def ewa_ready(vm: str, java: str, jar: str, wait_s: int) -> None:
    """Java and the JDBC driver on this PC: found, or copied out of the VM."""
    if not (java_path(java) and jar_path(jar)):
        copy_ewa_from_vm(vm, home() / "ewa_extract", wait_s)


DB_NAMES: dict[int, str] = {2054: "wisnet", 2034: "SPECIALFILES"}   # port -> database (set from the arguments)


def db_login(ip: str, port: int) -> bool | str:
    """True if this PC can log in to the database on `port` (db_login.js,
    over JDBC, read-only: it connects and closes), else the driver's error.
    True when Java isn't set up in this run: then nothing logs in anyway."""
    if not JAVA or not (STAGE / "db_login.js").is_file():
        return True
    try:
        r = subprocess.run([JAVA, "-cp", "tbjdbc.jar", "jdk.nashorn.tools.Shell", "db_login.js"],
                           cwd=STAGE, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=90, creationflags=NO_WINDOW, check=False,
                           env={**os.environ, "AR_HOST": ip, "AR_PORT": str(port),
                                "AR_DB": DB_NAMES.get(port, "wisnet"),
                                # the users the pulls log in as (tbadmin on EPC: error 16004)
                                "AR_USER": "tbuser" if DB_NAMES.get(port) == "SPECIALFILES" else "tbadmin"})
    except subprocess.TimeoutExpired:
        return "no answer within 90 s"
    out = next((l for l in r.stdout.splitlines() if l.startswith("LOGIN ")), "")
    return True if out.startswith("LOGIN OK") else (out[len("LOGIN FAIL "):] or
                                                     (r.stderr.strip().splitlines() or ["no output"])[-1])


def windows_up(vm: str) -> bool:
    """Windows in the guest has started its services: Guest Additions report
    run level 2 (userland) or 3 (desktop). 0 while it boots -- or while it
    sits in Startup Repair, which never gets that far."""
    for line in vbox("showvminfo", vm, "--machinereadable", check=False).splitlines():
        if line.startswith("GuestAdditionsRunLevel="):
            return line.split("=", 1)[1].strip('"') in ("2", "3")
    return False


def screenshot(vm: str) -> str:
    """A picture of the VM's screen, for the person reading why it stopped."""
    path = SHOT_DIR / "vm_screen.png"
    try:
        path.unlink(missing_ok=True)
        vbox("controlvm", vm, "screenshotpng", str(path))
        log(f"VM_SCREEN {path}")
        return str(path)
    except (OSError, RuntimeError) as exc:
        log(f"(no picture of the VM's screen: {exc})")
        return ""


def save_state(vm: str, why: str) -> None:
    """Freeze the VM where it is (never a power-off: that can leave Windows
    in Startup Repair). Starting it again in VirtualBox resumes it there."""
    log(f"saving {vm}'s state ({why}); open it in VirtualBox to see it as it is")
    vbox("controlvm", vm, "savestate", check=False)
    STARTED.discard(vm)


def vm_up(vm: str, port: int, subnet: str, wait_s: int) -> str:
    """Boot (if needed) and return the DB's address. If Windows came up but
    the database didn't, one more boot after a clean shutdown (seen once in
    the from-scratch run: after 12 clean batches, one boot left the DB dark
    for 10 minutes). If Windows itself never came up, stop: a picture of the
    screen and the VM's state are kept, nothing is forced off."""
    for attempt in (1, 2):
        ip = _vm_up_once(vm, port, subnet, wait_s, final=attempt == 2)
        if ip:
            return ip
        log("database never came up; shutting down cleanly for one more boot")
        vm_down(vm)
        if vm_state(vm) == "saved":
            # Windows ignored the power button, so the "boot" would only
            # resume the same Windows (2026-10-23: a blue screen behind it,
            # resumed twice, 20 minutes lost). A person has to look.
            die(f"Windows in {vm} did not shut down when asked, so another boot would resume it "
                f"as it is -- see the picture of its screen. Open the VM in VirtualBox: if it "
                f"shows an error (a blue screen), use Machine > Reset; then shut it down from the "
                f"Start menu, or roll back to a snapshot in Shop setup, and run this again.")
        sleep(60)
    die("unreachable")   # _vm_up_once(final=True) already stopped with the reason


def _vm_up_once(vm: str, port: int, subnet: str, wait_s: int, final: bool) -> str | None:
    state = vm_state(vm)
    if state == "saved":
        # resumed as it was: discarding a saved state is a power-off for the
        # guest. (A state saved under another VirtualBox version may not
        # resume; then it says so and stops.)
        log("VM has a saved state; resuming it")
    if state not in ("running",):
        log(f"starting {vm} headless")
        try:
            vbox("startvm", vm, "--type", "headless")
        except RuntimeError as exc:
            die(f"VirtualBox could not start {vm}: {exc}")
        STARTED.add(vm)
    deadline = time.time() + wait_s
    told = said_login = False
    while time.time() < deadline:
        stop_if_cancelled()
        ip = find_db(vm, port, subnet)
        if ip:
            # the port answering is not a login (2026-10-29: a firewall rule
            # for the port alone passed this and failed every pull)
            login = db_login(ip, port)
            if login is True:
                log(f"database answering at {ip}:{port} and a login works; letting it settle 30 s")
                sleep(30)
                return ip
            if not said_login:
                log(f"the database's port answers at {ip}:{port} but a login fails: {login}")
                said_login = True
        if vm not in GUEST_SETUP and windows_up(vm) and logged_in(vm) \
                and time.time() > deadline - wait_s + 90:
            open_guest_firewall(vm, subnet)
        if not told and time.time() > deadline - wait_s + 240:
            log("database not answering yet. Once, inside the VM (VirtualBox: select it, Show): "
                "sign in to Windows, start 'Mercedes WSM EPC' (it starts the database), and let "
                "ports 2054 and 2034 through Windows Firewall (or turn it off). Still checking...")
            told = True
        time.sleep(5)
    if not windows_up(vm):
        screenshot(vm)
        save_state(vm, "Windows did not start")
        die(f"Windows in {vm} did not start within {wait_s // 60} min -- see the picture of "
            f"its screen. Open the VM in VirtualBox and get Windows to start (e.g. let Startup "
            f"Repair finish), shut it down from the Start menu, then run this again.")
    if final:
        screenshot(vm)
        die(f"Windows is up but this PC can't log in to the database on port {port} -- see "
            f"the message above.")
    return None


def vm_down(vm: str) -> None:
    """A clean shutdown (ACPI power button). If Windows doesn't finish in
    2 min, the VM's state is saved -- never a power-off."""
    if vm_state(vm) != "running":
        return
    log(f"shutting {vm} down")
    vbox("controlvm", vm, "acpipowerbutton", check=False)
    for _ in range(24):
        if vm_state(vm) == "poweroff":
            return
        time.sleep(5)
    screenshot(vm)
    save_state(vm, "no clean shutdown after 2 min")


# -- will it work? ---------------------------------------------------------------

# the scripts each step runs (the epc ones: an older extractor copy has none)
STEP_SCRIPTS = {"docs": ["pull.js"], "applic": ["pull_applic.js", "pull_applic2.js"],
                "vehicles": ["pull_vehicles.js"], "links": ["pull_links.js"],
                "images": ["pull_images.js"], "epc": ["pull_epc.js", "epc_fv4.js"],
                "build": ["parse.py", "apply_applic.py", "parse_vehicles.py", "parse_links.py",
                          "convert_images.py", "apply_images.py"]}
# free space wanted on the output drive: images are ~11 GB converted plus the
# raw pull; the rest is a few GB
NEED_GB = {"images": 30, "epc": 10}


def check(key: str, level: str, msg: str) -> str:
    print(f"CHECK\t{key}\t{level}\t{msg}", flush=True)
    return level


def preflight(vm: str, steps: list[str], out: Path, subnet: str, java: str, jar: str) -> list[str]:
    """What this run needs, without booting anything: one CHECK line each."""
    levels = []
    online = [s for s in steps if s != "build"]
    names = {n: st for n, st in list_vms()}
    if vm not in names:
        return [check("vm", "fail", f"There is no VirtualBox VM named {vm!r}.")]
    info = dict(l.split("=", 1) for l in vbox("showvminfo", vm, "--machinereadable").splitlines()
                if "=" in l)
    state = info.get("VMState", "").strip('"')
    if state == "running":
        levels.append(check("vm", "warn", f"{vm} is already running: the run uses it as it is and "
                                          "leaves it running. Close any VirtualBox window on it."))
    elif state == "saved":
        levels.append(check("vm", "warn", f"{vm} has a saved state: it will be resumed where it was "
                                          "(not discarded). If Windows was stuck, open it in "
                                          "VirtualBox and sort that out first."))
    elif state in ("aborted", "gurumeditation", "stuck"):
        levels.append(check("vm", "warn", f"{vm} was last stopped abnormally ({state}): Windows may "
                                          "run a disk check or Startup Repair on its next boot."))
    else:
        levels.append(check("vm", "ok", f"{vm} is off and ready to start."))
    if online:
        ours = [s["name"] for s in snapshots(vm) if s.get("name", "").startswith(AUTO_PREFIX)]
        recent = max((_stamp(n) for n in ours), default=0.0)
        note = (f"Uses the snapshot {max(ours, key=_stamp)}, taken in the last 6 hours."
                if time.time() - recent < AUTO_EVERY_S else
                "A snapshot of the VM is taken before it starts, to roll back to if Windows breaks.")
        levels.append(check("snapshot", "warn", f"{note} There are {len(ours)} AutoRundown snapshots: "
                                                "delete old ones in VirtualBox (Snapshots) to free disk "
                                                "space -- AutoRundown never deletes them.")
                      if len(ours) > TOO_MANY else check("snapshot", "ok", note))
        hostonly = [k for k, v in info.items() if k.startswith("nic") and v.strip('"') == "hostonly"]
        if not hostonly and not hostonly_ifs():
            # making one installs a network driver: administrator rights, so a person does it
            levels.append(check("hostonly", "fail", "This PC has no VirtualBox host-only network, so it "
                                                    "cannot reach the VM's database. In VirtualBox: File "
                                                    "-> Tools -> Network Manager -> Host-only Networks -> "
                                                    "Create (Windows asks for administrator rights), then "
                                                    "check again."))
        elif not hostonly:
            # the "network" key with a fail is what Shop setup offers "Fix it" for (--fix-network)
            levels.append(check("network", "fail", f"{vm} has no host-only network adapter: this PC "
                                                   "cannot reach its database. Fix it sets Adapter 1 to "
                                                   "Host-only Adapter (or in VirtualBox: Settings -> "
                                                   "Network -> Adapter 1 -> Host-only Adapter)."))
        else:
            host_ips = [ip for _, ip in hostonly_ifs()]
            if not any(ip.startswith(subnet + ".") for ip in host_ips):
                levels.append(check("network", "warn", f"The host-only network is not on {subnet}.x "
                                                       f"(found {', '.join(host_ips) or 'none'}); the "
                                                       "database may not be found."))
            else:
                levels.append(check("network", "ok", "Host-only network adapter."))
        ga = vbox("guestproperty", "get", vm, "/VirtualBox/GuestAdd/Version", check=False)
        if "Value:" not in ga:
            levels.append(check("guest", "warn", "No Guest Additions seen in this VM: whether Windows "
                                                 "started can't be told apart from the database not "
                                                 "answering."))
        j, jr = java_path(java), jar_path(jar)
        if j and jr:
            levels.append(check("java", "ok", f"Java: {j}"))
            levels.append(check("jdbc", "ok", "Transbase JDBC driver found."))
        elif "Value:" in ga:
            levels.append(check("java", "ok", "Java and the Transbase JDBC driver are copied out of the VM "
                                              "on the first run (about 175 MB, once), into "
                                              f"{home() / 'ewa_extract'}."))
        else:
            levels.append(check("java", "fail", "Java and the Transbase JDBC driver (from the EWA install in "
                                                "the VM) are not on this PC, and without Guest Additions "
                                                "they can't be copied out of the VM. Copy sun_jre and "
                                                "tbjdbc.jar from the VM's C:\\Program Files\\EWA into "
                                                f"{home() / 'ewa_extract'} by hand, keeping their folders."))
    missing = [f for s in steps for f in STEP_SCRIPTS.get(s, []) if not (HERE / f).is_file()]
    if online and not (HERE / "db_login.js").is_file():
        missing.append("db_login.js")      # the login check every online run makes
    levels.append(check("scripts", "fail", f"This extractor copy ({HERE}) is missing {', '.join(missing)}: "
                                           "copy a current extractor folder over it.") if missing else
                  check("scripts", "ok", "The extractor has every script these steps need."))
    try:
        check_outside_repo(out)
        out.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(out).free / 1e9
        need = max([NEED_GB.get(s, 5) for s in steps] or [5])
        levels.append(check("disk", "ok", f"{free:,.0f} GB free for {out}.") if free >= need else
                      check("disk", "warn" if free >= need / 3 else "fail",
                            f"Only {free:,.0f} GB free for {out}; these steps want about {need} GB."))
    except (OSError, SystemExit) as exc:
        levels.append(check("disk", "fail", f"The output folder {out} can't be used: {exc}"))
    return levels


def boot_check(vm: str, steps: list[str], subnet: str, wait_s: int, wis_port: int,
               epc_port: int) -> list[str]:
    """Boot the VM and see that Windows starts and the databases answer; shut
    it down cleanly afterwards if this check started it."""
    levels = []
    ports = ([("WIS", wis_port)] if any(s in steps for s in STEP_SCRIPTS if s not in ("epc", "build"))
             else []) + ([("EPC", epc_port)] if "epc" in steps else [])
    started = vm_state(vm) != "running"
    first = True
    for name, port in ports:
        log(f"checking the {name} database (port {port})")
        try:
            ip = _vm_up_once(vm, port, subnet, wait_s if first else 120, final=True)
            first = False
            levels.append(check(f"db_{name.lower()}", "ok", f"Windows started and this PC can log in "
                                                           f"to the {name} database at {ip}:{port}."))
        except SystemExit:
            if cancelled():
                raise
            levels.append(check(f"db_{name.lower()}", "fail", f"The {name} database did not answer -- "
                                                             "see the log and the picture of the VM's screen."))
            return levels
    if started:
        vm_down(vm)
    return levels


# -- running the pieces ----------------------------------------------------------

# Errors that a power-cycle cannot fix: the pull itself is wrong, not the
# database. On 2026-10-15 an OutOfMemoryError in pull_epc.js was retried six
# times, the VM rebooted each time; on 2026-10-23 a script bug (a Java
# array's missing forEach) was too. A null from a lost database can raise a
# TypeError, so only the messages a code mistake gives are listed.
FATAL_MARKERS = ("OutOfMemoryError", "SyntaxError", "ReferenceError",
                 "has no such function", "is not a function")   # never a dropped connection
LAST_FATAL = ""


def run(cmd: list[str], env: dict, cwd: Path) -> int:
    global LAST_FATAL
    LAST_FATAL = ""
    log("$ " + " ".join(Path(c).name if i == 0 else c for i, c in enumerate(cmd)))
    p = subprocess.Popen(cmd, cwd=cwd, env={**os.environ, **env, "PYTHONIOENCODING": "utf-8"},
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", bufsize=1, creationflags=NO_WINDOW)

    def watch() -> None:       # a cancel stops the pull (each one resumes where it stopped)
        while p.poll() is None:
            if cancelled():
                p.terminate()
                return
            time.sleep(1)
    threading.Thread(target=watch, daemon=True).start()
    for line in p.stdout:
        line = line.rstrip()
        # Nashorn stack traces are dozens of lines of noise; keep the message
        if line.startswith(("\tat ", "Caused by")) or line.startswith("at "):
            continue
        if not LAST_FATAL:
            LAST_FATAL = next((m for m in FATAL_MARKERS if m in line), "")
        print("    " + line, flush=True)
    rc = p.wait()
    stop_if_cancelled()        # stopped by a cancel: not a failure to retry
    return rc


def stage_scripts(jar: str) -> Path:
    """Nashorn is fussy about spaces in paths: run the .js from a clean dir."""
    stage = Path(tempfile.gettempdir()) / "ar_extract_stage"
    stage.mkdir(exist_ok=True)
    shutil.copy2(jar, stage / "tbjdbc.jar")
    for js in HERE.glob("*.js"):
        shutil.copy2(js, stage / js.name)
    return stage


def nashorn(script: str, env: dict, heap: str = "") -> int:
    # the bundled JRE is 32-bit: a heap over ~1.2 GB cannot be reserved
    jvm = [f"-Xmx{heap}"] if heap else []
    return run([JAVA, *jvm, "-cp", "tbjdbc.jar", "jdk.nashorn.tools.Shell", script], env, STAGE)


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
                    help=f"comma list of {', '.join(ALL_STEPS)} (default: the WIS ones)")
    ap.add_argument("--lang", default="2", help="WIS language code (2 = English)")
    ap.add_argument("--port", type=int, default=2054)
    ap.add_argument("--db", default="wisnet")
    ap.add_argument("--subnet", default="192.168.56")
    ap.add_argument("--wait", type=int, default=600, help="seconds to wait for the database")
    ap.add_argument("--java", default="")
    ap.add_argument("--jar", default="")
    ap.add_argument("--epc-port", type=int, default=2034,
                    help="the EPC database (a separate Transbase instance from WIS's)")
    ap.add_argument("--epc-catalogs", default="all,engines",
                    help="pull_epc.js AR_CATNUM: all (chassis), engines, or catalog numbers")
    ap.add_argument("--check", action="store_true",
                    help="say whether the run would work (CHECK lines), without booting")
    ap.add_argument("--boot", action="store_true",
                    help="with --check: also boot the VM, check Windows and the databases")
    ap.add_argument("--list-snapshots", action="store_true",
                    help="the VM's snapshots: name, ours (auto/rollback) or not, current, description")
    ap.add_argument("--restore", default="",
                    help="roll the VM back to this snapshot (the current state is snapshotted first)")
    ap.add_argument("--fix-network", action="store_true",
                    help="put the VM's Adapter 1 on the host-only network (asked for from Shop setup)")
    args = ap.parse_args(argv)
    global SHOT_DIR

    VBOX = find_vbox()
    if args.list_vms:
        for name, state in list_vms():
            print(f"{name}\t{state}")
        return 0
    if not args.vm:
        ap.error("--vm is required (see --list-vms)")
    if args.vm not in {n for n, _ in list_vms()}:
        die(f"no VM named {args.vm!r}; --list-vms shows them")
    if args.list_snapshots:
        for sn in snapshots(args.vm):
            ours = sn.get("name", "").startswith(("AutoRundown-auto-", "AutoRundown-before-rollback-"))
            print("\t".join(["SNAP", sn.get("name", ""), "ours" if ours else "",
                             "current" if sn["current"] else "",
                             sn.get("description", "").replace("\t", " ").replace("\n", " ")]))
        return 0
    if args.restore:
        restore_snapshot(args.vm, args.restore)
        return 0
    if args.fix_network:
        fix_network(args.vm, args.subnet)
        return 0
    steps =[s for s in args.steps.split(",") if s]
    bad = [s for s in steps if s not in ALL_STEPS]
    if bad:
        ap.error(f"unknown step(s) {bad}")

    out = Path(args.out_root)
    DB_NAMES.clear()
    DB_NAMES.update({args.port: args.db, args.epc_port: "SPECIALFILES"})
    if args.check:
        levels = preflight(args.vm, steps, out, args.subnet, args.java, args.jar)
        if args.boot and "fail" not in levels:
            SHOT_DIR = out
            auto_snapshot(args.vm, "a VM test")
            # Java for the real login (db_login.js): copied out of the VM if
            # this PC has none (the preflight said it would be)
            ewa_ready(args.vm, args.java, args.jar, args.wait)
            JAVA, STAGE = find_java(args.java), stage_scripts(find_jar(args.jar))
            levels += boot_check(args.vm, steps, args.subnet, args.wait, args.port, args.epc_port)
        log("check: " + ("ready" if "fail" not in levels else "NOT ready -- see the CHECK lines"))
        return 0 if "fail" not in levels else 2
    check_outside_repo(out)
    SHOT_DIR = out
    extract, vehicles, applic2, images, links = (
        out / "wis_extract", out / "wis_vehicles", out / "wis_applic2",
        out / "wis_images", out / "wis_links")
    for d in (extract, vehicles, applic2, images, links):
        d.mkdir(parents=True, exist_ok=True)
    online = [s for s in steps if s != "build"]
    started_here = vm_state(args.vm) != "running"

    if online:
        auto_snapshot(args.vm, f"an extraction ({','.join(steps)})")
        ewa_ready(args.vm, args.java, args.jar, args.wait)
        JAVA, STAGE = find_java(args.java), stage_scripts(find_jar(args.jar))
        log(f"VM {args.vm}; Java {JAVA}; output {out}")
        # the database the first step needs: WIS (2054), or EPC (2034) alone
        port = args.epc_port if online == ["epc"] else args.port
        ip = vm_up(args.vm, port, args.subnet, args.wait)
        base = {"AR_HOST": ip, "AR_PORT": str(args.port), "AR_DB": args.db, "AR_LANG": args.lang}

        def cycle(port: int = args.port) -> str:
            vm_down(args.vm)
            sleep(60)               # let the host settle between heavy runs
            return vm_up(args.vm, port, args.subnet, args.wait)

        def pull(script: str, out_dir: Path, what: str, env: dict | None = None,
                 port: int = args.port, heap: str = "") -> None:
            """Run a pull; on a dropped connection power-cycle and run it again.
            The in-VM database resets connections under sustained load (the
            first from-scratch run lost it on the last table of pull_applic2)."""
            for attempt in range(6):
                if nashorn(script, {**base, **(env or {}), "AR_OUT": str(out_dir)}, heap) == 0:
                    return
                if LAST_FATAL:
                    die(f"{what} failed with {LAST_FATAL} -- a problem in the pull itself, "
                        f"not the database, so restarting the VM would not help. What it "
                        f"saved so far is kept; a re-run after the fix resumes.")
                log(f"{what} stopped (attempt {attempt + 1}); power-cycling and resuming")
                base["AR_HOST"] = cycle(port)
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
        if "epc" in steps:
            epc = out / "epc_extract"
            epc.mkdir(parents=True, exist_ok=True)
            if online != ["epc"]:      # the WIS steps ran first: find the EPC database
                base["AR_HOST"] = vm_up(args.vm, args.epc_port, args.subnet, args.wait)
            log(f"== EPC parts catalogue ({args.epc_catalogs}); resumes per catalog, "
                f"drawings already on disk are reused")
            # AR_LANG is EPC's own one-letter code, not WIS's number
            pull("pull_epc.js", epc, "EPC pull",
                 env={"AR_PORT": str(args.epc_port), "AR_LANG": "E",
                      "AR_CATNUM": args.epc_catalogs, "AR_RESUME": "1",
                      "AR_SLEEP_MS": "20", "AR_TBJDBC": "tbjdbc.jar"},
                 port=args.epc_port, heap="1024m")
            log(f"EPC corpus ready: {epc}")
        if started_here or "images" in steps or "epc" in steps:
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
SHOT_DIR = Path(tempfile.gettempdir())   # vm_screen.png goes here (the output folder, once known)
STARTED: set[str] = set()     # VMs this run booted -- shut down however it ends

def shut_down_started() -> None:
    """A failed or cancelled run must not leave the VMs it booted running
    (the first from-scratch run did, after the DB dropped mid-pull). Over a
    copy: vm_down saving a VM's state takes it out of STARTED -- iterating
    the set itself crashed on the real VM (2026-10-16, a cancel)."""
    for name in list(STARTED):
        try:
            vm_down(name)
        except Exception as exc:
            log(f"could not shut {name} down: {exc}")


if __name__ == "__main__":
    try:
        rc = main()
    finally:
        shut_down_started()
    raise SystemExit(rc)
