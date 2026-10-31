"""The PC's own Open dialog, for Shop setup's Browse buttons (the user,
2026-10-31: "can't we just have an actual file explorer open up").

A web page is never told where a file it was given lives, so the browser's
file input cannot choose a VM or a folder for the server. The server runs on
the same PC, though, so it opens the dialog itself: Windows' own, through
PowerShell's System.Windows.Forms (in every Windows; nothing added to the
MSI). Only the PC asks for it (/api/browse is an admin route); a phone never
can. Elsewhere, or if PowerShell fails, `pick` returns None and the page
falls back to its own folder list.

The start folder and the filter reach the script through environment
variables, never pasted into its text.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading

KINDS = {"vbox": ("VirtualBox machine (*.vbox)|*.vbox", "Choose the Mercedes VM's .vbox file"),
         "folder": ("", "Choose the folder to import")}

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
[Windows.Forms.Application]::EnableVisualStyles()
# an owner that is always on top: the dialog opens in front of the browser
$owner = New-Object Windows.Forms.Form -Property @{TopMost = $true; ShowInTaskbar = $false}
$start = $env:AR_DLG_START
if ($env:AR_DLG_FILTER) {
  $d = New-Object Windows.Forms.OpenFileDialog -Property @{
    Filter = $env:AR_DLG_FILTER; Title = $env:AR_DLG_TITLE; Multiselect = $false}
  if ($start -and (Test-Path -LiteralPath $start -PathType Container)) { $d.InitialDirectory = $start }
  if ($d.ShowDialog($owner) -eq 'OK') { $d.FileName }
} else {
  $d = New-Object Windows.Forms.FolderBrowserDialog -Property @{
    Description = $env:AR_DLG_TITLE; ShowNewFolderButton = $false}
  if ($start -and (Test-Path -LiteralPath $start -PathType Container)) { $d.SelectedPath = $start }
  if ($d.ShowDialog($owner) -eq 'OK') { $d.SelectedPath }
}
"""

_ONE = threading.Lock()          # one dialog at a time
NO_WINDOW = 0x08000000           # no console window flashing up with it


class Busy(RuntimeError):
    pass


def available() -> bool:
    return sys.platform == "win32"


def pick(kind: str, start: str = "", timeout: float = 600) -> str | None:
    """The path chosen, "" if the dialog was cancelled, or None when this
    PC has no such dialog (not Windows, or PowerShell failed). Raises Busy
    while another dialog is open."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    if not available():
        return None
    if not _ONE.acquire(blocking=False):
        raise Busy("A file dialog is already open on this PC.")
    try:
        flt, title = KINDS[kind]
        env = {**os.environ, "AR_DLG_START": start or "", "AR_DLG_FILTER": flt, "AR_DLG_TITLE": title}
        try:
            r = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-STA",
                                "-ExecutionPolicy", "Bypass", "-Command", _SCRIPT],
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               env=env, timeout=timeout, creationflags=NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if r.returncode != 0:
            return None
        lines = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
        return lines[-1] if lines else ""
    finally:
        _ONE.release()
