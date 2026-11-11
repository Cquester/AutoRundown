"""This PC's name on a Tailscale network, asked from Tailscale itself.

The server answers only to names it knows (`auth.host_allowed`: DNS
rebinding reaches it under an attacker's own name). A device on the shop's
tailnet opens it as `http://<pc>.<tailnet>.ts.net:8756` (MagicDNS), a name
the PC's own host name doesn't give. The exact name comes from
`tailscale status --json` (`Self.DNSName`), never a pattern such as
"anything ending .ts.net": another tailnet can name a machine the same.

Tailscale may start after the app (both start at sign-in), or not be
installed at all: `names()` asks again at most once a minute, only when a
request names an unknown host under .ts.net, and a missing or stopped
Tailscale is simply no names. Nothing here changes who is admin: a request
over Tailscale comes from a 100.x address, never loopback.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SUFFIX = ".ts.net"
RETRY_S = 60.0

_lock = threading.Lock()
_names: set[str] = set()
_asked = 0.0


def _exe() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    if os.name == "nt":                  # the installer doesn't put it on PATH
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")):
            if base:
                p = os.path.join(base, "Tailscale", "tailscale.exe")
                if os.path.isfile(p):
                    return p
    return None


def parse(status: dict) -> set[str]:
    """The names a tailnet device may use for this PC: the full MagicDNS
    name, and its first label (MagicDNS's short name, which can differ from
    the host name: Tailscale lower-cases and replaces characters)."""
    self_ = status.get("Self") or {}
    dns = str(self_.get("DNSName") or "").strip().lower().rstrip(".")
    if not dns.endswith(SUFFIX) or dns.count(".") < 2:
        return set()
    return {dns, dns.split(".", 1)[0]}


def ask(timeout: float = 3.0) -> set[str]:
    """Run `tailscale status --json`; no Tailscale, stopped or logged out = set()."""
    exe = _exe()
    if not exe:
        return set()
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True,
                             timeout=timeout, creationflags=NO_WINDOW)
        if out.returncode != 0:
            return set()
        status = json.loads(out.stdout.decode("utf-8", "replace") or "{}")
    except (OSError, subprocess.SubprocessError, ValueError):
        return set()
    if status.get("BackendState") not in (None, "Running"):
        return set()
    return parse(status)


def known() -> set[str]:
    """The names found so far, without asking Tailscale (never blocks)."""
    with _lock:
        return set(_names)


def names(host: str = "", now: float | None = None, asker=None) -> set[str]:
    """This PC's tailnet names. Asks Tailscale on the first call, and again
    (at most once a minute) when `host` is an unknown .ts.net name. Blocks
    up to a few seconds while asking: call it off the event loop."""
    global _asked, _names
    asker = asker or ask
    now = time.monotonic() if now is None else now
    with _lock:
        due = _asked == 0.0 or (host.endswith(SUFFIX) and host not in _names
                                and now - _asked >= RETRY_S)
        if not due:
            return set(_names)
        _asked = now
    found = asker()
    with _lock:
        _names = found
        return set(_names)


def reset() -> None:
    """For tests."""
    global _asked, _names
    with _lock:
        _asked, _names = 0.0, set()
