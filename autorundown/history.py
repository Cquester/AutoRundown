"""Questions asked and searches made, kept so they can be opened again.

The user's request (2026-10-23): a history of the results -- "if we are
having people pay for ai requests then it should be retrievable". An Ask is
stored with its whole answer as the server returned it, so opening it again
shows the same answer without a new AI call; a keyword search is stored as
its words (running it again is free). An AI chat is one entry: the first
answer is its result, the follow-ups are kept in `result.turns` (add_turn),
and the chat's car is the entry's. Each entry keeps the car it was for as
a structured pick (doc_id, variant index *and name*, engine, year), built
here from the index, never from text the browser sent; the page checks it
again before re-selecting the car, as it does for recent cars.

Who sees what (`visible_to`), as for cars sent to the desk (handoff.py):
the PC (admin) sees everyone's, with their names; a signed-in mechanic sees
their own; a device that is not signed in sees the not-signed-in entries.

Entries live in `auth.db` (table `history`), not in the index: a rebuild
must not touch them. No expiry -- a paid answer stays retrievable -- but at
most `KEEP_PER_USER` per user and `KEEP_TOTAL` in all.
"""
from __future__ import annotations

import json
import sqlite3
import time

from . import auth

KEEP_PER_USER = 500
KEEP_TOTAL = 5000
SHOW = 50

SCHEMA = """
CREATE TABLE IF NOT EXISTS history(
    id        INTEGER PRIMARY KEY,
    user_id   INTEGER,              -- NULL: a device not signed in, or the PC
    user_name TEXT,
    admin     INTEGER NOT NULL DEFAULT 0,
    created   REAL NOT NULL,
    kind      TEXT NOT NULL,        -- 'ask' | 'search'
    doc_id    TEXT NOT NULL,
    variant   INTEGER,
    vname     TEXT,                 -- the variant's name, re-checked on use
    engine    TEXT,
    year      INTEGER,
    label     TEXT NOT NULL,
    question  TEXT NOT NULL,
    model     TEXT,
    cost_usd  REAL,                 -- NULL: search, or cost not tracked
    result    TEXT                  -- the answer as /api/ask returned it (ask only);
                                    -- a chat's follow-ups in its `turns`
);
CREATE INDEX IF NOT EXISTS history_user ON history(user_id, created);
"""


def ensure(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def pick(vehicle: dict, variant: int | None, engine: str | None, year: int | None) -> dict:
    """The car as stored: names and label from the index row."""
    try:
        variants = json.loads(vehicle.get("variants") or "[]")
    except ValueError:
        variants = []
    x = variants[variant] if variant is not None and 0 <= variant < len(variants) else None
    make = vehicle.get("make") or ""
    name = (f"{year or ''} {make} {x.get('name') or ''}" if x else
            f"{year or vehicle.get('year_range') or ''} {make} {vehicle.get('model') or ''}")
    if engine and not x:
        name += f" · engine {engine}"
    return {"doc_id": vehicle["doc_id"], "variant": variant if x else None,
            "vname": x.get("name") if x else None, "engine": engine or None,
            "year": year, "label": " ".join(name.split())[:120] or vehicle["doc_id"]}


def add(conn: sqlite3.Connection, who: auth.Who, kind: str, car: dict, question: str,
        result: dict | None = None) -> int:
    ensure(conn)
    usage = (result or {}).get("usage") or {}
    cur = conn.execute(
        """INSERT INTO history(user_id, user_name, admin, created, kind, doc_id, variant, vname,
                               engine, year, label, question, model, cost_usd, result)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (who.user_id, who.name or None, int(who.admin), time.time(), kind, car["doc_id"],
         car["variant"], car["vname"], car["engine"], car["year"], car["label"],
         question[:2000], usage.get("model"), usage.get("cost_usd"),
         json.dumps(result) if result is not None else None))
    new_id = cur.lastrowid
    conn.execute(
        """DELETE FROM history WHERE user_id IS ? AND admin=? AND id NOT IN (
               SELECT id FROM history WHERE user_id IS ? AND admin=?
               ORDER BY created DESC, id DESC LIMIT ?)""",
        (who.user_id, int(who.admin), who.user_id, int(who.admin), KEEP_PER_USER))
    conn.execute(
        """DELETE FROM history WHERE id NOT IN (
               SELECT id FROM history ORDER BY created DESC, id DESC LIMIT ?)""", (KEEP_TOTAL,))
    conn.commit()
    return new_id


def add_turn(conn: sqlite3.Connection, entry_id: int, who: auth.Who, question: str,
             result: dict) -> int | None:
    """A follow-up in a chat, kept in the chat's entry (`result.turns`), its
    cost added to the entry's. Only an entry `who` can see; None otherwise.
    Returns the number of follow-ups now kept."""
    entry = get(conn, entry_id, who)
    if entry is None or entry["kind"] != "ask" or not entry["result"]:
        return None
    stored = entry["result"]
    stored.setdefault("turns", []).append({**result, "question": question[:2000]})
    cost = (result.get("usage") or {}).get("cost_usd")
    conn.execute(
        """UPDATE history SET result=?, created=?,
                  cost_usd=CASE WHEN ? IS NULL THEN cost_usd ELSE COALESCE(cost_usd, 0) + ? END
           WHERE id=?""",
        (json.dumps(stored), time.time(), cost, cost, entry_id))
    conn.commit()
    return len(stored["turns"])


def _scope(who: auth.Who) -> tuple[str, tuple]:
    if who.admin:
        return "1=1", ()
    if who.user_id is not None:
        return "user_id=?", (who.user_id,)
    return "user_id IS NULL AND admin=0", ()


def visible_to(conn: sqlite3.Connection, who: auth.Who, limit: int = SHOW) -> list[dict]:
    ensure(conn)
    where, args = _scope(who)
    rows = conn.execute(
        f"""SELECT id, user_name, admin, created, kind, doc_id, variant, vname, engine, year,
                   label, question, model, cost_usd,
                   json_extract(result, '$.found') AS found,
                   json_array_length(result, '$.turns') AS followups
            FROM history WHERE {where} ORDER BY created DESC, id DESC LIMIT ?""",
        (*args, limit)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["by"] = "this PC" if d.pop("admin") else (d.pop("user_name") or "not signed in")
        d.pop("user_name", None)
        if d["kind"] == "ask":
            d["found"] = bool(d["found"])
            d["followups"] = d["followups"] or 0
        else:
            d.pop("found")
            d.pop("followups")
        out.append(d)
    return out


def get(conn: sqlite3.Connection, entry_id: int, who: auth.Who) -> dict | None:
    """One entry with its stored answer -- one `who` can see, else None."""
    ensure(conn)
    where, args = _scope(who)
    r = conn.execute(
        f"""SELECT id, created, kind, doc_id, variant, vname, engine, year, label, question,
                   model, cost_usd, result FROM history WHERE id=? AND ({where})""",
        (entry_id, *args)).fetchone()
    if r is None:
        return None
    d = dict(r)
    d["result"] = json.loads(d["result"]) if d["result"] else None
    return d
