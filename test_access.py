"""Who may do what on the shop network -- FREE, no API calls, ~2 s.

    python test_access.py

Drives the real FastAPI app in-process, from three kinds of client:
this PC (127.0.0.1), a phone on the LAN (192.168.1.50), and the PC reached
through a foreign Host name (DNS rebinding). Uses a throwaway
AUTORUNDOWN_HOME, so the real config, accounts and index are not touched.
Also issues a local CA + server certificate and verifies a TLS chain with
Python's own ssl module, as a phone would.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ar_access_"))
os.environ["AUTORUNDOWN_HOME"] = str(TMP)  # before importing autorundown

from fastapi.testclient import TestClient  # noqa: E402

from autorundown import auth, config, index, server  # noqa: E402

fails = 0


def check(cond: bool, what: str) -> None:
    global fails
    print(("  ok   " if cond else "  FAIL ") + what)
    fails += not cond


def client(addr: str, host: str = "localhost:8756") -> TestClient:
    return TestClient(APP, base_url=f"http://{host}", client=(addr, 50000))


def post(c: TestClient, path: str, body: dict | None = None, origin: str | None = "same"):
    headers = {}
    if origin == "same":
        headers["Origin"] = str(c.base_url).rstrip("/")
    elif origin:
        headers["Origin"] = origin
    return c.post(path, json=body or {}, headers=headers)


idx = TMP / "index.db"
conn = index.connect(idx)
conn.execute("INSERT INTO vehicles(doc_id, make, model) VALUES ('v1','Ford','F-150')")
conn.commit()
conn.close()
APP = server.create_app(str(idx), TMP / "auth.db")

pc = client("127.0.0.1")
phone = client("192.168.1.50", "192.168.1.20:8756")
rebind = client("127.0.0.1", "evil.example:8756")

print("sign-in off (the default)")
check(pc.get("/api/me").json()["admin"] is True, "this PC is admin with no sign-in")
check(phone.get("/api/me").json()["admin"] is False, "a phone is not admin")
check(phone.get("/api/vehicles").status_code == 200, "a phone can list vehicles")
check(phone.get("/api/settings").status_code == 403, "a phone cannot read settings")
r = post(phone, "/api/settings", {"api_key": "sk-stolen", "model": "x"})
check(r.status_code == 403, "a phone cannot change the API key")
check(config.load().api_key == "", "  ... and the key is unchanged")
check(post(phone, "/api/import", {"path": "/"}).status_code == 403, "a phone cannot import")
check(phone.get("/api/browse").status_code == 403, "a phone cannot browse folders")
check(phone.get("/api/admin/users").status_code == 403, "a phone cannot list accounts")
check(rebind.get("/api/settings").status_code == 421,
      "loopback with a foreign Host (DNS rebinding) is not admin")
check(rebind.get("/api/search", params={"doc_id": "v1", "q": "oil"}).status_code == 421,
      "  ... and can't search either")
check(post(rebind, "/api/ask", {"doc_id": "v1", "question": "x"}).status_code == 421,
      "  ... or ask on this PC's key")
check(client("127.0.0.1", "LOCALHOST.:8756").get("/api/me").status_code == 200,
      "localhost in any case, with a trailing dot, is served")
check(phone.get("/api/me").status_code == 200, "a phone using the PC's IP address is served")
check(client("192.168.1.50", "autorundown.local:8756").get("/api/me").status_code == 200,
      "  ... and one using autorundown.local")

print("settings are checked before they are saved")
for bad, what in (({"index_path": ""}, "an empty index path"),
                  ({"index_path": str(TMP / "typo" / "indx.db")}, "an index path that doesn't exist"),
                  ({"extractor_dir": "\\\\evil\\share"}, "an extractor on a network share"),
                  ({"extractor_dir": "relative/dir"}, "a relative extractor folder"),
                  ({"extractor_dir": str(TMP)}, "a folder without vm_pipeline.py"),
                  ({"model": "gpt-4"}, "an unknown model")):
    check(post(pc, "/api/settings", bad).status_code == 400, f"refused: {what}")
check(not (TMP / "typo").exists(), "  ... and no empty database was created for the typo")
check(pc.get("/api/vehicles").status_code == 200, "  ... and the site still works")
check("index_path" not in phone.get("/api/stats").json(), "a phone does not see this PC's paths")
check("index_path" in pc.get("/api/stats").json(), "this PC does")
check(post(pc, "/api/settings", {"model": "claude-haiku-4-5"},
           origin="http://evil.example").status_code == 403,
      "a cross-site page cannot POST as the admin")
check(post(pc, "/api/settings", {"model": "claude-haiku-4-5"}).status_code == 200,
      "this PC can change settings")
check(APP.state.index_path == str(idx), "  ... without moving the server off its --index")

print("the index is brought up to date before the server answers")
import sqlite3  # noqa: E402
old = TMP / "old-index.db"
c = index.connect(old)
c.execute("DROP INDEX pages_doc_page")       # an index from before that index existed
c.commit()
c.close()
server.create_app(str(old), TMP / "auth.db")
c = sqlite3.connect(old)
check(c.execute("SELECT 1 FROM sqlite_master WHERE name='pages_doc_page'").fetchone() is not None,
      "create_app adds new indexes up front (on first requests they raced and 500'd)")
c.close()
c = index.connect(old, setup=False)
check(c.execute("SELECT COUNT(*) FROM vehicles").fetchone()[0] == 0, "  ... and request connections skip the schema")
c.close()

print("headers, sizes and cross-site requests")
r = pc.get("/")
check(r.headers.get("x-frame-options") == "SAMEORIGIN" and "frame-ancestors" in r.headers.get(
      "content-security-policy", ""), "pages can't be framed by another site")
check(r.headers.get("x-content-type-options") == "nosniff", "  ... and are never content-sniffed")
check(pc.get("/", headers={"If-None-Match": r.headers["etag"]}).status_code == 304,
      "the page is not sent again when the browser has it")
check(pc.get("/api/vehicles", headers={"Accept-Encoding": "gzip"}).status_code == 200, "vehicles answer")
check(pc.get("/api/vms", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403,
      "an <img> on another site can't reach an admin route as this PC")
check(pc.get("/api/browse", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403,
      "  ... nor can another port of localhost")
check(pc.get("/api/import", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200,
      "  ... this page itself still can")
check(pc.get("/api/browse", params={"path": str(TMP / "no-such-folder")}).status_code == 404,
      "a folder that doesn't exist is an error, not the home folder")
check(pc.get("/api/browse", params={"path": "\\\\server\\share"}).status_code == 400,
      "network shares are not browsed")
check(post(phone, "/api/auth/signin", {"code": "x" * 300_000}).status_code == 413,
      "a 300 KB request body is refused before it is read")
check(phone.get("/api/search", params={"doc_id": "v1", "q": "oil", "limit": 100000}).status_code == 422,
      "a search can't ask for 100,000 results")
check(post(phone, "/api/ask", {"doc_id": "v1", "question": "x" * 5000}).status_code == 422,
      "a 5,000-character question is refused (paid input)")
check(all("file_path" not in v for v in phone.get("/api/vehicles").json()),
      "a phone doesn't see this PC's folders in the vehicle list")
check(all("file_path" in v for v in pc.get("/api/vehicles").json()), "  ... this PC does")

print("accounts")
r = post(pc, "/api/admin/users", {"name": "Sam", "monthly_cap_usd": 0.05})
check(r.status_code == 200 and len(r.json()["code"]) == 8, "this PC adds a mechanic and gets a code")
sam_id, code = r.json()["id"], r.json()["code"]
check(post(pc, "/api/admin/users", {"name": "sam"}).status_code == 400, "names are unique, any case")
for cap in (-5, "Infinity", "NaN", 1e9):
    check(post(pc, "/api/admin/users", {"name": f"Cap {cap}", "monthly_cap_usd": cap}).status_code == 422,
          f"a monthly limit of {cap} is refused")
check(post(pc, f"/api/admin/users/{sam_id}", {"monthly_cap_usd": -1}).status_code == 422,
      "  ... when changing a limit too")

print("sign-in required")
check(post(pc, "/api/settings", {"require_signin": True}).status_code == 200, "this PC turns it on")
check(phone.get("/api/vehicles").status_code == 401, "a phone with no token is refused")
check(phone.get("/").status_code == 200, "  ... but gets the page (to show the sign-in box)")
check(pc.get("/api/vehicles").status_code == 200, "this PC never signs in")
check(post(phone, "/api/auth/signin", {"code": "WRONG123"}).status_code == 400, "a wrong code fails")
r = post(phone, "/api/auth/signin", {"code": code[:4].lower() + " " + code[4:]})
check(r.status_code == 200 and auth.COOKIE in phone.cookies, "the right code (any case, spaces) signs in")
check(post(client("192.168.1.51", "192.168.1.20:8756"), "/api/auth/signin",
           {"code": code}).status_code == 400, "a code works once only")
me = phone.get("/api/me").json()
check(me["signed_in"] and me["name"] == "Sam", "the phone is Sam")
check(phone.get("/api/vehicles").status_code == 200, "Sam can list vehicles")
check(phone.get("/api/settings").status_code == 403, "Sam still cannot read settings")

print("spend and the monthly limit")
c = index.connect(idx)
c.execute("""INSERT INTO usage_log(ts, source, model, cost_usd, user_id, user_name)
             VALUES (datetime('now'),'web','m',0.06,?, 'Sam')""", (sam_id,))
c.commit()
c.close()
r = post(phone, "/api/ask", {"doc_id": "v1", "question": "oil capacity?"})
check(r.status_code == 402 and "limit" in r.json()["detail"], "over the limit: asking is refused before any API call")
check(phone.get("/api/search", params={"doc_id": "v1", "q": "oil"}).status_code == 200,
      "  ... keyword search still works")
users = pc.get("/api/admin/users").json()["users"]
check(users[0]["month_spend_usd"] == 0.06, "this PC sees Sam's spend this month")
check(index.usage_summary(index.connect(idx))["by_user"][0]["user"] == "Sam", "ask.py --usage reports by user")

print("revoking")
check(post(pc, f"/api/admin/users/{sam_id}", {"disabled": True}).status_code == 200, "this PC disables Sam")
check(phone.get("/api/vehicles").status_code == 401, "Sam's phone is signed out at once")
check(post(pc, f"/api/admin/users/{sam_id}/code").status_code == 400, "no code for a disabled user")
post(pc, f"/api/admin/users/{sam_id}", {"disabled": False})
code2 = post(pc, f"/api/admin/users/{sam_id}/code").json()["code"]
phone2 = client("192.168.1.52", "192.168.1.20:8756")
check(post(phone2, "/api/auth/signin", {"code": code2}).status_code == 200, "re-enabled, a new code works")
check(post(phone2, "/api/auth/signout").status_code == 200
      and phone2.get("/api/vehicles").status_code == 401, "signing out ends that device's token")

print("throttle")
t = client("192.168.1.60", "192.168.1.20:8756")
codes = [post(t, "/api/auth/signin", {"code": "AAAAAAAA"}).status_code for _ in range(auth.MAX_FAILS + 1)]
check(codes[-1] == 429, f"{auth.MAX_FAILS} wrong codes and that address is refused")

print("HTTPS: local CA and server certificate")
try:
    import ssl
    import socket
    import threading

    from autorundown import tls
    crt, key, names, ips = tls.ensure_server_cert()
    check("localhost" in names and "127.0.0.1" in ips, f"cert names {names + ips}")
    check(tls.ensure_server_cert()[0] == crt and tls._server_ok(names, ips), "re-used when still valid")
    srv_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    srv_ctx.load_cert_chain(crt, key)
    lsock = socket.socket()
    lsock.bind(("127.0.0.1", 0))
    lsock.listen(2)
    port = lsock.getsockname()[1]

    def serve(n):
        for _ in range(n):
            s, _ = lsock.accept()
            try:
                srv_ctx.wrap_socket(s, server_side=True).close()
            except (ssl.SSLError, OSError):
                pass
    threading.Thread(target=serve, args=(2,), daemon=True).start()
    cli_ctx = ssl.create_default_context(cafile=str(tls.CA_CRT))
    # a name and an address: both must pass the CA's Name Constraints
    for name in ("localhost", "127.0.0.1"):
        with socket.create_connection(("127.0.0.1", port)) as raw:
            with cli_ctx.wrap_socket(raw, server_hostname=name) as tls_sock:
                check(tls_sock.version() is not None,
                      f"a client trusting ca.crt verifies {name} ({tls_sock.version()})")
    lsock.close()
    check(pc.get("/ca.crt").status_code == 200 and phone.get("/ca.crt").status_code == 200,
          "ca.crt downloads without signing in")
except SystemExit as exc:  # cryptography missing
    check(False, str(exc))

print()
print(f"{fails} failure(s)")
sys.exit(1 if fails else 0)
