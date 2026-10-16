"""Who is asking: the PC itself (admin), a signed-in mechanic, or nobody.

Rules (the user's decisions, 2026-10-09):
- The PC running AutoRundown is the admin. A request from loopback, with a
  loopback Host header, is admin with no sign-in. Nothing admin -- settings,
  the API key, imports, mechanics' accounts -- answers to any other device.
- Mechanic accounts are optional (`Config.require_signin`). Off: anyone on
  the network can search and ask, as before. On: every other device must sign
  in with a one-time code the admin issues, and gets a long-lived token.
- No passwords. The admin opens a sign-in QR for a mechanic (2026-10-16): a
  "grant" holding a link token (in the QR) and a typed fallback code (8
  characters, under the QR). Either one works once, for 15 minutes, and
  using one uses both; closing the dialog voids the grant. The phone gets a
  32-byte random token kept as a cookie. Only hashes of link tokens, codes
  and cookie tokens are stored, so a copied auth.db signs nobody in.

Accounts live in their own `auth.db` next to config.json, not in the index:
the index gets rebuilt, and accounts must not go with it. Spend stays in the
index's `usage_log`, tagged with the user's id and name.
"""
from __future__ import annotations

import hashlib
import ipaddress
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import config

AUTH_PATH = config.APP_DIR / "auth.db"
COOKIE = "ar_token"
TOKEN_DAYS = 365
CODE_MINUTES = 15
# no 0/O, 1/I/L: read off a screen and typed on a phone
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LEN = 8
# failed sign-ins per client address before it is refused for a while
MAX_FAILS, FAIL_WINDOW = 10, 15 * 60

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users(
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created   TEXT NOT NULL,
    disabled  INTEGER NOT NULL DEFAULT 0,
    monthly_cap_usd REAL            -- NULL = no cap
);
CREATE TABLE IF NOT EXISTS tokens(
    hash      TEXT PRIMARY KEY,     -- sha256 of the cookie value
    user_id   INTEGER NOT NULL REFERENCES users(id),
    created   REAL NOT NULL,
    expires   REAL NOT NULL,
    last_used REAL,
    device    TEXT                  -- User-Agent at sign-in, for the admin list
);
-- before 2026-10-16: typed codes alone. Kept so an old auth.db opens; it is
-- emptied at connect (a code lived 15 minutes, so nothing of value is lost)
CREATE TABLE IF NOT EXISTS codes(
    hash      TEXT PRIMARY KEY,
    user_id   INTEGER NOT NULL REFERENCES users(id),
    expires   REAL NOT NULL
);
-- one sign-in QR as the admin opened it: the link in the QR and the typed
-- code under it. Used, voided or expired, both hashes are cleared and the
-- row stays a little while so the admin's dialog can say what happened
CREATE TABLE IF NOT EXISTS grants(
    id        INTEGER PRIMARY KEY,
    user_id   INTEGER NOT NULL REFERENCES users(id),
    link_hash TEXT UNIQUE,           -- sha256 of the QR link's token
    code_hash TEXT UNIQUE,           -- sha256 of the typed code
    created   REAL NOT NULL,
    expires   REAL NOT NULL,
    used_at   REAL,                  -- set when a phone signed in with it
    voided    INTEGER NOT NULL DEFAULT 0
);
"""

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


@dataclass
class Who:
    admin: bool = False
    user_id: int | None = None
    name: str = ""

    @property
    def label(self) -> str:
        return "this PC" if self.admin else (self.name or "anyone")


def _h(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(path or AUTH_PATH)
    config.make_private_dir(path.parent)
    if os.name != "nt":
        # token and code hashes: readable by this user only (sqlite made it
        # 0644). Created private, not chmod'ed after; its -wal/-shm files
        # take the same mode. An older one made 0644 is tightened.
        try:
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        except FileExistsError:
            if path.stat().st_mode & 0o077:
                os.chmod(path, 0o600)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # codes from before sign-in QRs: at most 15 minutes old, now unusable
    # (phones already signed in keep their tokens)
    if conn.execute("SELECT 1 FROM codes LIMIT 1").fetchone():
        conn.execute("DELETE FROM codes")
        conn.commit()
    return conn


# -- where a request comes from -------------------------------------------

def is_loopback(addr: str) -> bool:
    try:
        return ipaddress.ip_address(addr).is_loopback
    except ValueError:
        return False


def host_is_loopback(host_header: str) -> bool:
    """The Host the browser asked for. A page on evil.example whose DNS later
    points at 127.0.0.1 (DNS rebinding) reaches us from loopback but with its
    own name here, so admin needs both."""
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        host = host[:host.find("]") + 1]
    elif ":" in host:
        host = host.rsplit(":", 1)[0]
    return host in LOOPBACK_HOSTS or is_loopback(host.strip("[]"))


def is_admin_request(client_addr: str, host_header: str) -> bool:
    return is_loopback(client_addr) and host_is_loopback(host_header)


def host_name(host_header: str) -> str:
    """The name or address in a Host header, without port or brackets."""
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        return host[1:host.find("]")] if "]" in host else host[1:]
    if host.count(":") == 1:
        host = host.rsplit(":", 1)[0]
    return host.rstrip(".")


def host_allowed(host_header: str, names: set[str]) -> bool:
    """Is this request addressed to us? DNS rebinding reaches the server under
    the attacker's own name (attacker.example -> 127.0.0.1), so every request,
    not only admin ones, must name this PC: an IP address (no DNS involved,
    so it can't be rebound), localhost, or one of `names` (this PC's host name,
    <host>.local, autorundown.local)."""
    host = host_name(host_header)
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    return host == "localhost" or host in names


# -- accounts ---------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def users(conn: sqlite3.Connection) -> list[dict]:
    out = []
    for r in conn.execute("SELECT * FROM users ORDER BY name"):
        d = dict(r)
        d["devices"] = conn.execute(
            "SELECT COUNT(*) FROM tokens WHERE user_id=? AND expires>?",
            (r["id"], time.time())).fetchone()[0]
        d["last_used"] = conn.execute(
            "SELECT MAX(last_used) FROM tokens WHERE user_id=?", (r["id"],)).fetchone()[0]
        out.append(d)
    return out


def add_user(conn: sqlite3.Connection, name: str, cap: float | None = None) -> int:
    name = " ".join((name or "").split())
    if not name or len(name) > 60:
        raise ValueError("A name of 1-60 characters is needed.")
    try:
        cur = conn.execute(
            "INSERT INTO users(name, created, monthly_cap_usd) VALUES (?,?,?)",
            (name, _now_iso(), cap))
    except sqlite3.IntegrityError:
        raise ValueError(f"There is already a user called {name!r}.")
    conn.commit()
    return cur.lastrowid


def update_user(conn: sqlite3.Connection, user_id: int, *,
                disabled: bool | None = None, cap: float | None = None,
                clear_cap: bool = False) -> None:
    if conn.execute("SELECT 1 FROM users WHERE id=?", (user_id,)).fetchone() is None:
        raise KeyError(user_id)
    if disabled is not None:
        conn.execute("UPDATE users SET disabled=? WHERE id=?", (int(disabled), user_id))
        if disabled:  # sign out every device at once
            conn.execute("DELETE FROM tokens WHERE user_id=?", (user_id,))
            _void(conn, "user_id=?", (user_id,))
    if clear_cap:
        conn.execute("UPDATE users SET monthly_cap_usd=NULL WHERE id=?", (user_id,))
    elif cap is not None:
        conn.execute("UPDATE users SET monthly_cap_usd=? WHERE id=?", (cap, user_id))
    conn.commit()


def sign_out_all(conn: sqlite3.Connection, user_id: int) -> None:
    conn.execute("DELETE FROM tokens WHERE user_id=?", (user_id,))
    conn.commit()


@dataclass
class Grant:
    """A sign-in QR as issued: the secrets exist only in this object, which
    goes to the admin's page once; auth.db keeps their hashes."""
    id: int
    user_id: int
    link_token: str      # goes in the QR: /signin?t=<link_token>
    code: str            # typed fallback, 8 characters
    expires: float


def _void(conn: sqlite3.Connection, where: str, args: tuple) -> None:
    """Make grants unusable (clear both hashes); the row stays for status."""
    conn.execute(f"UPDATE grants SET link_hash=NULL, code_hash=NULL, voided=1 "
                 f"WHERE used_at IS NULL AND voided=0 AND ({where})", args)


def new_grant(conn: sqlite3.Connection, user_id: int) -> Grant:
    """A one-time sign-in QR (and its typed code) for one mechanic. Opening a
    new one voids any earlier unused one for that mechanic; finished grants
    older than a day are forgotten."""
    row = conn.execute("SELECT disabled FROM users WHERE id=?", (user_id,)).fetchone()
    if row is None:
        raise KeyError(user_id)
    if row["disabled"]:
        raise ValueError("That user is disabled; enable them first.")
    now = time.time()
    _void(conn, "user_id=? OR expires<?", (user_id, now))
    conn.execute("DELETE FROM grants WHERE expires<?", (now - 86400,))
    link = secrets.token_urlsafe(32)
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
    expires = now + CODE_MINUTES * 60
    cur = conn.execute(
        "INSERT INTO grants(user_id, link_hash, code_hash, created, expires) VALUES (?,?,?,?,?)",
        (user_id, _h(link), _h(code), now, expires))
    conn.commit()
    return Grant(cur.lastrowid, user_id, link, code, expires)


def void_grant(conn: sqlite3.Connection, grant_id: int) -> None:
    """The admin closed the dialog: that QR and its code stop working."""
    _void(conn, "id=?", (grant_id,))
    conn.commit()


def grant_status(conn: sqlite3.Connection, grant_id: int) -> str:
    """pending | used | voided | expired | unknown -- for the admin's dialog."""
    row = conn.execute("SELECT * FROM grants WHERE id=?", (grant_id,)).fetchone()
    if row is None:
        return "unknown"
    if row["used_at"]:
        return "used"
    if row["voided"]:
        return "voided"
    return "expired" if row["expires"] <= time.time() else "pending"


def normalize_code(code: str) -> str:
    return "".join(c for c in (code or "").upper() if c.isalnum())


def _redeem(conn: sqlite3.Connection, column: str, secret: str, device: str) -> str | None:
    """Use a grant (by its link token or its code): one use, unexpired, the
    mechanic enabled. Returns a new cookie token, or None."""
    now = time.time()
    row = conn.execute(
        f"""SELECT g.id, g.user_id FROM grants g JOIN users u ON u.id=g.user_id
            WHERE g.{column}=? AND g.expires>? AND g.used_at IS NULL AND g.voided=0
              AND u.disabled=0""",
        (_h(secret), now)).fetchone()
    if row is None:
        return None
    # single use: both the link and the code die here. The WHERE makes two
    # phones racing on one grant safe -- only one UPDATE changes a row
    cur = conn.execute(
        "UPDATE grants SET used_at=?, link_hash=NULL, code_hash=NULL "
        "WHERE id=? AND used_at IS NULL AND voided=0", (now, row["id"]))
    if cur.rowcount != 1:
        conn.commit()
        return None
    token = secrets.token_urlsafe(32)
    conn.execute("INSERT INTO tokens VALUES (?,?,?,?,?,?)",
                 (_h(token), row["user_id"], now, now + TOKEN_DAYS * 86400, now,
                  (device or "")[:200]))
    conn.commit()
    return token


def redeem_code(conn: sqlite3.Connection, code: str, device: str = "") -> str | None:
    """Exchange a typed code for a token. None if the code is wrong or stale."""
    code = normalize_code(code)
    if len(code) != CODE_LEN:
        return None
    return _redeem(conn, "code_hash", code, device)


def redeem_link(conn: sqlite3.Connection, link_token: str, device: str = "") -> str | None:
    """Exchange a QR link's token for a cookie token. None if wrong or stale."""
    link_token = (link_token or "").strip()
    if not 20 <= len(link_token) <= 100:
        return None
    return _redeem(conn, "link_hash", link_token, device)


def user_for_token(conn: sqlite3.Connection, token: str | None) -> Who | None:
    if not token:
        return None
    row = conn.execute(
        """SELECT u.id, u.name, t.last_used FROM tokens t JOIN users u ON u.id=t.user_id
           WHERE t.hash=? AND t.expires>? AND u.disabled=0""",
        (_h(token), time.time())).fetchone()
    if row is None:
        return None
    if (row["last_used"] or 0) < time.time() - 3600:  # an hour's resolution is plenty
        conn.execute("UPDATE tokens SET last_used=? WHERE hash=?", (time.time(), _h(token)))
        conn.commit()
    return Who(admin=False, user_id=row["id"], name=row["name"])


def revoke_token(conn: sqlite3.Connection, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM tokens WHERE hash=?", (_h(token),))
        conn.commit()


def monthly_cap(conn: sqlite3.Connection, user_id: int) -> float | None:
    row = conn.execute("SELECT monthly_cap_usd FROM users WHERE id=?", (user_id,)).fetchone()
    return row["monthly_cap_usd"] if row else None


class Throttle:
    """Counts failed sign-ins per client address. In memory: a restart
    resets it, which is fine -- a code still expires in 15 minutes, and
    31^8 codes cannot be walked at 10 tries per 15 minutes."""

    def __init__(self) -> None:
        self.fails: dict[str, list[float]] = {}

    def blocked(self, addr: str) -> bool:
        now = time.time()
        recent = [t for t in self.fails.get(addr, []) if t > now - FAIL_WINDOW]
        self.fails[addr] = recent
        return len(recent) >= MAX_FAILS

    def fail(self, addr: str) -> None:
        self.fails.setdefault(addr, []).append(time.time())

    def ok(self, addr: str) -> None:
        self.fails.pop(addr, None)
