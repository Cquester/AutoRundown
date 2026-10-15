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


FTS_TABLES = ("chunks_fts", "parts_fts")   # see index.SCHEMA: parts in their own


def _run(conn: sqlite3.Connection, doc_id: str, match: str, limit: int,
         include_other_variants: bool, types: str = "",
         engines: tuple[str, ...] = (), codes: tuple = ((), ()),
         fts: str = "chunks_fts") -> list[dict]:
    if not match:
        return []
    assert fts in FTS_TABLES
    where, args, boost_sql, boost_args = _filters(include_other_variants, engines, codes)
    tclause, targs = _types_clause(types)
    if tclause:
        where += " AND " + tclause          # appended last, so its args go last
        args = args + targs
    sql = f"""
        SELECT c.page_uid, c.doc_id, c.ord, c.title, c.section_path, c.body,
               p.page_no, p.system, p.doc_type, p.images, p.applicability,
               bm25({fts}, ?, ?, ?){boost_sql} AS score
        FROM {fts}
        JOIN chunks c ON c.id = {fts}.rowid
        JOIN pages  p ON p.page_uid = c.page_uid
        WHERE {fts} MATCH ? AND c.doc_id = ?{where}
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
               engines: tuple[str, ...] = (), codes: tuple = ((), ()),
               fts: str = "chunks_fts") -> dict[str, list[dict]]:
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
    assert fts in FTS_TABLES
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
                       bm25({fts}, ?, ?, ?){boost_sql} AS score
                FROM {fts}
                JOIN chunks c ON c.id = {fts}.rowid
                JOIN pages  p ON p.page_uid = c.page_uid
                WHERE {fts} MATCH ? AND c.doc_id IN ({marks}){where}
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
                  engines: tuple[str, ...] = (), codes: tuple = ((), ()),
                  fts: str = "chunks_fts") -> list[list[dict]]:
    """`_search_one` for every doc_id of a scope, in two statements: AND
    first, then OR only for the doc_ids that came up short. Returns the lists
    in `doc_ids` order, exactly as one `_search_one` per doc_id would."""
    lists = _run_scope(conn, doc_ids, build_match(query, "and"), limit,
                       include_other_variants, types, general, engines, codes, fts)
    short = [d for d in doc_ids if len(lists.get(d, [])) < limit]
    if short:
        more = _run_scope(conn, short, build_match(query, "or"), limit * 2,
                          include_other_variants, types, general, engines, codes, fts)
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
    parts: tuple = ()       # joined parts catalogue doc_ids (EPC); searched apart


# Joined parts catalogues are searched as their own group and their results
# appended after the manual's, so the manual's top N is exactly what it is
# without them. Interleaved, the extra buckets cost manual pages a rank or two
# (eval_wis on WIS + EPC: W124 brake pads 5 -> 7, W211 manual trans 6 -> 7).
def parts_slots(limit: int) -> int:
    return max(2, limit // 4)


PARTS_CAP = 4   # parts passages added to an answer's context (Ask)
_CATALOG_RE = re.compile(r"\(EPC catalog [^)]*\)")
_TABLE_RE = re.compile(r"^(?:Callout|Item) \| Part number \|")
# words on every parts line, so they select nothing
_PARTS_STOP = {"part", "parts", "number", "numbers", "no", "pn", "replacement",
               "replace", "order", "ordering", "catalogue", "catalog", "epc"}
SNIPPET_MAX = 4000


def _parts_snippet(conn: sqlite3.Connection, row: dict, query: str) -> str:
    """A parts page as an answer should see it: the header, then every line
    of the page that names what was asked, with its footnotes.

    A parts page is indexed in ~2,400-character chunks and BM25 picks the
    chunk that says the words most often, which on a parts page is not the
    part asked for: "glow plug" on the OM651 page ranked the wiring-harness
    chunk ("GLOW PLUGS WIRING HARNESS" x8) over the one holding the four glow
    plug lines. Lines are self-contained (number, name, fits, footnotes), so
    the matching lines are the useful passage. Same page, same page_no: the
    citation is unchanged. Falls back to the chunk when no line matches.
    """
    page = conn.execute("SELECT text FROM pages WHERE page_uid=?", (row["page_uid"],)).fetchone()
    if not page:
        return row["body"]
    lines = (page[0] or "").split("\n")
    start = next((i for i, l in enumerate(lines) if _TABLE_RE.match(l)), None)
    if start is None:
        return row["body"]
    head = [l for l in lines[:start + 1]
            if l.strip() and not l.startswith(("Applies to:", "Noted in the manual:"))]
    end = next((i for i, l in enumerate(lines) if l == "Footnotes:"), len(lines))
    table, notes = lines[start + 1:end], lines[end + 1:]
    terms = [t[:-1] if len(t) > 3 and t.endswith("s") else t
             for t in tokenize(query) if t not in _PARTS_STOP]
    if not terms:
        return row["body"]
    low = [l.lower() for l in table]
    hit = [table[i] for i, l in enumerate(low) if all(t in l for t in terms)] or \
          [table[i] for i, l in enumerate(low) if any(t in l for t in terms)]
    if not hit:
        return row["body"]
    # long footnotes listed at the end of the page, for the lines kept
    cited = {m for l in hit for m in re.findall(r"fn (\d{3})", l)}
    notes = [n for n in notes if re.match(r"fn (\d{3}):", n) and n[3:6] in cited]
    out, size = head + [f"(lines of this page matching: {', '.join(terms)})"], 0
    for l in hit + (["Footnotes:"] + notes if notes else []):
        size += len(l) + 1
        if size > SNIPPET_MAX:
            out.append("... (more matching lines on the page)")
            break
        out.append(l)
    return "\n".join(out)


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
    # They stay in the list (a cited parts page must open through the car) and
    # are named in `scope.parts`, which `search` ranks as a separate group.
    # a parts vehicle chosen on its own (an EPC series with no WIS car)
    parts: list[str] = [vehicle["doc_id"]] if vehicle.get("doc_type") == "parts" else []
    for pid in vehicle.get("joined_ids") or []:
        if pid not in scope:
            scope.append(pid)
            parts.append(pid)
    joins = vehicle.get("engine_joins") or {}
    for fam in sorted({str(e)[:3] for v in chosen for e in v.get("engines", [])}):
        if fams and fam not in fams:
            continue
        for pid in joins.get(fam, []):
            if pid not in scope:
                scope.append(pid)
                parts.append(pid)
    scope.parts = tuple(parts)
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
    parts = [i for i in getattr(doc_ids, "parts", ()) if i in ids]
    if parts:
        # the manual exactly as without a parts catalogue, then the parts
        # (see parts_slots), each row marked so callers can keep them apart
        manual = Scope(i for i in ids if i not in parts)
        manual.types, manual.general = types, general     # same narrowing as before
        manual.engines, manual.codes = engines, codes
        head = search(conn, manual, query, limit, include_other_variants) if manual else []
        # A series has one EPC catalog per market and period (the W124: 4,178
        # parts pages, 582 distinct titles), so the same subgroup comes back
        # several times; one per title, over-fetched to fill the slots.
        k = parts_slots(limit) if manual else limit   # a parts car on its own: all of it
        tail, titles = [], set()
        # Best score first across the parts buckets, not round-robin: they
        # answer the same query in one FTS table, so scores compare, and a
        # whole-series scope has ~10 engine buckets -- one slot each per round
        # gave the W203's second slot to an engine bucket's weak OR match (a
        # camshaft bolt for "brake pad") over its own FRONT WHEEL BRAKE page.
        ranked = sorted((r for rows in _search_scope(
                            conn, parts, query, k * 12, include_other_variants,
                            "", "", engines, codes, fts="parts_fts") for r in rows),
                        key=lambda r: r["score"])
        for r in ranked:
            key = _CATALOG_RE.sub("", r.get("title") or "")
            if key in titles:
                continue
            titles.add(key)
            r["parts"] = True
            r["body"] = _parts_snippet(conn, r, query)
            # the body is now the page's lines, not chunk `ord`: one passage per
            # page (several Ask queries found the same page under three chunks)
            r["ord"] = 0
            tail.append(r)
            if len(tail) == k:
                break
        return head + tail
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
    parts_lists: list[list[dict]] = []
    for q in queries:
        rows = search(conn, doc_ids, q, per_query, include_other_variants)
        ranked_lists.append(sorted((r for r in rows if not r.get("parts")),
                                   key=lambda r: r["score"]))
        parts_lists.append([r for r in rows if r.get("parts")])
    # a joined parts catalogue gets its own small share after the manual's
    return _interleave(ranked_lists, cap) + _interleave(parts_lists, PARTS_CAP)


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
