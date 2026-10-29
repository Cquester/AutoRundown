"""Start AutoRundown in the background when the user signs in (Windows).

A shortcut in the user's Startup folder, not a Windows service: a service
needs admin rights to install and runs as SYSTEM, which cannot see the
user's VirtualBox VMs or their ~/.autorundown. The shortcut runs as the
user, with no window (pythonw), and the tray icon is how it is stopped.
The MSI removes the shortcut on uninstall (AutoRundown.wxs).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ICON = Path(__file__).resolve().parent / "web" / "icons" / "favicon.ico"   # tools/make_icons.py
NAME = "AutoRundown.lnk"


def supported() -> bool:
    return os.name == "nt"


def shortcut_path() -> Path:
    return (Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
            / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / NAME)


def is_enabled() -> bool:
    return supported() and shortcut_path().is_file()


def pythonw() -> str:
    w = Path(sys.executable).with_name("pythonw.exe")
    return str(w if w.is_file() else Path(sys.executable))


def set_enabled(on: bool) -> None:
    if not supported():
        raise RuntimeError("Starting at sign-in is only available on Windows.")
    lnk = shortcut_path()
    if not on:
        lnk.unlink(missing_ok=True)
        return
    lnk.parent.mkdir(parents=True, exist_ok=True)
    # WScript.Shell is the stock way to write a .lnk; no pywin32 needed.
    # Values go in through the environment, so no path needs quoting.
    ps = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:AR_LNK);"
          "$s.TargetPath = $env:AR_TARGET; $s.Arguments = $env:AR_ARGS;"
          "$s.WorkingDirectory = $env:AR_CWD; $s.Description = 'AutoRundown (background)';"
          "if ($env:AR_ICON) { $s.IconLocation = $env:AR_ICON }; $s.Save()")
    env = {**os.environ, "AR_LNK": str(lnk), "AR_TARGET": pythonw(),
           "AR_ARGS": "-m autorundown.launcher --background --no-browser",
           "AR_CWD": str(ROOT),
           "AR_ICON": str(ICON) if ICON.is_file() else ""}
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                       env=env, capture_output=True, text=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if r.returncode != 0 or not lnk.is_file():
        raise RuntimeError(f"could not create {lnk}: {(r.stderr or r.stdout).strip()}")
