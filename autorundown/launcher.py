"""What the Start-menu shortcut runs: start the server, open the browser.

    python -m autorundown.launcher [--port 8756] [--index <path>] [--no-browser]

If AutoRundown is already running on that port (a second click on the
shortcut), it only opens the browser. The server runs in this console window,
so closing the window stops it -- there is no hidden background process to
hunt for in Task Manager.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.request
import webbrowser

from . import __version__, config


def _running(port: int) -> bool:
    """True when an AutoRundown server already answers on this port."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/settings", timeout=1) as r:
            return "index_path" in json.load(r)
    except (OSError, ValueError):
        return False


def _open_when_up(url: str, port: int) -> None:
    for _ in range(120):            # first start can be slow (antivirus scans)
        if _running(port):
            webbrowser.open(url)
            return
        time.sleep(0.5)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8756)
    ap.add_argument("--index", default=None, help="index to serve (default: the configured one)")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)
    url = f"http://localhost:{args.port}"

    if _running(args.port):
        print(f"AutoRundown is already running: {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    if os.name == "nt":
        os.system(f"title AutoRundown {__version__}")
    print(f"AutoRundown {__version__}")
    print(f"settings and index: {config.APP_DIR}")
    print("Close this window to stop AutoRundown.\n")

    if not args.no_browser:
        threading.Thread(target=_open_when_up, args=(url, args.port), daemon=True).start()

    from . import server
    server_args = ["--port", str(args.port)]
    if args.index:
        server_args += ["--index", args.index]
    return server.main(server_args)


if __name__ == "__main__":
    try:
        rc = main()
    except SystemExit as exc:      # uvicorn exits this way when the port is taken
        rc = exc.code if isinstance(exc.code, int) else 1
    except Exception as exc:
        # started from a shortcut, the window would close before anyone read this
        print(f"\nAutoRundown stopped with an error: {exc!r}", file=sys.stderr)
        rc = 1
    if rc and sys.stdin and sys.stdin.isatty():
        input("Press Enter to close this window.")
    raise SystemExit(rc)
