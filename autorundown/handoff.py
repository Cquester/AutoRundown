"""Cars waiting at the desk: a phone sends the car it picked, the PC opens it.

The user's story (2026-10-16): "I scan a VIN on my phone in the shop, then
walk back to the desktop and that vehicle is waiting there to search."

What is stored is the *structured* pick -- vehicle doc_id, variant index,
engine families, model year, VIN -- checked against the index here, never a
free-text label from the phone. The label shown on the desk is built here
from the index, and the desk checks the pick again against its own
/api/vehicles before applying it, through the same code as a normal pick.

Who sees what (`visible_to`):
- the PC (admin) sees every waiting car, labelled with the sender's name;
- a signed-in mechanic sees only the cars they sent (from any of their
  devices -- a phone in the bay, a tablet at the bench);
- a device that is not signed in (only possible with sign-in off, where
  anyone on the network can already use the whole site) sees only cars sent
  by devices that were not signed in either: a shop-wide list.

Cars live in `auth.db` (table `handoffs`), not in the index: a rebuild must
not touch them, and they are small. They expire after `TTL_HOURS`; at most
`KEEP_PER_SENDER` per sender and `KEEP_TOTAL` in all are kept.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import datetime

from . import auth

TTL_HOURS = 24
KEEP_PER_SENDER = 10        # a sender's older cars are dropped past this
KEEP_TOTAL = 200            # the whole table, whoever sent
SHOW = 5                    # the desk lists the latest few
# sends per sender (user, or client address when not signed in)
MAX_SENDS, SEND_WINDOW = 30, 10 * 60

# VIN characters: digits and letters except I, O, Q. A European FIN is 17
# too; 10 is the shortest the car box decodes (WMI + series + check digit...)
VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{10,17}$")
ENGINE_RE = re.compile(r"^\d{3}(,\d{3}){0,5}$")

SCHEMA = """
CREATE TABLE IF NOT EXISTS handoffs(
    id        INTEGER PRIMARY KEY,
    user_id   INTEGER,              -- NULL: sent by a device not signed in
    user_name TEXT,                 -- snapshot of the sender's name
    created   REAL NOT NULL,
    expires   REAL NOT NULL,
    doc_id    TEXT NOT NULL,
    variant   INTEGER,              -- index into vehicles.variants (stored order)
    engine    TEXT,                 -- engine families, "272" or "272,276"
    year      INTEGER,
    vin       TEXT,
    label     TEXT NOT NULL,        -- built from the index, not from the phone
    dismissed INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS handoffs_user ON handoffs(user_id, created);
"""


class BadPick(ValueError):
    """The pick does not name a car this index has."""


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def _variants(vehicle: dict) -> list[dict]:
    try:
        v = json.loads(vehicle.get("variants") or "[]")
    except ValueError:
        return []
    return v if isinstance(v, list) else []


def check_pick(vehicles: list[dict], doc_id: str, variant: int | None,
               engine: str | None, year: int | None, vin: str | None) -> dict:
    """The pick, normalised, with a label built from the index. Raises
    BadPick for anything the car box could not have produced."""
    vehicle = next((v for v in vehicles if v["doc_id"] == doc_id), None)
    # a parts catalogue joined into a car is not listed, so not pickable
    if vehicle is None or vehicle.get("joined_into"):
        raise BadPick("That car is not in this shop's manuals.")
    variants = _variants(vehicle)
    x = None
    if variant is not None:
        if not 0 <= variant < len(variants):
            raise BadPick("That model is not in this car's list.")
        x = variants[variant]
    if engine is not None:
        engine = engine.strip()
        if not ENGINE_RE.match(engine):
            raise BadPick("The engine must be engine family numbers, e.g. 272.")
        # the families this car (or this model of it) was built with
        pool = [x] if x else variants
        # (the car box takes a year's engine from engine_by_bm, so both count)
        known = {str(f) for v in pool for f in (v.get("engine_families") or {})}
        known |= {str(e)[:3] for v in pool for es in (v.get("engine_by_bm") or {}).values()
                  for e in (es or [])}
        if not set(engine.split(",")) <= known:
            raise BadPick("That engine does not fit the car.")
    max_year = datetime.now().year + 2
    if year is not None and not 1900 <= year <= max_year:
        raise BadPick("That model year is not plausible.")
    if vin is not None:
        vin = vin.strip().upper()
        if not vin:
            vin = None
        elif not VIN_RE.match(vin):
            raise BadPick("A VIN is 10-17 letters and digits (no I, O or Q).")
    make = vehicle.get("make") or ""
    if x:
        name = f"{year or ''} {make} {x.get('name') or ''}"
    else:
        name = f"{year or vehicle.get('year_range') or ''} {make} {vehicle.get('model') or ''}"
    label = " ".join(name.split())[:120] or doc_id
    return {"doc_id": doc_id, "variant": variant, "engine": engine,
            "year": year, "vin": vin, "label": label}


def _purge(conn: sqlite3.Connection, now: float) -> None:
    conn.execute("DELETE FROM handoffs WHERE expires<? OR dismissed=1", (now,))


def add(conn: sqlite3.Connection, pick: dict, who: auth.Who) -> int:
    """Store a checked pick for `who` (a mechanic, or a device not signed in)."""
    ensure(conn)
    now = time.time()
    _purge(conn, now)
    cur = conn.execute(
        """INSERT INTO handoffs(user_id, user_name, created, expires, doc_id, variant,
                                engine, year, vin, label) VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (who.user_id, who.name or None, now, now + TTL_HOURS * 3600, pick["doc_id"],
         pick["variant"], pick["engine"], pick["year"], pick["vin"], pick["label"]))
    new_id = cur.lastrowid
    # caps: a sender's oldest go first, then the table's oldest
    conn.execute(
        """DELETE FROM handoffs WHERE user_id IS ? AND id NOT IN (
               SELECT id FROM handoffs WHERE user_id IS ? ORDER BY created DESC, id DESC LIMIT ?)""",
        (who.user_id, who.user_id, KEEP_PER_SENDER))
    conn.execute(
        """DELETE FROM handoffs WHERE id NOT IN (
               SELECT id FROM handoffs ORDER BY created DESC, id DESC LIMIT ?)""", (KEEP_TOTAL,))
    conn.commit()
    return new_id


def _scope(who: auth.Who) -> tuple[str, tuple]:
    """The WHERE that limits rows to what `who` may see (see module doc)."""
    if who.admin:
        return "1=1", ()
    if who.user_id is not None:
        return "user_id=?", (who.user_id,)
    return "user_id IS NULL", ()


def visible_to(conn: sqlite3.Connection, who: auth.Who, limit: int = SHOW) -> list[dict]:
    ensure(conn)
    where, args = _scope(who)
    rows = conn.execute(
        f"""SELECT id, user_id, user_name, created, expires, doc_id, variant, engine, year,
                   vin, label FROM handoffs
            WHERE dismissed=0 AND expires>? AND ({where})
            ORDER BY created DESC, id DESC LIMIT ?""",
        (time.time(), *args, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        # the sender as the desk shows it; the id is not needed by anyone
        d["from"] = d.pop("user_name") or "a phone (not signed in)"
        d.pop("user_id")
        out.append(d)
    return out


def dismiss(conn: sqlite3.Connection, handoff_id: int, who: auth.Who) -> bool:
    """Take a car off the list -- one `who` can see, else nothing (False)."""
    ensure(conn)
    where, args = _scope(who)
    cur = conn.execute(f"UPDATE handoffs SET dismissed=1 WHERE id=? AND dismissed=0 AND ({where})",
                       (handoff_id, *args))
    conn.commit()
    return cur.rowcount == 1


class SendLimit:
    """Sends per sender in a window, in memory (like auth.Throttle)."""

    def __init__(self, limit: int = MAX_SENDS, window: float = SEND_WINDOW) -> None:
        self.limit, self.window = limit, window
        self.sent: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        recent = [t for t in self.sent.get(key, []) if t > now - self.window]
        if len(recent) >= self.limit:
            self.sent[key] = recent
            return False
        recent.append(now)
        self.sent[key] = recent
        return True
