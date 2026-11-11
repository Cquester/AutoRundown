"""This PC's Tailscale name is accepted, nothing else under .ts.net -- FREE, ~2 s.

    python tests/test_tailnet.py

The server answers only to names it knows (DNS rebinding). On a tailnet a
device opens it as http://<pc>.<tailnet>.ts.net:8756, a name taken from
`tailscale status --json` (tailnet.py), never a pattern. Tailscale is faked
here (no tailscale binary is run); throwaway AUTORUNDOWN_HOME.
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo on sys.path)
import os
import sys
import tempfile
import threading
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_tailnet_"))
os.environ["AUTORUNDOWN_HOME"] = str(TMP)  # before importing autorundown

from fastapi.testclient import TestClient  # noqa: E402

from autorundown import config, index, server, tailnet  # noqa: E402

fails = 0


def check(cond: bool, what: str) -> None:
    global fails
    print(("  ok   " if cond else "  FAIL ") + what)
    fails += not cond


NAME = "shop-pc.tail1234.ts.net"
STATUS = {"BackendState": "Running", "Self": {"DNSName": "Shop-PC.tail1234.ts.net."}}

print("reading tailscale status")
check(tailnet.parse(STATUS) == {NAME, "shop-pc"}, "the full MagicDNS name and its short name, lower-case")
check(tailnet.parse({"Self": {"DNSName": ""}}) == set(), "logged out: no names")
check(tailnet.parse({"Self": {"DNSName": "shop-pc.example.com."}}) == set(), "a name not under .ts.net: none")
check(tailnet.parse({}) == set(), "no Self: none")

print("asking again")
calls = []


def asker(result):
    def f():
        calls.append(1)
        return set(result)
    return f


tailnet.reset()
check(tailnet.names(now=100.0, asker=asker(set())) == set(), "Tailscale not running yet: no names")
check(tailnet.names(NAME, now=130.0, asker=asker({NAME})) == set() and len(calls) == 1,
      "an unknown .ts.net name within a minute doesn't ask again")
check(tailnet.names("evil.example", now=200.0, asker=asker({NAME})) == set() and len(calls) == 1,
      "a name not under .ts.net never asks")
check(tailnet.names(NAME, now=200.0, asker=asker({NAME})) == {NAME} and len(calls) == 2,
      "after a minute it asks, and finds the name (Tailscale started after the app)")
check(tailnet.names(NAME, now=400.0, asker=asker(set())) == {NAME} and len(calls) == 2,
      "a known name never asks again")

print("the server")
idx = TMP / "index.db"
conn = index.connect(idx)
conn.execute("INSERT INTO vehicles(doc_id, make, model) VALUES ('v1','Ford','F-150')")
conn.commit()
conn.close()
cfg = config.load()
cfg.require_signin = False
cfg.save()
APP = server.create_app(str(idx), TMP / "auth.db")
for t in threading.enumerate():          # the startup ask, so it can't overwrite ours
    if t.name == "tailnet":
        t.join(10)
tailnet.reset()
tailnet.names(asker=asker({NAME, "shop-pc"}))


def client(addr: str, host: str) -> TestClient:
    return TestClient(APP, base_url=f"http://{host}", client=(addr, 50000))


laptop = client("100.64.0.7", f"{NAME}:8756")
r = laptop.get("/api/me")
check(r.status_code == 200, f"a tailnet device opens the PC by its Tailscale name: {r.status_code}")
check(r.status_code == 200 and r.json()["admin"] is False, "  ... and is not admin")
check(client("100.64.0.7", "shop-pc:8756").get("/api/me").status_code == 200,
      "the short MagicDNS name works too")
check(client("100.64.0.7", "100.100.1.2:8756").get("/api/me").status_code == 200,
      "the Tailscale address works (any IP did already)")
check(client("100.64.0.7", "other-pc.tail1234.ts.net:8756").get("/api/me").status_code == 421,
      "another machine's name on the same tailnet is refused")
check(client("100.64.0.7", "shop-pc.evil5678.ts.net:8756").get("/api/me").status_code == 421,
      "the same machine name on another tailnet is refused (no pattern)")
loop = client("127.0.0.1", f"{NAME}:8756")
check(loop.get("/api/settings").status_code == 403,
      "loopback under the Tailscale name is not admin (the Host must be loopback)")
check(client("127.0.0.1", "localhost:8756").get("/api/settings").status_code == 200,
      "this PC is still admin")
urls = client("127.0.0.1", "localhost:8756").get("/api/settings").json()
check(f"http://{NAME}:8756" in str(urls), "Shop setup lists the Tailscale address")

tailnet.reset()
print("FAILED" if fails else "all passed")
sys.exit(1 if fails else 0)
