"""Retrieval over the FTS5 index.

Vehicle filtering is applied in the SQL itself, before ranking, so a chunk
from another vehicle can never outrank a correct one. Ranking is BM25 with
the title and section path weighted above the body -- section headings in
this archive are highly descriptive ("Brakes > Front Disc > Torque Specs").

A vehicle may be searched together with a companion doc_id (`scope_ids`), for
sources that file unscoped documents separately from per-model ones. Every
query is still filtered to one doc_id before ranking; the scope only decides
which doc_ids are queried at all.
"""
from __future__ import annotations

import json
import re
import sqlite3
from typing import Iterable, Sequence

# letters of any language: "Ölwanne" was searched as "lwanne". The FTS
# tokenizer (unicode61) folds the accents on both sides.
TOKEN_RE = re.compile(r"[^\W_][\w./-]*")
STOPWORDS = {
    "a", "an", "the", "of", "for", "on", "in", "to", "is", "are", "what",
    "whats", "how", "do", "i", "my", "me", "and", "or", "it", "its", "be",
    "with", "at", "from", "this", "that", "you", "your", "can", "should",
}
# (title, section_path, body) -- SQLite BM25: lower score = better match
WEIGHTS = (6.0, 3.0, 1.0)
CODE_BOOST = 1.35  # see _run: pages naming the chosen car's exact engine codes


def tokenize(query: str) -> list[str]:
    tokens = [t.lower() for t in TOKEN_RE.findall(query)]
    kept = [t for t in tokens if t not in STOPWORDS and len(t) > 1]
    return kept or tokens


def build_match(query: str, mode: str = "and") -> str:
    """Build a safe FTS5 MATCH expression.

    Every token is double-quoted, which both escapes FTS operators and keeps
    part numbers like `3.5l` or `f-150` intact as single terms.
    """
    tokens = tokenize(query)
    if not tokens:
        return ""
    quoted = ['"' + t.replace('"', "") + '"' for t in tokens]
    joiner = " AND " if mode == "and" else " OR "
    return joiner.join(quoted)


def _types_clause(types: str) -> tuple[str, list]:
    """`types` (",0,2,") keeps only pages flagged for at least one of those
    vehicle types. Unflagged pages carry no restriction and always pass."""
    bits = [b for b in types.split(",") if b]
    if not bits:
        return "", []
    return ("(COALESCE(p.vehicle_types, '') = '' OR "
            + " OR ".join("instr(p.vehicle_types, ?) > 0" for _ in bits) + ")",
            [f",{b}," for b in bits])


def _filters(include_other_variants: bool, engines: tuple[str, ...] = (),
             codes: tuple = ((), ())) -> tuple[str, list, str, list]:
    """The page filters every search applies, in SQL before ranking:
    (where-clause, its args, score-boost expression, its args)."""
    variant_clause = "" if include_other_variants else \
        " AND p.applicability = 'this_vehicle'"
    type_clause, type_args = "", []
    # `engines` ("272",): drop pages limited to *other* engines -- in SQL,
    # before ranking, like the vehicle filter. Pages naming no engine pass.
    if engines:
        type_clause += (" AND (COALESCE(p.engines, '') = '' OR "
                        + " OR ".join("instr(p.engines, ?) > 0" for _ in engines) + ")")
        type_args += [f",{e}," for e in engines]
    # `codes` = (the car's model codes, its engine codes), exact: "201.034",
    # "102.983". A page that excludes *all* of either ("except 102.983/99")
    # or names other codes as its only targets ("Engine 102.942 ...") is not
    # for this car. All-of, so a choice spanning several codes loses nothing.
    vcodes, ecodes = codes
    if vcodes or ecodes:
        for group in (vcodes, ecodes):
            if group:
                type_clause += (" AND (COALESCE(p.codes_except, '') = '' OR "
                                + " OR ".join("instr(p.codes_except, ?) = 0" for _ in group) + ")")
                type_args += [f",{c}," for c in group]
        mine = (*vcodes, *ecodes)
        type_clause += (" AND (COALESCE(p.codes_only, '') = '' OR "
                        + " OR ".join("instr(p.codes_only, ?) > 0" for _ in mine) + ")")
        type_args += [f",{c}," for c in mine]
    # A page that names the chosen car's exact codes ("engine 102.983") is
    # about that car, and ranks ahead of pages that merely pass the filters:
    # for the 190E 2.3-16 the 16-valve head cover page (cap nuts 10 Nm) sat
    # at #14 behind 8-valve M102 pages (15 Nm). BM25 is negative, lower is
    # better, so the boost multiplies.
    # Engine codes only: boosting on model codes too (a page listing "124.030"
    # among dozens) pushed the right W124 brake-pad pages out of the top 10.
    mine = tuple(codes[1])
    boost_sql, boost_args = "", []
    if mine and CODE_BOOST != 1.0:
        boost_sql = (" * (CASE WHEN " + " OR ".join("instr(p.text, ?) > 0" for _ in mine)
                     + f" THEN {CODE_BOOST} ELSE 1 END)")
        boost_args = list(mine)
    return variant_clause + type_clause, type_args, boost_sql, boost_args


def _run(conn: sqlite3.Connection, doc_id: str, match: str, limit: int,
         include_other_variants: bool, types: str = "",
         engines: tuple[str, ...] = (), codes: tuple = ((), ())) -> list[dict]:
    if not match:
        return []
    where, args, boost_sql, boost_args = _filters(include_other_variants, engines, codes)
    tclause, targs = _types_clause(types)
    if tclause:
        where += " AND " + tclause          # appended last, so its args go last
        args = args + targs
    sql = f"""
        SELECT c.page_uid, c.doc_id, c.ord, c.title, c.section_path, c.body,
               p.page_no, p.system, p.doc_type, p.images, p.applicability,
               bm25(chunks_fts, ?, ?, ?){boost_sql} AS score
        FROM chunks_fts
        JOIN chunks c ON c.id = chunks_fts.rowid
        JOIN pages  p ON p.page_uid = c.page_uid
        WHERE chunks_fts MATCH ? AND c.doc_id = ?{where}
        ORDER BY score
        LIMIT ?
    """
    try:
        rows = conn.execute(
            sql, (*WEIGHTS, *boost_args, match, doc_id, *args, limit)).fetchall()
    except sqlite3.OperationalError:
        return []  # malformed MATCH expression
    return [dict(r) for r in rows]


def _run_scope(conn: sqlite3.Connection, doc_ids: list[str], match: str, limit: int,
               include_other_variants: bool, types: str = "", general: str = "",
               engines: tuple[str, ...] = (), codes: tuple = ((), ())) -> dict[str, list[dict]]:
    """`_run` for several doc_ids at once: the best `limit` rows of each.

    FTS5 finds and scores every match in the whole index before the doc_id
    filter drops other vehicles' rows, so running `_run` once per bucket did
    that whole-index work 12-14 times for one generation (7-20 s per keyword
    search on a full-size synthetic index). One statement does it once and
    ranks within each doc_id with a window function. The filters are the
    same, still in SQL before ranking; only `general` is type-narrowed.
    """
    if not match or not doc_ids:
        return {}
    where, args, boost_sql, boost_args = _filters(include_other_variants, engines, codes)
    tclause, targs = _types_clause(types)
    if tclause and general in doc_ids:
        where += f" AND (c.doc_id != ? OR {tclause})"
        args = [*args, general, *targs]
    marks = ",".join("?" for _ in doc_ids)
    sql = f"""
        SELECT * FROM (
            SELECT x.*, ROW_NUMBER() OVER (PARTITION BY x.doc_id ORDER BY x.score) AS rn
            FROM (
                SELECT c.page_uid, c.doc_id, c.ord, c.title, c.section_path, c.body,
                       p.page_no, p.system, p.doc_type, p.images, p.applicability,
                       bm25(chunks_fts, ?, ?, ?){boost_sql} AS score
                FROM chunks_fts
                JOIN chunks c ON c.id = chunks_fts.rowid
                JOIN pages  p ON p.page_uid = c.page_uid
                WHERE chunks_fts MATCH ? AND c.doc_id IN ({marks}){where}
            ) x
        ) WHERE rn <= ?
        ORDER BY doc_id, score
    """
    try:
        rows = conn.execute(sql, (*WEIGHTS, *boost_args, match, *doc_ids, *args, limit)).fetchall()
    except sqlite3.OperationalError:
        return {}  # malformed MATCH expression
    out: dict[str, list[dict]] = {}
    for r in rows:
        d = dict(r)
        d.pop("rn", None)
        out.setdefault(d["doc_id"], []).append(d)
    return out


def _search_scope(conn: sqlite3.Connection, doc_ids: list[str], query: str, limit: int,
                  include_other_variants: bool, types: str = "", general: str = "",
                  engines: tuple[str, ...] = (), codes: tuple = ((), ())) -> list[list[dict]]:
    """`_search_one` for every doc_id of a scope, in two statements: AND
    first, then OR only for the doc_ids that came up short. Returns the lists
    in `doc_ids` order, exactly as one `_search_one` per doc_id would."""
    lists = _run_scope(conn, doc_ids, build_match(query, "and"), limit,
                       include_other_variants, types, general, engines, codes)
    short = [d for d in doc_ids if len(lists.get(d, [])) < limit]
    if short:
        more = _run_scope(conn, short, build_match(query, "or"), limit * 2,
                          include_other_variants, types, general, engines, codes)
        for d in short:
            results = lists.setdefault(d, [])
            seen = {r["page_uid"] + str(r["ord"]) for r in results}
            for row in more.get(d, []):
                key = row["page_uid"] + str(row["ord"])
                if key not in seen:
                    seen.add(key)
                    results.append(row)
                if len(results) >= limit:
                    break
    return [lists.get(d, []) for d in doc_ids]


def as_ids(doc_ids: str | Iterable[str]) -> list[str]:
    """Accept a single doc_id or several, so old call sites keep working."""
    if isinstance(doc_ids, str):
        return [doc_ids]
    return [d for d in doc_ids if d]


class Scope(list):
    """The doc_ids a question may draw on, first one being the vehicle itself.

    Carries `types`, the vehicle-type bits (",0,2,") that narrow the `general`
    doc_id -- and only that one. The vehicle's own pages and its engines' pages
    are named by Mercedes' own tables, which is stronger evidence than a type
    bit. A plain list or string still works everywhere a Scope does -- it just
    narrows nothing.
    """
    types: str = ""
    general: str = ""
    engines: tuple = ()     # engine families chosen ("272",); () = any
    codes: tuple = ((), ())  # (model codes, engine codes) of the chosen car


def scope_ids(vehicle: dict, variant: int | None = None,
              engine: str | None = None) -> Scope:
    """Which doc_ids a question about this vehicle may draw on.

    A source that splits by model files the documents tied to no model
    separately -- on the real WIS corpus 49,299 of them, ~20%. They are
    searched alongside the selected model, but not all of them apply to every
    car: bus engine specs and truck bulletins sit in there too. WIS flags every
    document with vehicle-type bits, and each model's own documents carry a
    consistent set, so a general document is only in scope when it shares a
    type with the selected model. The selected vehicle leads, and `search`
    interleaves, so a general document can never crowd the model's own pages
    out of the budget.

    Engines come next. WIS files engine documents under the engine's own
    series (the 190E 2.3-16's head-gasket check lives under M102, never under
    the W201), so the engine buckets of the chosen `variant` are added -- or
    of every variant, when the series itself is chosen. That is the one thing
    choosing a specific model changes: the car's manual is per series, its
    engine is per variant.
    """
    scope = Scope([vehicle["doc_id"]])
    try:
        variants = json.loads(vehicle.get("variants") or "[]")
    except ValueError:
        variants = []
    # `engine` ("272", or "272,276"): the engine families to narrow to -- the
    # picker's "Any M272", or what a year + car name resolves to (a 2010 E 350
    # is the M272, a 2013 one the M276). A W212 asked about its valve cover
    # with nothing chosen searched all 10 engines and answered for V6, V8 and
    # diesel side by side.
    fams = tuple(sorted({f.strip() for f in (engine or "").split(",") if f.strip()}))
    has_variant = variant is not None and 0 <= variant < len(variants)
    if has_variant:
        chosen = [variants[variant]]
    elif fams:
        chosen = [v for v in variants
                  if any(str(e)[:3] in fams for e in v.get("engines", []))] or variants
    else:
        chosen = variants
    for v in chosen:
        for eid in v.get("engine_ids", []):
            # one sales name can span engines ("E 300": M272, later M276), so
            # an engine choice adds only those engines' buckets ("...-e272")
            if fams and not any(eid.lower().endswith(f"-e{f}") for f in fams):
                continue
            if eid not in scope:
                scope.append(eid)
    # A parts catalogue of another source (EPC with WIS), joined by
    # index.link_sources: the series' parts, then the parts of the chosen
    # engines -- the same engine choice narrows them. Each is still its own
    # doc_id, filtered in SQL before ranking like every other bucket, and its
    # page numbers are prefixed ("P123"), so no citation can cross sources.
    for pid in vehicle.get("joined_ids") or []:
        if pid not in scope:
            scope.append(pid)
    joins = vehicle.get("engine_joins") or {}
    for fam in sorted({str(e)[:3] for v in chosen for e in v.get("engines", [])}):
        if fams and fam not in fams:
            continue
        for pid in joins.get(fam, []):
            if pid not in scope:
                scope.append(pid)
    general = (vehicle.get("general_doc_id") or "").strip()
    if general and general not in scope:
        scope.append(general)
        scope.general = general
    scope.types = (vehicle.get("vehicle_types") or "").strip()
    if has_variant:
        # the chosen car's exact codes, narrowed to the chosen engines when
        # the year picked them (a 2010 E 350 is 212.056 / 272.980 only)
        x = variants[variant]
        ebm = x.get("engine_by_bm") or {}
        bms = [b for b in x.get("baumuster", [])
               if not fams or not ebm.get(b) or any(str(e)[:3] in fams for e in ebm[b])]
        engs = [e for e in x.get("engines", []) if not fams or str(e)[:3] in fams]
        scope.codes = (tuple(sorted(bms)), tuple(sorted(engs)))
    if fams:
        scope.engines = fams
    elif has_variant:
        scope.engines = tuple(sorted({str(e)[:3] for e in variants[variant].get("engines", [])
                                      if str(e)[:3].isdigit()}))
    return scope


def _search_one(conn: sqlite3.Connection, doc_id: str, query: str, limit: int,
                include_other_variants: bool, types: str = "",
                engines: tuple[str, ...] = (), codes: tuple = ((), ())) -> list[dict]:
    """AND-first for precision, topped up with OR for recall.

    Pages belonging to a different trim or engine are excluded by default.
    """
    results = _run(conn, doc_id, build_match(query, "and"), limit,
                   include_other_variants, types, engines, codes)
    if len(results) < limit:
        seen = {r["page_uid"] + str(r["ord"]) for r in results}
        for row in _run(conn, doc_id, build_match(query, "or"), limit * 2,
                        include_other_variants, types, engines, codes):
            key = row["page_uid"] + str(row["ord"])
            if key not in seen:
                seen.add(key)
                results.append(row)
            if len(results) >= limit:
                break
    return results


def _interleave(ranked_lists: Sequence[list[dict]], cap: int,
                by_score: bool = False) -> list[dict]:
    """Take one from each list in turn, so no list can fill the budget alone.

    `by_score` orders the picks *within* each round. Use it only when the
    lists answer the same query, where BM25 scores are comparable -- then a
    round-robin still guarantees each list its share without letting a weak
    hit from the first list outrank a strong one from the second. Across
    different queries the scores mean different things, so the lists are taken
    in the order given instead.
    """
    merged: list[dict] = []
    seen: set[str] = set()
    depth = 0
    while len(merged) < cap:
        round_rows = [rows[depth] for rows in ranked_lists if depth < len(rows)]
        if not round_rows:
            break
        if by_score:
            round_rows.sort(key=lambda r: r["score"])
        for row in round_rows:
            # by page_no, not page_uid: a WIS document naming both a model and
            # an engine is filed in both buckets, and a scope holding both
            # must show it once. page_no is unique within a scope: the one
            # source joined into another's scope (a parts catalogue) prefixes
            # its page numbers ("P123"), see importers/records.py.
            key = f"{row['page_no']}#{row['ord']}"
            if key in seen:
                continue
            seen.add(key)
            merged.append(row)
            if len(merged) >= cap:
                return merged
        depth += 1
    return merged


def search(conn: sqlite3.Connection, doc_ids: str | Iterable[str], query: str,
           limit: int = 12, include_other_variants: bool = False) -> list[dict]:
    """Search one vehicle, or a vehicle plus its unscoped companion.

    With several doc_ids each is searched and ranked on its own and the
    results are interleaved, for the same reason `multi_search` merges
    round-robin: the larger pile would otherwise fill the budget. The general
    bucket holds roughly half the documents in a WIS corpus, so ranking the
    two together would routinely bury the pages filed against the model the
    user actually picked.

    Within each round the better BM25 score still wins -- the lists answer the
    same query here, so a loose OR-match on the model must not outrank a tight
    match from the general bucket just for being model-specific.
    """
    ids = as_ids(doc_ids)
    types = getattr(doc_ids, "types", "")
    general = getattr(doc_ids, "general", "")
    engines = getattr(doc_ids, "engines", ())
    codes = getattr(doc_ids, "codes", ((), ()))
    if len(ids) == 1:
        return _search_one(conn, ids[0], query, limit, include_other_variants,
                           engines=engines, codes=codes)
    # only the general pool is type-narrowed; the vehicle and its engines are
    # not. The engine filter applies everywhere.
    return _interleave(
        _search_scope(conn, ids, query, limit, include_other_variants,
                      types, general, engines, codes), limit, by_score=True)


def multi_search(conn: sqlite3.Connection, doc_ids: str | Iterable[str],
                 queries: list[str], per_query: int = 8, cap: int = 18,
                 include_other_variants: bool = False) -> list[dict]:
    """Run several queries and merge them round-robin.

    Merging by global score alone loses whole sub-questions: on "how much oil
    and what's the drain plug torque", the capacity queries score higher, fill
    the budget, and the torque queries drop out entirely. Taking from each
    query in rotation guarantees every sub-question keeps a share of the
    context, while ranking still orders within each query.
    """
    ranked_lists: list[list[dict]] = []
    for q in queries:
        rows = search(conn, doc_ids, q, per_query, include_other_variants)
        ranked_lists.append(sorted(rows, key=lambda r: r["score"]))
    return _interleave(ranked_lists, cap)


def get_page(conn: sqlite3.Connection, doc_ids: str | Iterable[str],
             page_no: str) -> dict | None:
    """Look the page up across the whole scope.

    An answer may cite a page from the unscoped bucket, and that citation has
    to open. Ids are tried in order, so the selected vehicle wins a tie.
    """
    for doc_id in as_ids(doc_ids):
        row = conn.execute(
            "SELECT * FROM pages WHERE doc_id=? AND page_no=?",
            (doc_id, page_no)).fetchone()
        if row:
            return dict(row)
    return None
