"""AutoRundown on a headless Linux server -- FREE, ~1 s.

    python tests/test_linux_server.py

No VM and no real Java needed (a stand-in Java runs where a shell can run
it); the parts that differ off Windows are plain functions:

- the installed app's Mercedes folder: ~/.local/share (XDG_DATA_HOME), never
  a Windows-style ~/AppData/Local; Windows keeps %LOCALAPPDATA%;
- a source checkout copied without .git (a test box) is still a checkout;
- the VM pull's Java: off Windows the EWA's java.exe can't run, so the first
  Java that runs Nashorn (AR_JAVA, the distro's 8/11, PATH) -- one without it
  (Java 15+) is never picked, and with none the run stops before the VM is
  touched, saying what to install;
- packaging/linux: the systemd unit and install.sh are filled in and parse.
"""
from __future__ import annotations

from _paths import ROOT
import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

sys.path.insert(0, str(ROOT / "extractor"))
import vm_pipeline as vp  # noqa: E402
import wis_paths  # noqa: E402

FAILS = 0
TMP = Path(tempfile.mkdtemp(prefix="ar_linux_"))


def check(name: str, ok: bool, got=None) -> None:
    global FAILS
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f"\n       got: {got!r}"))
    FAILS += not ok


def fake_os(name: str, env: dict):
    """wis_paths' view of the OS: its name and environment, nothing else."""
    return types.SimpleNamespace(name=name, environ=env)


def load_wis_paths(folder: Path):
    spec = importlib.util.spec_from_file_location(f"_wp_{folder.name}", folder / "extractor" / "wis_paths.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def mercedes_folder() -> None:
    print("the installed app's Mercedes folder")
    real = wis_paths.os
    try:
        wis_paths.os = fake_os("posix", {})
        got = wis_paths.installed_home()
        check("Linux: ~/.local/share/AutoRundown/Mercedes data",
              got == Path.home() / ".local" / "share" / "AutoRundown" / "Mercedes data", got)
        check("... never a Windows-style AppData folder", "AppData" not in got.parts, got)
        wis_paths.os = fake_os("posix", {"XDG_DATA_HOME": str(TMP / "xdg")})
        got = wis_paths.installed_home()
        check("... XDG_DATA_HOME when it is set", got == TMP / "xdg" / "AutoRundown" / "Mercedes data", got)
        wis_paths.os = fake_os("posix", {"AUTORUNDOWN_HOME": str(TMP / "ar")})
        got = wis_paths.installed_home()
        check("... AUTORUNDOWN_HOME still wins (tests, a second instance)", got == TMP / "ar" / "Mercedes data", got)
        wis_paths.os = fake_os("nt", {"LOCALAPPDATA": str(TMP / "lad")})
        got = wis_paths.installed_home()
        check("Windows: unchanged, %LOCALAPPDATA%", got == TMP / "lad" / "AutoRundown" / "Mercedes data", got)
    finally:
        wis_paths.os = real

    print("a source checkout without .git")
    for name, marks in (("box", ["tests"]), ("clone", [".git"]), ("msi", [])):
        top = TMP / name
        (top / "extractor").mkdir(parents=True)
        shutil.copy2(ROOT / "extractor" / "wis_paths.py", top / "extractor")
        for m in marks:
            (top / m).mkdir()
        mod = load_wis_paths(top)
        if name == "msi":
            check("an installed app (no tests/, no .git) is not a checkout", mod.REPO is None, mod.REPO)
        else:
            check(f"a {name} ({marks[0]}/) is a checkout", mod.REPO == top, mod.REPO)


def java() -> None:
    print("the VM pull's Java off Windows")
    saved = vp.WINDOWS, vp.linux_javas, vp.has_nashorn
    j8, j17, other = TMP / "jvm8" / "java", TMP / "jvm17" / "java", TMP / "mine" / "java"
    for p in (j8, j17, other):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
    nashorn = {str(j8), str(other)}
    calls = []
    try:
        vp.WINDOWS = False
        vp.has_nashorn = lambda j: j in nashorn
        vp.linux_javas = lambda: [str(j17), str(j8)]
        check("the first Java that runs Nashorn, not a newer one without it",
              vp.java_path() == str(j8), vp.java_path())
        check("... --java first when it has Nashorn", vp.java_path(str(other)) == str(other))
        check("... --java without Nashorn is passed over", vp.java_path(str(j17)) == str(j8))
        check("... the EWA's java.exe is never looked for", vp.java_path() != "" and
              not vp.java_path().endswith(".exe"))
        vp.linux_javas = lambda: [str(j17)]
        check("no Java with Nashorn: none", vp.java_path() == "")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                vp.find_java()
                stopped = False
            except SystemExit:
                stopped = True
        check("... the run stops, saying what to install",
              stopped and "openjdk-8-jre-headless" in out.getvalue(), out.getvalue())
        real_copy = vp.copy_ewa_from_vm
        vp.copy_ewa_from_vm = lambda *a: calls.append(a)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                try:
                    vp.ewa_ready("SomeVM", "", "", 60)
                except SystemExit:
                    pass
        finally:
            vp.copy_ewa_from_vm = real_copy
        check("... before the VM is started to copy anything out of it", calls == [], calls)
    finally:
        vp.WINDOWS, vp.linux_javas, vp.has_nashorn = saved

    env = os.environ.get("AR_JAVA")
    os.environ["AR_JAVA"] = str(other)
    try:
        check("AR_JAVA comes first", vp.linux_javas()[:1] == [str(other)], vp.linux_javas())
    finally:
        if env is None:
            os.environ.pop("AR_JAVA")
        else:
            os.environ["AR_JAVA"] = env
    check("a Java that isn't there has no Nashorn", vp.has_nashorn(str(TMP / "nope" / "java")) is False)

    if os.name != "nt":
        # stand-in Javas: one that runs the probe script, one that refuses
        # the class (Java 15+: "Could not find or load main class")
        good, bad = TMP / "good_java", TMP / "bad_java"
        good.write_text('#!/bin/sh\n[ "$1" = jdk.nashorn.tools.Shell ] && sed -n "s/print(\'\\(.*\\)\')/\\1/p"\n',
                        encoding="utf-8")
        bad.write_text('#!/bin/sh\necho "Error: Could not find or load main class $1" >&2\nexit 1\n',
                       encoding="utf-8")
        good.chmod(0o755)
        bad.chmod(0o755)
        check("a Java whose shell runs the probe has Nashorn", vp.has_nashorn(str(good)) is True)
        check("a Java 15+ (no Nashorn class) has none", vp.has_nashorn(str(bad)) is False)


def packaging() -> None:
    print("packaging/linux")
    unit = (ROOT / "packaging" / "linux" / "autorundown.service").read_text(encoding="utf-8")
    filled = unit.replace("@REPO@", "/home/shop/AutoRundown")
    check("the unit runs serve.py from the checkout's venv",
          "ExecStart=/home/shop/AutoRundown/.venv/bin/python serve.py" in filled
          and "WorkingDirectory=/home/shop/AutoRundown" in filled, filled)
    check("... a user service (the VM belongs to the user), started with the user's session",
          "WantedBy=default.target" in unit and "User=" not in unit)
    script = ROOT / "packaging" / "linux" / "install.sh"
    text = script.read_text(encoding="utf-8")
    check("install.sh fills in @REPO@ and never runs sudo itself",
          "s#@REPO@#$REPO#g" in text and not any(
              line.strip().startswith("sudo") for line in text.splitlines()))
    check("install.sh has Unix line endings", b"\r\n" not in script.read_bytes())
    bash = shutil.which("bash")
    if bash and os.name != "nt":
        r = subprocess.run([bash, "-n", str(script)], capture_output=True, text=True)
        check("install.sh parses", r.returncode == 0, r.stderr)


def main() -> int:
    try:
        mercedes_folder()
        java()
        packaging()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{'all passed' if not FAILS else f'{FAILS} FAILED'}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
