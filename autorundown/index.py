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
from typing import Iterable, Iterator

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
    body         TEXT
);
CREATE INDEX IF NOT EXISTS chunks_page ON chunks(page_uid);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    title, section_path, body,
    content='chunks', content_rowid='id', tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, title, section_path, body)
    VALUES (new.id, new.title, new.section_path, new.body);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, title, section_path, body)
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
    "pages": {
        "vehicle_types": "TEXT",
        "engines": "TEXT",       # ",272,642," -- see PageRecord.engines
        "codes_only": "TEXT",    # ",102.942," -- see PageRecord.codes_only
        "codes_except": "TEXT",  # ",102.983," -- see PageRecord.codes_except
    },
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in ADDED_COLUMNS.items():
        have = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
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

    rows = [
        (uid, page.doc_id, n, page.title, section, body)
        for n, body in enumerate(chunk_text(page.text))
    ]
    conn.executemany(
        """INSERT INTO chunks (page_uid, doc_id, ord, title, section_path, body)
           VALUES (?,?,?,?,?,?)""", rows)
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
              question: str, usage) -> None:
    """Record one question's spend. Never let logging break a good answer."""
    try:
        conn.execute(
            """INSERT INTO usage_log
               (ts, source, model, doc_id, question, calls,
                input_tokens, output_tokens, cost_usd)
               VALUES (datetime('now'),?,?,?,?,?,?,?,?)""",
            (source, usage.model, doc_id, question[:500], usage.calls,
             usage.input_tokens, usage.output_tokens, usage.cost_usd),
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
    total = conn.execute(
        "SELECT ROUND(COALESCE(SUM(cost_usd),0), 4) FROM usage_log").fetchone()[0]
    return {"total_usd": total, "by_source": by_source, "by_day": by_day}


def recent_usage(conn: sqlite3.Connection, limit: int = 20) -> list[dict]:
    return [dict(r) for r in conn.execute(
        """SELECT ts, source, model, question, cost_usd
           FROM usage_log ORDER BY id DESC LIMIT ?""", (limit,))]


def vehicles(conn: sqlite3.Connection) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM vehicles ORDER BY year_range, make, model")]


def stats(conn: sqlite3.Connection) -> dict:
    q = lambda sql: conn.execute(sql).fetchone()[0]
    return {
        "vehicles": q("SELECT COUNT(*) FROM vehicles"),
        "pages": q("SELECT COUNT(*) FROM pages"),
        "chunks": q("SELECT COUNT(*) FROM chunks"),
        "unique_pages": q("SELECT COUNT(*) FROM pages WHERE canonical_uid IS NULL"),
        "other_variant": q("SELECT COUNT(*) FROM pages WHERE applicability='other_variant'"),
    }
