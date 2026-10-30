"""HTTPS for phones, switched on and off from Shop setup -- FREE, ~5 s.

    python tests/test_https_phones.py

The microphone and tap-to-copy need a secure page, and a phone on plain
http is not one (the user, 2026-10-30). Shop setup's "HTTPS for phones"
starts a second listener on the app's port + 2 with a certificate from this
PC's own CA (tls.py), while the server runs; plain http stays for this PC.
This runs the real server on free ports with a throwaway AUTORUNDOWN_HOME
and checks:

  - off by default: nothing on port + 2, phone addresses are http;
  - turned on: port + 2 answers HTTPS with a chain a phone can verify from
    /ca.crt, the setting is saved, phone addresses and the settings say
    https and that port; plain http still answers;
  - only this PC may turn it on (a phone gets 403);
  - turned off: port + 2 stops answering, addresses are http again;
  - a server started with the setting on starts the listener itself;
  - port + 2 taken by another program: refused with the reason, not saved.
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo on sys.path)
import json
import os
import socket
import ssl
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_https_"))
os.environ["AUTORUNDOWN_HOME"] = str(TMP)  # before importing autorundown

from autorundown import config, index, server, tls  # noqa: E402

fails = 0


def check(cond: bool, what: str, detail: str = "") -> None:
    global fails
    print(("  ok   " if cond else "  FAIL ") + what + (f" -- {detail}" if detail and not cond else ""))
    fails += not cond


def free_port() -> int:
    """A port with port + 2 free too, below Windows' ephemeral range (49152+):
    there a probe's own source port can be the one probed, and the TCP
    connect "succeeds" to itself -- the real port, 8758, is far below it."""
    import random
    for _ in range(200):
        p = random.randint(20000, 40000)
        ok = True
        for q in (p, p + 2):
            with socket.socket() as s:
                try:
                    s.bind(("127.0.0.1", q))
                except OSError:
                    ok = False
        if ok:
            return p
    raise SystemExit("no free ports")


def start(port: int):
    srv = server.make_server(str(TMP / "index.db"), host="127.0.0.1", port=port)
    threading.Thread(target=server.serve, args=(srv,), daemon=True).start()
    for _ in range(200):
        if srv.started:
            return srv
        time.sleep(0.05)
    raise SystemExit("server did not start")


def get(url: str, ctx=None) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=5, context=ctx) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def settings(port: int) -> dict:
    return json.loads(get(f"http://127.0.0.1:{port}/api/settings")[1])


def post_settings(port: int, body: dict, peer_host: str = "127.0.0.1") -> tuple[int, dict]:
    req = urllib.request.Request(f"http://{peer_host}:{port}/api/settings", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def https_up(port: int) -> bool:
    ctx = ssl.create_default_context(cafile=str(tls.CA_CRT))
    ctx.check_hostname = False            # the cert names this PC's addresses; 127.0.0.1 is among them
    try:
        return get(f"https://127.0.0.1:{port}/api/me", ctx)[0] == 200
    except (OSError, ssl.SSLError):
        return False


index.connect(TMP / "index.db").close()
config.load().save()                      # defaults: require_signin on, https_phones off

print("-- off by default --")
port = free_port()
srv = start(port)
s = settings(port)
check(config.Config().https_phones is False and not s["https_phones"] and not s["https_port"],
      "HTTPS for phones is off by default")
check(not server._port_taken(port + 2), "nothing listens on port + 2")
check(all(u.startswith("http://") for u in s["phone_urls"]), "phone addresses are http", str(s["phone_urls"]))

print("-- turned on --")
code, _ = post_settings(port, {"https_phones": True})
s = settings(port)
check(code == 200 and s["https_phones"] and s["https_port"] == port + 2, "turned on: saved, on port + 2", str(s))
check(config.load().https_phones is True, "  ... and in config.json")
check(https_up(port + 2), "port + 2 answers HTTPS, verified from the PC's CA (/ca.crt)")
code, body = get(f"http://127.0.0.1:{port}/ca.crt")
check(code == 200 and body == tls.CA_CRT.read_bytes(), "/ca.crt hands out that CA")
check(get(f"http://127.0.0.1:{port}/api/me")[0] == 200, "plain http still answers (this PC)")
check(s["phone_urls"] and all(u.startswith("https://") and u.endswith(f":{port + 2}") for u in s["phone_urls"]),
      "phone addresses are https on port + 2", str(s["phone_urls"]))
check(not s["https_error"], "no error", s["https_error"])

print("-- only this PC --")
lan = server.lan_ip()
if lan.startswith("127."):
    print("  skip  no LAN address on this machine")
else:
    # the server listens on 127.0.0.1 only here, so build the request in-process
    from fastapi.testclient import TestClient
    phone = TestClient(srv.config.app, base_url=f"http://{lan}:{port}", client=("192.168.1.50", 50000))
    r = phone.post("/api/settings", json={"https_phones": False})
    check(r.status_code in (401, 403), "a phone can't turn it off", str(r.status_code))
    check(server._port_taken(port + 2), "  ... it still runs")

print("-- turned off --")
code, _ = post_settings(port, {"https_phones": False})
time.sleep(1.0)
s = settings(port)
check(code == 200 and not s["https_phones"] and not s["https_port"], "turned off: saved", str(s))
check(not https_up(port + 2), "port + 2 stops answering")
check(all(u.startswith("http://") for u in s["phone_urls"]), "phone addresses are http again")
srv.config.app.state.shutdown()
time.sleep(0.5)

print("-- on at start --")
cfg = config.load()
cfg.https_phones = True
cfg.save()
port2 = free_port()
srv2 = start(port2)
time.sleep(0.5)
check(https_up(port2 + 2) and settings(port2)["https_port"] == port2 + 2,
      "a server started with it on starts the HTTPS listener itself")
srv2.config.app.state.shutdown()
time.sleep(0.5)
check(not https_up(port2 + 2), "  ... and stops it when it stops")

print("-- port + 2 taken --")
cfg = config.load()
cfg.https_phones = False
cfg.save()
port3 = free_port()
blocker = socket.socket()
blocker.bind(("127.0.0.1", port3 + 2))
blocker.listen(1)
srv3 = start(port3)
code, body = post_settings(port3, {"https_phones": True})
check(code == 400 and str(port3 + 2) in body.get("detail", ""), "refused, naming the port", str(body))
check(config.load().https_phones is False and not settings(port3)["https_phones"], "  ... and not saved as on")
check("already used" in settings(port3)["https_error"], "  ... Shop setup shows why", settings(port3)["https_error"])
blocker.close()
srv3.config.app.state.shutdown()

print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
