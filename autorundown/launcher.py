"""What the Start-menu shortcut runs: start the server, open the browser.

    python -m autorundown.launcher [--port 8756] [--index <path>] [--no-browser]
    pythonw -m autorundown.launcher --background     # no window; tray icon
    python -m autorundown.launcher --stop            # stop a running one

If AutoRundown is already running on that port (a second click on the
shortcut), it only opens the browser.

Two ways to run. In a console (the default from source), closing the window
stops it. In the background (what the installed shortcut and the sign-in
start use), there is no window: output goes to ~/.autorundown/autorundown.log
and a tray icon offers Open / Start when I sign in / Stop. Without a tray
(not Windows, or no desktop), it serves headless; `--stop` ends it.
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

LOG = config.APP_DIR / "autorundown.log"
LOG_MAX = 2_000_000


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


def stop(port: int) -> int:
    if not _running(port):
        print("AutoRundown is not running.")
        return 0
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown", data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=5):
        pass
    for _ in range(40):
        if not _running(port):
            print("AutoRundown stopped.")
            return 0
        time.sleep(0.25)
    print("AutoRundown did not stop.", file=sys.stderr)
    return 1


def _log_to_file() -> None:
    """pythonw has no console: send everything to the log, kept small."""
    config.APP_DIR.mkdir(parents=True, exist_ok=True)
    if LOG.is_file() and LOG.stat().st_size > LOG_MAX:
        LOG.unlink()
    f = open(LOG, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = f
    print(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} AutoRundown {__version__}")


def _message(text: str) -> None:
    """A visible error when there is no console to print it in."""
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, "AutoRundown", 0x10)


def _tray_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((2, 2, 62, 62), radius=14, fill=(245, 166, 35, 255))
    d.text((14, 18), "AR", fill=(20, 20, 20, 255), font_size=26)
    return img


def run_background(srv, url: str, open_browser: bool) -> int:
    from . import autostart, server

    result: dict = {}

    def serve() -> None:
        try:
            server.serve(srv)
        except BaseException as exc:          # uvicorn exits when the port is taken
            result["error"] = exc
        finally:
            if tray:
                tray.stop()

    tray = None
    try:
        if os.name != "nt":
            raise ImportError("tray icon is Windows-only here")
        import pystray

        def phone_text(_item) -> str:
            urls = srv.config.app.state.phone_urls
            return f"Phone: {urls[0]}" if urls else "Phone: (no network)"

        def toggle_autostart(_icon, _item) -> None:
            try:
                autostart.set_enabled(not autostart.is_enabled())
            except RuntimeError as exc:
                _message(str(exc))

        def stop_all(_icon, _item) -> None:
            srv.should_exit = True

        tray = pystray.Icon("AutoRundown", _tray_image(), f"AutoRundown {__version__}", menu=pystray.Menu(
            pystray.MenuItem("Open AutoRundown", lambda: webbrowser.open(url), default=True),
            pystray.MenuItem(phone_text, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Start when I sign in", toggle_autostart,
                             checked=lambda _item: autostart.is_enabled()),
            pystray.MenuItem("Open log", lambda: os.startfile(LOG)),
            pystray.MenuItem("Stop AutoRundown", stop_all),
        ))
    except Exception as exc:
        print(f"no tray icon ({exc}); stop with: launcher --stop")
        tray = None

    if open_browser:
        threading.Thread(target=_open_when_up, args=(url, srv.config.port), daemon=True).start()
    t = threading.Thread(target=serve, name="server")
    t.start()
    if tray:
        try:
            tray.run()                 # until Stop, or the server ends
        except Exception as exc:
            print(f"tray icon failed ({exc}); serving without it")
        srv.should_exit = True         # tray closed: the server goes with it
    t.join()
    err = result.get("error")
    if err is not None and not (isinstance(err, SystemExit) and not err.code):
        print(f"server stopped: {err!r}")
        _message(f"AutoRundown could not start or stopped with an error.\n\n"
                 f"Details are in:\n{LOG}")
        return 1
    print("stopped")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8756)
    ap.add_argument("--index", default=None, help="index to serve (default: the configured one)")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--background", action="store_true",
                    help="no console: log to a file, tray icon to open or stop")
    ap.add_argument("--stop", action="store_true", help="stop a running AutoRundown")
    args = ap.parse_args(argv)
    url = f"http://localhost:{args.port}"
    # started by pythonw (the shortcut): there is no console to print to
    background = args.background or sys.stdout is None

    if args.stop:
        return stop(args.port)
    if background:
        _log_to_file()
    if _running(args.port):
        print(f"AutoRundown is already running: {url}")
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    from . import server
    srv = server.make_server(args.index or config.load().index_path, port=args.port)
    if background:
        return run_background(srv, url, not args.no_browser)

    if os.name == "nt":
        os.system(f"title AutoRundown {__version__}")
    print(f"AutoRundown {__version__}")
    print(f"settings and index: {config.APP_DIR}")
    print("Close this window to stop AutoRundown.\n")
    if not args.no_browser:
        threading.Thread(target=_open_when_up, args=(url, args.port), daemon=True).start()
    return server.serve(srv)


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
