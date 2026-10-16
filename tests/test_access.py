"""Who may do what on the shop network -- FREE, no API calls, ~2 s.

    python tests/test_access.py

Drives the real FastAPI app in-process, from three kinds of client:
this PC (127.0.0.1), a phone on the LAN (192.168.1.50), and the PC reached
through a foreign Host name (DNS rebinding). Uses a throwaway
AUTORUNDOWN_HOME, so the real config, accounts and index are not touched.
Also issues a local CA + server certificate and verifies a TLS chain with
Python's own ssl module, as a phone would.
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo on sys.path)
import os
import sys
import tempfile
import time
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
ARCH = TMP / "archive"
(ARCH / "images").mkdir(parents=True)
(ARCH / "images" / "x.png").write_bytes(b"\x89PNG\r\n\x1a\n")
conn.execute("INSERT INTO vehicles(doc_id, make, model, file_path) VALUES ('v2','Ford','F-250',?)",
             (str(ARCH),))
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

print("no API map, and odd image names")
for path in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"):
    check(phone.get(path).status_code == 404 and pc.get(path).status_code == 404,
          f"{path} is not served (it mapped every route for the LAN; /docs ran CDN script)")
check(phone.get("/api/image/v2/x.png").status_code == 200, "a real picture is served")
for name in ("x.png%00.db", "x%00.png", "%00"):
    r = phone.get(f"/api/image/v2/{name}")
    check(r.status_code == 404 and r.headers.get("x-content-type-options") == "nosniff",
          f"image name {name!r}: 404 with the security headers (a NUL was an unhandled 500)")
check(phone.get("/api/image/v2/..%2f..%2findex.db").status_code == 404, "  ... and no way out of images/")

if os.name != "nt":
    print("private files (Linux/macOS)")
    import stat  # noqa: E402
    home = TMP / "newhome"
    config.make_private_dir(home)
    check(stat.S_IMODE(home.stat().st_mode) == 0o700, "a new app folder is 0700")
    c = auth.connect(home / "auth.db")
    c.close()
    check(stat.S_IMODE((home / "auth.db").stat().st_mode) == 0o600, "a new auth.db is 0600 (was 0644)")
    loose = TMP / "loose.db"
    loose.touch()
    os.chmod(loose, 0o644)
    auth.connect(loose).close()
    check(stat.S_IMODE(loose.stat().st_mode) == 0o600, "an older 0644 auth.db is tightened")


def mint(uid: int) -> dict:
    """This PC opens a sign-in QR for a mechanic."""
    r = post(pc, f"/api/admin/users/{uid}/qr")
    assert r.status_code == 200, r.text
    return r.json()


def link_path(grant: dict) -> str:
    """The QR's URL without the PC's address: the phone client has its own."""
    return "/signin?" + grant["url"].split("?", 1)[1]


def scan(c: TestClient, grant: dict, **headers):
    """A phone's camera opens the QR's link."""
    return c.get(link_path(grant), headers=headers, follow_redirects=False)


print("accounts")
r = post(pc, "/api/admin/users", {"name": "Sam", "monthly_cap_usd": 0.05})
check(r.status_code == 200 and isinstance(r.json()["id"], int) and "code" not in r.json(),
      "this PC adds a mechanic (the sign-in QR is a separate step)")
sam_id = r.json()["id"]
check(post(pc, "/api/admin/users", {"name": "sam"}).status_code == 400, "names are unique, any case")
for cap in (-5, "Infinity", "NaN", 1e9):
    check(post(pc, "/api/admin/users", {"name": f"Cap {cap}", "monthly_cap_usd": cap}).status_code == 422,
          f"a monthly limit of {cap} is refused")
check(post(pc, f"/api/admin/users/{sam_id}", {"monthly_cap_usd": -1}).status_code == 422,
      "  ... when changing a limit too")

print("sign-in required")
check(post(pc, "/api/settings", {"require_signin": True}).status_code == 200, "this PC turns it on")
check(phone.get("/api/vehicles").status_code == 401, "a phone with no token is refused")
for path in ("/api/search?doc_id=v1&q=oil", "/api/page/v1/1", "/api/models", "/api/stats",
             "/api/status", "/api/image/v1/x.png", "/api/connectors/v1", "/api/wis/oil/v1"):
    check(phone.get(path).status_code == 401, f"  ... {path.split('?')[0]} too")
check(post(phone, "/api/ask", {"doc_id": "v1", "question": "x"}).status_code == 401, "  ... and ask")
check(phone.get("/").status_code == 200, "  ... but gets the page (to show the sign-in box)")
check(pc.get("/api/vehicles").status_code == 200, "this PC never signs in")

print("sign-in QR")
check(post(phone, f"/api/admin/users/{sam_id}/qr").status_code == 403,
      "a phone cannot make a sign-in QR")
g = mint(sam_id)
check(g["url"].startswith("http") and "/signin?t=" in g["url"] and g["svg"].startswith("<svg"),
      "this PC gets a QR (SVG) holding a /signin link")
check(len(g["code"]) == 8 and g["minutes"] == 15 and 890 <= g["expires_in"] <= 900,
      "  ... with an 8-character typed code, valid 15 minutes")
token = g["url"].split("t=", 1)[1]
a = sqlite3.connect(TMP / "auth.db")
dump = "\n".join(str(r) for t in ("grants", "tokens", "codes") for r in a.execute(f"SELECT * FROM {t}"))
check(token not in dump and g["code"] not in dump, "auth.db holds neither the link's token nor the code")
check(pc.get(f"/api/admin/users/{sam_id}/qr/{g['grant_id']}").json()["state"] == "pending",
      "  ... the dialog sees it pending")
check(phone.get(f"/api/admin/users/{sam_id}/qr/{g['grant_id']}").status_code == 403,
      "a phone cannot read a QR's state")
r = scan(phone, g, **{"Sec-Fetch-Site": "cross-site"})
check(r.status_code == 303 and "refused" in r.headers["location"] and auth.COOKIE not in phone.cookies,
      "a link followed from another site does not sign the phone in")
r = scan(phone, g, **{"Sec-Fetch-Site": "none"})
check(r.status_code == 303 and r.headers["location"] == "/" and auth.COOKIE in r.cookies,
      "scanning it signs the phone in and sends it to / (the token gone from the address)")
check(r.headers.get("cache-control") == "no-store", "  ... and the redirect is not cached")
cookie_attrs = r.headers.get("set-cookie", "").lower()
check("httponly" in cookie_attrs and "samesite=lax" in cookie_attrs, "  ... an HttpOnly, SameSite cookie")
me = phone.get("/api/me").json()
check(me["signed_in"] and me["name"] == "Sam", "the phone is Sam")
check(phone.get("/api/vehicles").status_code == 200, "Sam can list vehicles")
check(phone.get("/api/settings").status_code == 403, "Sam still cannot read settings")
check(post(phone, f"/api/admin/users/{sam_id}/qr").status_code == 403,
      "a signed-in mechanic cannot make sign-in QRs either")
check(pc.get(f"/api/admin/users/{sam_id}/qr/{g['grant_id']}").json()["state"] == "used",
      "the dialog sees it used")
other = client("192.168.1.51", "192.168.1.20:8756")
r = scan(other, g)
check(r.status_code == 303 and "expired" in r.headers["location"] and auth.COOKIE not in other.cookies,
      "the same QR a second time is refused")
check(post(other, "/api/auth/signin", {"code": g["code"]}).status_code == 400,
      "  ... and so is its typed code: one use covers both")

print("typed code (camera can't scan)")
check(post(other, "/api/auth/signin", {"code": "WRONG123"}).status_code == 400, "a wrong code fails")
g = mint(sam_id)
r = post(other, "/api/auth/signin", {"code": g["code"][:4].lower() + " " + g["code"][4:]})
check(r.status_code == 200 and auth.COOKIE in other.cookies, "the code (any case, spaces) signs in")
check(scan(client("192.168.1.53", "192.168.1.20:8756"), g).headers["location"] != "/",
      "  ... and that QR's link is dead now")
check(post(client("192.168.1.53", "192.168.1.20:8756"), "/api/auth/signin",
           {"code": g["code"]}).status_code == 400, "a code works once only")

print("expiry, closing the dialog, a newer QR")
g = mint(sam_id)
a.execute("UPDATE grants SET expires=? WHERE id=?", (time.time() - 1, g["grant_id"]))
a.commit()
p3 = client("192.168.1.54", "192.168.1.20:8756")
check(scan(p3, g).headers["location"] != "/" and auth.COOKIE not in p3.cookies,
      "a QR past its 15 minutes is refused (checked on the server)")
check(post(p3, "/api/auth/signin", {"code": g["code"]}).status_code == 400, "  ... its code too")
check(pc.get(f"/api/admin/users/{sam_id}/qr/{g['grant_id']}").json()["state"] == "expired",
      "  ... the dialog sees it expired")
g = mint(sam_id)
check(post(phone, f"/api/admin/users/{sam_id}/qr/{g['grant_id']}/void").status_code == 403,
      "a phone cannot void a QR")
check(post(pc, f"/api/admin/users/{sam_id}/qr/{g['grant_id']}/void").status_code == 200,
      "closing the dialog voids the QR")
check(scan(p3, g).headers["location"] != "/" and auth.COOKIE not in p3.cookies, "  ... it no longer signs in")
check(post(p3, "/api/auth/signin", {"code": g["code"]}).status_code == 400, "  ... nor does its code")
g1, g2 = mint(sam_id), mint(sam_id)
check(scan(p3, g1).headers["location"] != "/", "opening a new QR voids the older one")
r = post(pc, "/api/admin/users", {"name": "Daxton"})
dax_id = r.json()["id"]
gd = mint(dax_id)
check(pc.get(f"/api/admin/users/{sam_id}/qr/{gd['grant_id']}").status_code == 404,
      "another mechanic's QR can't be read under Sam")
check(post(pc, f"/api/admin/users/{sam_id}/qr/{gd['grant_id']}/void").status_code == 404,
      "  ... nor voided under Sam")
check(pc.get(f"/api/admin/users/{dax_id}/qr/{gd['grant_id']}").json()["state"] == "pending",
      "  ... and is still good")
check(scan(p3, gd).headers["location"] == "/" and p3.get("/api/me").json()["name"] == "Daxton",
      "Daxton's QR signs that phone in as Daxton, not Sam")
old = p3.cookies.get(auth.COOKIE)
check(scan(p3, g2).headers["location"] == "/" and p3.get("/api/me").json()["name"] == "Sam",
      "the same phone scanning Sam's QR becomes Sam")
check(auth.user_for_token(auth.connect(TMP / "auth.db"), old) is None,
      "  ... and its Daxton token is ended, not left behind")
check(post(pc, "/api/admin/users/9999/qr").status_code == 404, "no QR for a mechanic who doesn't exist")

print("old typed codes: migrated")
legacy = TMP / "legacy-auth.db"
lc = sqlite3.connect(legacy)
lc.executescript("""CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created TEXT NOT NULL, disabled INTEGER NOT NULL DEFAULT 0, monthly_cap_usd REAL);
CREATE TABLE tokens(hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created REAL NOT NULL,
    expires REAL NOT NULL, last_used REAL, device TEXT);
CREATE TABLE codes(hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expires REAL NOT NULL);""")
lc.execute("INSERT INTO users VALUES (1,'Old','2026-01-01',0,NULL)")
lc.execute("INSERT INTO tokens VALUES (?,1,?,?,NULL,'')",
           (auth._h("old-phone-token"), time.time(), time.time() + 86400))
lc.execute("INSERT INTO codes VALUES (?,1,?)", (auth._h("ABCDEFGH"), time.time() + 600))
lc.commit()
lc.close()
lconn = auth.connect(legacy)
check(lconn.execute("SELECT COUNT(*) FROM codes").fetchone()[0] == 0,
      "an auth.db from before sign-in QRs: its unused typed codes are dropped")
check(auth.user_for_token(lconn, "old-phone-token").name == "Old", "  ... its signed-in phones stay signed in")
check(auth.redeem_code(lconn, "ABCDEFGH") is None, "  ... and an old code no longer signs in")
lconn.close()

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
def user_row(uid: int) -> dict:
    return next(u for u in pc.get("/api/admin/users").json()["users"] if u["id"] == uid)


check(user_row(sam_id)["month_spend_usd"] == 0.06 and user_row(sam_id)["month_questions"] == 1,
      "this PC sees Sam's questions and spend this month")
me = phone.get("/api/me").json()
check(me["month_spend_usd"] == 0.06 and me["month_questions"] == 1 and me["monthly_cap_usd"] == 0.05,
      "Sam's phone sees his own questions, spend and limit")
check(index.usage_summary(index.connect(idx))["by_user"][0]["user"] == "Sam", "ask.py --usage reports by user")

print("month rollover")
c = index.connect(idx)
c.execute("""INSERT INTO usage_log(ts, source, model, cost_usd, user_id, user_name, key_ref)
             VALUES (datetime('now','start of month','-1 second'),'web','m',5.0,?,'Sam','user:' || ?)""",
          (sam_id, sam_id))
c.execute("""INSERT INTO usage_log(ts, source, model, cost_usd, user_id, user_name)
             VALUES (datetime('now','start of month'),'web','m',0.01,?,'Sam')""", (sam_id,))
c.commit()
check(round(index.month_spend(c, sam_id), 4) == 0.07 and index.month_questions(c, sam_id) == 2,
      "last month's last second is not this month's; this month's first second is")
check(user_row(sam_id)["month_spend_usd"] == 0.07, "  ... in Shop setup")
check(phone.get("/api/me").json()["month_spend_usd"] == 0.07, "  ... and on Sam's phone")
check(index.month_total(c)[0] == 2, "  ... and in the company total")
c.close()

print("API keys: one company key, or a key per mechanic")
import autorundown.query as q  # noqa: E402

asked: list[tuple] = []


class _Res:
    def as_dict(self):
        return {"answer": "stub"}


def stub_ask(conn, vehicle, question, api_key, **kw):
    """Stands in for query.ask: records which key it was given, logs a spend
    row like the real one, calls no API."""
    asked.append((api_key, kw.get("key_ref"), kw.get("user_id")))

    class U:
        model, calls, input_tokens, output_tokens, cost_usd = "m", 1, 1, 1, 0.25
    index.log_usage(conn, "web", vehicle["doc_id"], question, U, kw.get("user_id"),
                    kw.get("user_name"), kw.get("key_ref"))
    return _Res()


real_ask, q.ask = q.ask, stub_ask
COMPANY, SAMKEY = "sk-ant-test-company-0000AAAA", "sk-ant-test-sam-00001111SAMK"
check(post(pc, "/api/settings", {"api_key": COMPANY}).status_code == 200, "this PC saves the company key")
post(pc, f"/api/admin/users/{sam_id}", {"clear_cap": True})
check(post(pc, "/api/settings", {"key_mode": "everyone"}).status_code == 422, "an unknown key mode is refused")
r = post(phone, "/api/ask", {"doc_id": "v1", "question": "q1"})
check(r.status_code == 200 and asked[-1][:2] == (COMPANY, "company"),
      "company mode: Sam's question is paid by the company key")
s = pc.get("/api/settings").json()
check(s["key_mode"] == "company" and s["month_questions"] == 3 and s["month_spend_usd"] == 0.32,
      "  ... and Shop setup shows this month's total across everyone")
check(s["company_key_last4"] == "AAAA" and COMPANY not in str(s), "  ... with the key's last 4 only")
check(post(pc, "/api/settings", {"key_mode": "per_mechanic"}).status_code == 200,
      "this PC switches to a key per mechanic")
r = post(phone, "/api/ask", {"doc_id": "v1", "question": "q2"})
check(r.status_code == 400 and "No API key is set up for you" in r.json()["detail"] and len(asked) == 1,
      "  ... Sam with no key of his own is told so -- no fallback to the company key")
check(phone.get("/api/stats").json()["has_api_key"] is False, "  ... and his page knows Ask is off for him")
check(post(phone, f"/api/admin/users/{sam_id}/key", {"api_key": "sk-ant-stolen"}).status_code == 403,
      "a phone cannot set a mechanic's key")
check(post(pc, f"/api/admin/users/{sam_id}/key", {"loaded_usd": 5}).status_code == 400,
      "an amount loaded with no key is refused")
check(post(pc, f"/api/admin/users/{sam_id}/key", {"api_key": "sk ant"}).status_code == 400,
      "a key with spaces is refused")
r = post(pc, f"/api/admin/users/{sam_id}/key", {"api_key": SAMKEY, "loaded_usd": 10})
k = r.json()["key"]
check(r.status_code == 200 and k["has_key"] and k["last4"] == "SAMK" and k["loaded_usd"] == 10
      and k["spent_usd"] == 0 and k["remaining_usd"] == 10,
      "this PC sets Sam's key with $10 loaded: last 4 shown, $10 remaining")
check(SAMKEY not in r.text, "  ... the key itself is not sent back")
r = post(phone, "/api/ask", {"doc_id": "v1", "question": "q3"})
check(r.status_code == 200 and asked[-1] == (SAMKEY, f"user:{sam_id}", sam_id),
      "Sam's question now goes on Sam's key")
check(phone.get("/api/stats").json()["has_api_key"] is True, "  ... and his page knows Ask is on")
k = user_row(sam_id)["key"]
check(k["spent_usd"] == 0.25 and k["remaining_usd"] == 9.75,
      "spent on Sam's key since it was loaded: $0.25, $9.75 remaining (last month's $5 on it not counted)")
me = phone.get("/api/me").json()
check(me["own_key"] == {"has_key": True, "loaded_usd": 10, "spent_usd": 0.25, "remaining_usd": 9.75},
      "Sam's phone sees his key's estimate -- and nothing of the key")
r = post(pc, "/api/ask", {"doc_id": "v1", "question": "q4"})
check(asked[-1][:2] == (COMPANY, "company"), "this PC still asks on the company key")
anon = client("192.168.1.70", "192.168.1.20:8756")
post(pc, "/api/settings", {"require_signin": False})
r = post(anon, "/api/ask", {"doc_id": "v1", "question": "q5"})
check(r.status_code == 400 and "Sign in" in r.json()["detail"] and len(asked) == 3,
      "sign-in off, per-mechanic keys: a device that is not signed in is refused, not put on a key")
post(pc, "/api/settings", {"require_signin": True})
time.sleep(1.1)   # usage_log.ts has one-second resolution
r = post(pc, f"/api/admin/users/{sam_id}/key", {"loaded_usd": 20, "reset": True})
k = r.json()["key"]
check(k["spent_usd"] == 0 and k["remaining_usd"] == 20, "\"topped up\": $20 loaded now, spend counted from now")
post(pc, f"/api/admin/users/{dax_id}/key", {"api_key": "sk-ant-test-dax-0000DAXX"})
check(user_row(dax_id)["key"]["remaining_usd"] is None and user_row(dax_id)["key"]["has_key"],
      "a key with no amount loaded: no remaining shown")
pc_json = pc.get("/api/admin/users").text + pc.get("/api/settings").text
phone_json = "".join(phone.get(p).text for p in ("/api/me", "/api/stats", "/api/vehicles", "/api/models"))
check(SAMKEY not in pc_json and "sk-ant-test-dax" not in pc_json,
      "no mechanic's key in any admin JSON (last 4 only)")
check(all(x not in phone_json for x in (SAMKEY, COMPANY, "SAMK", "AAAA", "sk-ant")),
      "no key, nor its last 4, in anything a phone gets")
check(oct(os.stat(config.CONFIG_PATH).st_mode & 0o777) in ("0o600", "0o666"),
      "config.json written private (0600 where the OS has modes)")
check(post(pc, f"/api/admin/users/{dax_id}/key", {"remove": True}).status_code == 200
      and not user_row(dax_id)["key"]["has_key"], "this PC removes a mechanic's key")
post(pc, "/api/settings", {"key_mode": "company"})
q.ask = real_ask

print("revoking")
open_qr = mint(sam_id)
check(post(pc, f"/api/admin/users/{sam_id}", {"disabled": True}).status_code == 200, "this PC disables Sam")
check(pc.get(f"/api/admin/users/{sam_id}/qr/{open_qr['grant_id']}").json()["state"] == "voided",
      "  ... which voids his open sign-in QR")
check(phone.get("/api/vehicles").status_code == 401, "Sam's phone is signed out at once")
check(post(pc, f"/api/admin/users/{sam_id}/qr").status_code == 400, "no sign-in QR for a disabled user")
post(pc, f"/api/admin/users/{sam_id}", {"disabled": False})
g = mint(sam_id)
phone2 = client("192.168.1.52", "192.168.1.20:8756")
check(scan(phone2, g).headers["location"] == "/", "re-enabled, a new QR works")
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
