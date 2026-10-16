"""SQLite FTS5 index over normalized pages.

Chunks keep their page identity so every retrieved passage can cite a source
page. `doc_id` is stored alongside every chunk because vehicle filtering is
applied *before* ranking -- a spec from the wrong year must never surface.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from . import config
from .importers.base import PageRecord, VehicleRecord

MAX_CHUNK = 2400
SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS vehicles(
    doc_id      TEXT PRIMARY KEY,
    source_name TEXT,
    file_path   TEXT,
    make        TEXT,
    model       TEXT,
    year_range  TEXT,
    engine      TEXT,
    drivetrain  TEXT,
    doc_type    TEXT,
    page_count  INTEGER,
    indexed_pages INTEGER DEFAULT 0,
    complete    INTEGER DEFAULT 0,
    general_doc_id TEXT,
    model_code  TEXT
);

CREATE TABLE IF NOT EXISTS pages(
    page_uid     TEXT PRIMARY KEY,
    doc_id       TEXT NOT NULL,
    page_no      TEXT NOT NULL,
    file_path    TEXT,
    title        TEXT,
    section_path TEXT,
    system       TEXT,
    doc_type     TEXT,
    images       TEXT,
    links        TEXT,
    text         TEXT,
    text_hash    TEXT,
    canonical_uid TEXT,
    applicability TEXT DEFAULT 'this_vehicle'
);
CREATE INDEX IF NOT EXISTS pages_doc ON pages(doc_id);
CREATE INDEX IF NOT EXISTS pages_hash ON pages(doc_id, text_hash);
-- opening a page (get_page) by its number; without it a general-pool page
-- walked ~29k rows (~60 ms). Built once on an existing index (~1 s).
CREATE INDEX IF NOT EXISTS pages_doc_page ON pages(doc_id, page_no);

-- Every paid call is recorded so spend can be attributed after the fact,
-- rather than reconstructed from memory or read off the console days later.
CREATE TABLE IF NOT EXISTS usage_log(
    id         INTEGER PRIMARY KEY,
    ts         TEXT NOT NULL,
    source     TEXT,            -- web | cli | eval
    model      TEXT,
    doc_id     TEXT,
    question   TEXT,
    calls      INTEGER,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    cost_usd   REAL
);
CREATE INDEX IF NOT EXISTS usage_ts ON usage_log(ts);

-- Connector IDs (C906, C1035A) appear as bare text throughout wiring and
-- diagnostic pages, but the archive files their pinout and their physical
-- location on separate pages. This maps the id to both so a mention can be
-- turned into a link.
CREATE TABLE IF NOT EXISTS connectors(
    doc_id       TEXT NOT NULL,
    connector_id TEXT NOT NULL,
    kind         TEXT NOT NULL,   -- 'endview' (pinout) | 'location'
    page_no      TEXT NOT NULL,
    title        TEXT,
    PRIMARY KEY (doc_id, connector_id, kind)
);

CREATE TABLE IF NOT EXISTS chunks(
    id           INTEGER PRIMARY KEY,
    page_uid     TEXT NOT NULL,
    doc_id       TEXT NOT NULL,
    ord          INTEGER,
    title        TEXT,
    section_path TEXT,
    body         TEXT,
    parts        INTEGER DEFAULT 0   -- 1: a parts catalogue's chunk, in parts_fts
);
CREATE INDEX IF NOT EXISTS chunks_page ON chunks(page_uid);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    title, section_path, body,
    content='chunks', content_rowid='id', tokenize='porter unicode61'
);

-- A parts catalogue (EPC) gets its own full-text index. BM25 statistics are
-- index-wide, so 213k EPC chunks in chunks_fts shifted WIS rankings even for
-- searches that never touch EPC (eval_wis: w204-c220cdi-glow-plugs gained a
-- wrong-model page). In their own table they cannot. The triggers that route
-- each chunk by `chunks.parts` are made in _migrate (FTS_TRIGGERS).
CREATE VIRTUAL TABLE IF NOT EXISTS parts_fts USING fts5(
    title, section_path, body,
    content='chunks', content_rowid='id', tokenize='porter unicode61'
);
"""

# Each chunk is indexed in exactly one FTS table, chosen by `chunks.parts`;
# the delete triggers use the same flag, so removing a manual keeps both
# tables consistent (an external-content FTS5 table must be told the old
# values of exactly the rows it holds).
FTS_TRIGGERS = """
CREATE TRIGGER chunks_ai AFTER INSERT ON chunks WHEN new.parts IS NOT 1 BEGIN
    INSERT INTO chunks_fts(rowid, title, section_path, body)
    VALUES (new.id, new.title, new.section_path, new.body);
END;
CREATE TRIGGER chunks_ad AFTER DELETE ON chunks WHEN old.parts IS NOT 1 BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, title, section_path, body)
    VALUES ('delete', old.id, old.title, old.section_path, old.body);
END;
CREATE TRIGGER chunks_ai_parts AFTER INSERT ON chunks WHEN new.parts = 1 BEGIN
    INSERT INTO parts_fts(rowid, title, section_path, body)
    VALUES (new.id, new.title, new.section_path, new.body);
END;
CREATE TRIGGER chunks_ad_parts AFTER DELETE ON chunks WHEN old.parts = 1 BEGIN
    INSERT INTO parts_fts(parts_fts, rowid, title, section_path, body)
    VALUES ('delete', old.id, old.title, old.section_path, old.body);
END;
"""


# Columns added to `vehicles` after the first release. CREATE TABLE only runs
# on a new index, so an existing one has to be altered -- a 189 MB index that
# took 40s to build should not need rebuilding to gain a nullable column.
ADDED_COLUMNS = {
    "vehicles": {
        "general_doc_id": "TEXT",
        "model_code": "TEXT",
        "vehicle_types": "TEXT",
        "variants": "TEXT",      # JSON list, see VehicleRecord.variants
        "vin_codes": "TEXT",
    },
    "chunks": {
        "parts": "INTEGER DEFAULT 0",  # see parts_fts
    },
    "pages": {
        "vehicle_types": "TEXT",
        "engines": "TEXT",       # ",272,642," -- see PageRecord.engines
        "codes_only": "TEXT",    # ",102.942," -- see PageRecord.codes_only
        "codes_except": "TEXT",  # ",102.983," -- see PageRecord.codes_except
    },
    "usage_log": {
        "user_id": "INTEGER",    # auth.db users.id; NULL = this PC, or no sign-in
        "user_name": "TEXT",     # the name at the time, so a report survives
                                 # a renamed or removed account
        "key_ref": "TEXT",       # whose key paid: "company" | "user:<id>"
                                 # (config.pick_key); NULL = before 2026-10-16;
                                 # ":gemini" etc. after it for other providers
        "provider": "TEXT",      # "anthropic" | "gemini" | "xai" | "deepseek"
    },                           # (providers.py); NULL = before providers = anthropic
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in ADDED_COLUMNS.items():
        have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not have:  # no such table (a bare backup); the schema makes it whole
            continue
        for name, decl in columns.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    _migrate_fts(conn)


def _migrate_fts(conn: sqlite3.Connection) -> None:
    """Install the routing triggers once; move parts chunks already indexed.

    An index from before parts_fts has unconditional triggers and, if it ever
    took a parts catalogue, those chunks in chunks_fts. Done once: afterwards
    every trigger exists and this returns after one sqlite_master read.
    """
    have = {r[0]: r[1] or "" for r in conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='trigger'")}
    if "chunks_ai_parts" in have and "parts" in have.get("chunks_ai", ""):
        return
    with conn:
        for name in ("chunks_ai", "chunks_ad", "chunks_ai_parts", "chunks_ad_parts"):
            conn.execute(f"DROP TRIGGER IF EXISTS {name}")
        for stmt in FTS_TRIGGERS.split("END;"):
            if stmt.strip():
                conn.execute(stmt + "END;")
        # parts chunks indexed by the old triggers: out of chunks_fts, into parts_fts
        conn.execute("""CREATE TEMP TABLE IF NOT EXISTS _move AS
                        SELECT c.id FROM chunks c JOIN pages p ON p.page_uid = c.page_uid
                        WHERE p.doc_type = 'parts' AND c.parts IS NOT 1""")
        conn.execute("""INSERT INTO chunks_fts(chunks_fts, rowid, title, section_path, body)
                        SELECT 'delete', id, title, section_path, body FROM chunks
                        WHERE id IN (SELECT id FROM _move)""")
        conn.execute("UPDATE chunks SET parts = 1 WHERE id IN (SELECT id FROM _move)")
        conn.execute("""INSERT INTO parts_fts(rowid, title, section_path, body)
                        SELECT id, title, section_path, body FROM chunks
                        WHERE id IN (SELECT id FROM _move)""")
        conn.execute("DROP TABLE _move")


def connect(path: str | Path, setup: bool = True) -> sqlite3.Connection:
    """Open the index. `setup` creates missing tables and indexes and adds
    new columns -- once, when a process starts (the server does it before it
    listens). Per-request connections pass setup=False: on a 3.5 GB index
    the first CREATE INDEX takes seconds, and requests that each tried it
    queued on the write lock and failed with "database is locked" (500s on
    the first page load after an upgrade)."""
    path = Path(path)
    if setup:
        if path.parent == config.APP_DIR:
            config.make_private_dir(path.parent)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
    # an import job writes in another process; wait for it rather than fail
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    if setup:
        conn.executescript(SCHEMA)
        _migrate(conn)
    return conn


def chunk_text(text, max_chars=MAX_CHUNK):
    """Split on blank lines, then newlines, then hard-split as a last resort.

    Table rows stay adjacent so a torque table is not severed mid-row.
    """
    buf = []
    size = 0

    def flush():
        nonlocal buf, size
        out = "\n\n".join(buf).strip() if buf else ""
        buf, size = [], 0
        return out

    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        if len(para) > max_chars:
            pending = flush()
            if pending:
                yield pending
            sub, sub_len = [], 0
            for line in para.split("\n"):
                while len(line) > max_chars:
                    if sub:
                        yield "\n".join(sub)
                        sub, sub_len = [], 0
                    yield line[:max_chars]
                    line = line[max_chars:]
                if sub_len + len(line) > max_chars and sub:
                    yield "\n".join(sub)
                    sub, sub_len = [], 0
                sub.append(line)
                sub_len += len(line) + 1
            if sub:
                yield "\n".join(sub)
            continue
        if size + len(para) > max_chars and buf:
            pending = flush()
            if pending:
                yield pending
        buf.append(para)
        size += len(para) + 2
    pending = flush()
    if pending:
        yield pending


def upsert_vehicle(conn: sqlite3.Connection, v: VehicleRecord) -> None:
    conn.execute(
        """INSERT INTO vehicles
           (doc_id, source_name, file_path, make, model, year_range, engine,
            drivetrain, doc_type, page_count, general_doc_id, model_code,
            vehicle_types, variants, vin_codes)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(doc_id) DO UPDATE SET
             source_name=excluded.source_name, file_path=excluded.file_path,
             make=excluded.make, model=excluded.model,
             year_range=excluded.year_range, engine=excluded.engine,
             drivetrain=excluded.drivetrain, page_count=excluded.page_count,
             general_doc_id=excluded.general_doc_id,
             model_code=excluded.model_code,
             vehicle_types=excluded.vehicle_types,
             variants=excluded.variants, vin_codes=excluded.vin_codes""",
        (v.doc_id, v.source_name, v.file_path, v.make, v.model, v.year_range,
         v.engine, v.drivetrain, v.doc_type, v.page_count,
         v.general_doc_id, v.model_code, v.vehicle_types,
         json.dumps(v.variants, ensure_ascii=False) if v.variants else None,
         v.vin_codes or None),
    )


def indexed_page_uids(conn: sqlite3.Connection, doc_id: str) -> set[str]:
    return {r[0] for r in conn.execute(
        "SELECT page_uid FROM pages WHERE doc_id=?", (doc_id,))}


def add_page(conn: sqlite3.Connection, page: PageRecord) -> int:
    """Insert a page and its chunks. Replaces any prior copy of the page.

    Archives commonly file the same article under several folders -- this one
    repeats every article about three times. Only the first copy of a given
    text is chunked; later copies are recorded and pointed at the canonical
    page, so retrieval returns three distinct answers instead of one answer
    three times.
    """
    uid = page.page_uid
    conn.execute("DELETE FROM chunks WHERE page_uid=?", (uid,))
    conn.execute("DELETE FROM pages WHERE page_uid=?", (uid,))

    section = " > ".join(page.section_path)
    digest = hashlib.sha1(page.text.encode("utf-8")).hexdigest()
    prior = conn.execute(
        """SELECT page_uid FROM pages
           WHERE doc_id=? AND text_hash=? AND canonical_uid IS NULL LIMIT 1""",
        (page.doc_id, digest),
    ).fetchone()
    canonical = prior[0] if prior else None

    conn.execute(
        """INSERT INTO pages
           (page_uid, doc_id, page_no, file_path, title, section_path,
            system, doc_type, images, links, text, text_hash, canonical_uid,
            applicability, vehicle_types, engines, codes_only, codes_except)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (uid, page.doc_id, page.page_no, page.file_path, page.title, section,
         page.system, page.doc_type, "\n".join(page.images),
         "\n".join(page.links), page.text,
         digest, canonical, page.applicability, page.vehicle_types, page.engines,
         page.codes_only, page.codes_except),
    )
    if canonical:
        return 0  # duplicate of an already-chunked page

    parts = 1 if page.doc_type == "parts" else 0     # -> parts_fts, see SCHEMA
    rows = [
        (uid, page.doc_id, n, page.title, section, body, parts)
        for n, body in enumerate(chunk_text(page.text))
    ]
    conn.executemany(
        """INSERT INTO chunks (page_uid, doc_id, ord, title, section_path, body, parts)
           VALUES (?,?,?,?,?,?,?)""", rows)
    return len(rows)


def mark_complete(conn: sqlite3.Connection, doc_id: str) -> None:
    n = conn.execute("SELECT COUNT(*) FROM pages WHERE doc_id=?", (doc_id,)).fetchone()[0]
    conn.execute("UPDATE vehicles SET complete=1, indexed_pages=? WHERE doc_id=?", (n, doc_id))


ENDVIEW_RE = re.compile(r"^Connector End View \((C\d+[A-Z]*)\)")
LOCATION_RE = re.compile(r"^(C\d+[A-Z]*)\s")


def build_connector_index(conn: sqlite3.Connection, doc_id: str) -> int:
    """Map connector ids to their pinout and location pages.

    Derived from page titles after indexing rather than during it, so it only
    ever points at canonical pages and can be rebuilt without a re-ingest.
    """
    conn.execute("DELETE FROM connectors WHERE doc_id=?", (doc_id,))
    rows: list[tuple] = []
    for page_no, title in conn.execute(
        """SELECT page_no, title FROM pages
           WHERE doc_id=? AND canonical_uid IS NULL""", (doc_id,)):
        title = title or ""
        m = ENDVIEW_RE.match(title)
        if m:
            rows.append((doc_id, m.group(1), "endview", page_no, title))
            continue
        m = LOCATION_RE.match(title)
        if m:
            rows.append((doc_id, m.group(1), "location", page_no, title))
    conn.executemany(
        """INSERT OR REPLACE INTO connectors
           (doc_id, connector_id, kind, page_no, title) VALUES (?,?,?,?,?)""",
        rows)
    return len({r[1] for r in rows})


def connector_ids(conn: sqlite3.Connection, doc_id: str) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT DISTINCT connector_id FROM connectors WHERE doc_id=?", (doc_id,))]


def connector_pages(conn: sqlite3.Connection, doc_id: str,
                    connector_id: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT kind, page_no, title FROM connectors
           WHERE doc_id=? AND connector_id=? ORDER BY kind""",
        (doc_id, connector_id.upper()))]


def log_usage(conn: sqlite3.Connection, source: str, doc_id: str,
              question: str, usage, user_id: int | None = None,
              user_name: str | None = None, key_ref: str | None = None) -> None:
    """Record one question's spend. Never let logging break a good answer.
    cost_usd is NULL when the model's price is not known ("cost not tracked")."""
    try:
        conn.execute(
            """INSERT INTO usage_log
               (ts, source, model, doc_id, question, calls,
                input_tokens, output_tokens, cost_usd, user_id, user_name, key_ref,
                provider)
               VALUES (datetime('now'),?,?,?,?,?,?,?,?,?,?,?,?)""",
            (source, usage.model, doc_id, question[:500], usage.calls,
             usage.input_tokens, usage.output_tokens, usage.cost_usd,
             user_id, user_name, key_ref, getattr(usage, "provider", None)),
        )
        conn.commit()
    except sqlite3.Error:
        pass


def usage_summary(conn: sqlite3.Connection) -> dict:
    by_source = [dict(r) for r in conn.execute(
        """SELECT source, COUNT(*) AS questions, SUM(calls) AS calls,
                  ROUND(SUM(cost_usd), 4) AS cost_usd
           FROM usage_log GROUP BY source ORDER BY cost_usd DESC""")]
    by_day = [dict(r) for r in conn.execute(
        """SELECT date(ts) AS day, COUNT(*) AS questions,
                  ROUND(SUM(cost_usd), 4) AS cost_usd
           FROM usage_log GROUP BY day ORDER BY day DESC LIMIT 14""")]
    by_user = [dict(r) for r in conn.execute(
        """SELECT COALESCE(user_name, CASE source WHEN 'web' THEN 'this PC / no sign-in'
                                      ELSE source END) AS user,
                  COUNT(*) AS questions, ROUND(SUM(cost_usd), 4) AS cost_usd
           FROM usage_log GROUP BY user ORDER BY cost_usd DESC""")]
    total = conn.execute(
        "SELECT ROUND(COALESCE(SUM(cost_usd),0), 4) FROM usage_log").fetchone()[0]
    return {"total_usd": total, "by_source": by_source, "by_day": by_day,
            "by_user": by_user}


# usage_log.ts is UTC (datetime('now')); a month is a UTC calendar month
MONTH_START = "strftime('%Y-%m-01 00:00:00','now')"


def month_spend(conn: sqlite3.Connection, user_id: int) -> float:
    return conn.execute(
        f"""SELECT COALESCE(SUM(cost_usd),0) FROM usage_log
            WHERE user_id=? AND ts >= {MONTH_START}""", (user_id,)).fetchone()[0]


def month_spend_by_user(conn: sqlite3.Connection) -> dict:
    """{user_id: (questions, cost)} this month; key None = this PC / no sign-in."""
    return {r[0]: (r[1], r[2]) for r in conn.execute(
        f"""SELECT user_id, COUNT(*), COALESCE(SUM(cost_usd),0) FROM usage_log
            WHERE source='web' AND ts >= {MONTH_START} GROUP BY user_id""")}


def month_questions(conn: sqlite3.Connection, user_id: int) -> int:
    return conn.execute(
        f"""SELECT COUNT(*) FROM usage_log
            WHERE user_id=? AND ts >= {MONTH_START}""", (user_id,)).fetchone()[0]


def month_total(conn: sqlite3.Connection) -> tuple[int, float]:
    """(questions, dollars) this month, everyone and every source -- what
    the company key paid in "One company key" mode, plus any cli/eval use."""
    n, c = conn.execute(
        f"""SELECT COUNT(*), COALESCE(SUM(cost_usd),0) FROM usage_log
            WHERE ts >= {MONTH_START}""").fetchone()
    return n, c


def key_spend_since(conn: sqlite3.Connection, key_ref: str, since: str) -> float:
    """Dollars logged on one key (usage_log.key_ref) since `since` (UTC
    'YYYY-MM-DD HH:MM:SS', when the owner last said how much was loaded)."""
    return conn.execute(
        """SELECT COALESCE(SUM(cost_usd),0) FROM usage_log
           WHERE key_ref=? AND ts >= ?""", (key_ref, since or "")).fetchone()[0]


def recent_usage(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT ts, source, COALESCE(provider, 'anthropic') AS provider, model,
                  question, cost_usd
           FROM usage_log ORDER BY id DESC LIMIT ?""", (limit,))]


def vehicles(conn: sqlite3.Connection) -> list[dict]:
    return link_sources([dict(r) for r in conn.execute(
        "SELECT * FROM vehicles ORDER BY year_range, make, model")])


def _maker(v: dict) -> str:
    # "mercedes-wis-virtualbox", "mercedes-epc" -> "mercedes"
    return (v.get("source_name") or "").split("-")[0]


def link_sources(rows: list[dict]) -> list[dict]:
    """Join a parts catalogue's vehicles to the manual's cars of the same series.

    Each source builds its own vehicles, so EPC's W210 parts would be a second
    "W210" beside the WIS one, and the W210 a shop picks would never search
    them. The join is made here, at query time, so each source stays
    independently rebuildable and removable:

    - a manual vehicle gets `joined_ids`: the parts vehicles of the same maker
      and `model_code` ("210", or an engine bucket "E112");
    - a car gets `engine_joins`: engine family -> parts engine buckets, for
      every engine its variants use, so its engines' parts are reachable even
      when the manual has no bucket for that engine;
    - a parts vehicle that a manual vehicle covers gets `joined_into` and is
      left out of the picker (`/api/vehicles`). One with no manual counterpart
      stays listed on its own.

    Only `doc_type == "parts"` vehicles are ever joined in: a manual is never
    mixed into another manual's scope (two languages of WIS share codes).
    """
    for v in rows:
        v["joined_ids"], v["joined_into"], v["engine_joins"] = [], "", {}
    parts: dict[tuple[str, str], list[dict]] = {}
    for v in rows:
        code = (v.get("model_code") or "").strip()
        if code and v.get("doc_type") == "parts":
            parts.setdefault((_maker(v), code), []).append(v)
    if not parts:
        return rows
    for v in rows:
        if v.get("doc_type") == "parts":
            continue
        maker, code = _maker(v), (v.get("model_code") or "").strip()
        for p in parts.get((maker, code), []) if code else []:
            v["joined_ids"].append(p["doc_id"])
            p["joined_into"] = p["joined_into"] or v["doc_id"]
        try:
            variants = json.loads(v.get("variants") or "[]")
        except ValueError:
            variants = []
        for fam in sorted({str(e)[:3] for x in variants for e in x.get("engines", [])
                           if str(e)[:3].isdigit()}):
            ids = [p["doc_id"] for p in parts.get((maker, "E" + fam), [])]
            if ids:
                v["engine_joins"][fam] = ids
    return rows


def stats(conn: sqlite3.Connection) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        "vehicles": q("SELECT COUNT(*) FROM vehicles"),
        "pages": q("SELECT COUNT(*) FROM pages"),
        "chunks": q("SELECT COUNT(*) FROM chunks"),
        "unique_pages": q("SELECT COUNT(*) FROM pages WHERE canonical_uid IS NULL"),
        "other_variant": q("SELECT COUNT(*) FROM pages WHERE applicability='other_variant'"),
    }
