"""The background launcher always exits after a stop (free, ~20 s).

An upgrade left the old launcher running each time -- server stopped, tray
icon still showing (2026-11-15). `launcher.exit_watch` gives the process a
deadline once a stop is asked; this checks the watch on stuck stand-ins, and
that a real background launcher started on a spare port exits on --stop.

    python tests/test_launcher.py
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import _paths  # noqa: F401  (repo root on sys.path)

from autorundown import launcher

ROOT = Path(__file__).resolve().parent.parent
fails = 0


def check(cond: bool, what: str) -> None:
    global fails
    print(("ok   " if cond else "FAIL ") + what)
    fails += 0 if cond else 1


class FakeServer:
    should_exit = False
    force_exit = False


LOGS = Path(tempfile.mkdtemp())


def watch(srv, thread, grace=1.0, wait=4.0):
    """Run exit_watch in a thread; return (exit code given or None, log).
    The log is a real file opened as the launcher opens its own (faulthandler
    writes to its file descriptor), left open: a watch still waiting writes later."""
    got: list = []
    path = LOGS / f"{len(list(LOGS.iterdir()))}.log"
    out = open(path, "a", encoding="utf-8", buffering=1)
    w = threading.Thread(target=launcher.exit_watch, args=(srv, thread),
                         kwargs=dict(grace=grace, exit_fn=got.append, poll=0.05, log=out),
                         daemon=True)
    w.start()
    w.join(wait)
    return (got[0] if got else None), path.read_text(encoding="utf-8")


def stuck_thread() -> tuple[threading.Thread, threading.Event]:
    release = threading.Event()
    t = threading.Thread(target=release.wait, name="server", daemon=True)
    t.start()
    return t, release


# 1. stop asked, the server never finishes: told to drop open requests, stacks logged, exit
srv = FakeServer()
t, release = stuck_thread()
srv.should_exit = True
t0 = time.monotonic()
code, log = watch(srv, t)
check(code == 0, "a server that never stops: the process is made to exit")
check(srv.force_exit, "... after uvicorn is told to stop waiting for open requests")
check(0.9 <= time.monotonic() - t0 < 3, "... at the deadline, not before")
check("forced exit" in log and "Thread 0x" in log and "exit_watch" in log,
      "... with every thread's stack in the log")
release.set()

# 2. the server ended by itself, the tray never closed: the process still goes
srv = FakeServer()
t = threading.Thread(target=lambda: None)
t.start()
t.join()
code, log = watch(srv, t)
check(code == 0, "server ended but the process lingers: it is made to exit")
check(not srv.force_exit, "... no forcing needed for a server already gone")

# 3. nothing asked: the watch only waits
srv = FakeServer()
t, release = stuck_thread()
code, log = watch(srv, t, wait=1.5)
check(code is None and not srv.force_exit and not log, "no stop asked: the watch does nothing")
# (left stuck: releasing it would end that watch's wait mid-test)


# 4. a real background launcher exits on --stop (the installer's call)
def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


with tempfile.TemporaryDirectory() as home:
    port = free_port()
    env = dict(os.environ, AUTORUNDOWN_HOME=home, PYTHONUTF8="1")
    for k in [k for k in env if k.endswith("_API_KEY")]:
        del env[k]
    base = [sys.executable, "-m", "autorundown.launcher", "--port", str(port)]
    p = subprocess.Popen(base + ["--background", "--no-browser",
                                 "--index", str(Path(home) / "index.db")], cwd=ROOT, env=env)
    for _ in range(120):
        if launcher._running(port) or p.poll() is not None:
            break
        time.sleep(0.5)
    check(launcher._running(port), "background launcher answers on its port")
    stop = subprocess.run(base + ["--stop"], cwd=ROOT, env=env, capture_output=True, text=True)
    check(stop.returncode == 0, f"--stop: {stop.stdout.strip()}")
    try:
        rc = p.wait(launcher.STOP_GRACE + 10)
    except subprocess.TimeoutExpired:
        p.kill()
        rc = None
    check(rc == 0, f"... and the launcher process has exited (code {rc})")
    log = (Path(home) / "autorundown.log").read_text(encoding="utf-8", errors="replace")
    check("stopped" in log and "forced exit" not in log, "... cleanly, not forced")

print(f"\n{'all passed' if not fails else f'{fails} FAILED'}")
sys.exit(1 if fails else 0)
