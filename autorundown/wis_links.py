"""WIS cross-references, resolved to pages when a page is opened.

WIS pages name other documents by number ("Refer to: AR42.10-P-0100A"). The
extractor's `parse_links.py` writes `wis_links.sqlite` next to the corpus,
whose `docnr` table maps each document number WIS has to the DokID of our
record (= `pages.page_no`).

A printed reference leaves off the variant letters that end a stored number:
"AR15.40-P-5710E" is ...5710ea, ...5710eb, ...5710ecm, one per model, and WIS
picks the one for the car being looked at. So a reference matches every
stored number that is the printed one plus letters only, and the variant
filed under the chosen car's scope comes first. Measured on the English
corpus: 46% of the 1.28M printed references match a stored number exactly,
and most of the rest only this way. Choosing a variant without the car would
open another model's version -- the wrong-vehicle failure -- which is why
this happens here, with the scope, and not at import.

Free, local: no API calls. Without the sidecar every reference stays text.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path

NAME = "wis_links.sqlite"

# the extractor's DOCNR_CORE (parse_sids.py), so both read the same numbers
DOCNR_RE = re.compile(
    r"\b[A-Z]{2}[0-9]{2}\.[0-9]{2}-[A-Z]{1,2}-[0-9]{3,4}[A-Z]?(?:-[0-9]{2}[A-Z]?)?\b")

_lock = threading.Lock()
_open: dict[str, sqlite3.Connection | None] = {}


def norm(docnr: str) -> str:
    """'AR42.10-P-0100A' (printed) and 'ar4210p0100a' (stored) -> the same."""
    return re.sub(r"[^0-9a-z]", "", docnr.lower())


def _db(corpus: Path) -> sqlite3.Connection | None:
    """The corpus's sidecar, opened read-only once per process; None if absent."""
    path = Path(corpus) / NAME
    key = str(path)
    with _lock:
        if key not in _open or _open[key] is None:
            if not path.is_file():
                return None
            try:
                _open[key] = sqlite3.connect(f"file:{path}?mode=ro", uri=True,
                                             check_same_thread=False)
            except sqlite3.Error:
                return None
        return _open[key]


def candidates(corpus: Path, printed: str) -> list[int]:
    """DokIDs a printed reference can mean: the same number, plus only
    variant letters. A number printed without its letter ("...-P-0100")
    must not match the sub-document "...-P-0100-01A"."""
    db = _db(corpus)
    if db is None:
        return []
    key = norm(printed)
    with _lock:
        rows = db.execute(
            "SELECT docnr, dok_id FROM docnr WHERE docnr >= ? AND docnr < ?",
            (key, key + "{")).fetchall()     # '{' sorts after 'z'
    return sorted({dok for docnr, dok in rows if docnr[len(key):].isalpha()
                   or docnr == key})


def resolve(conn: sqlite3.Connection, corpus: Path, text: str,
            scope: list[str], wis_ids: list[str], own_page: str = "") -> list[dict]:
    """The references in `text` that open a page, in order of first mention.

    Each: {"docnr": as printed, "pages": [{page_no, title, applies, doc_id,
    in_scope}]}, pages in the chosen car's scope first (in scope order), then
    the same document filed under other models. `wis_ids` are the doc_ids of
    this corpus's vehicles; a page is looked up in those only.
    """
    if _db(corpus) is None or not wis_ids:
        return []
    out, seen = [], set()
    rank = {d: i for i, d in enumerate(scope)}
    marks = ",".join("?" * len(wis_ids))
    for m in DOCNR_RE.finditer(text or ""):
        printed = m.group(0)
        if printed in seen:
            continue
        seen.add(printed)
        doks = [str(d) for d in candidates(corpus, printed) if str(d) != own_page]
        if not doks:
            continue
        rows = conn.execute(
            f"SELECT doc_id, page_no, title, section_path FROM pages WHERE page_no IN "
            f"({','.join('?' * len(doks))}) AND doc_id IN ({marks})",
            [*doks, *wis_ids]).fetchall()
        best: dict[str, tuple] = {}
        for doc_id, page_no, title, section in rows:
            r = rank.get(doc_id, len(rank))
            if page_no not in best or r < best[page_no][0]:
                best[page_no] = (r, doc_id, title, section)
        # `applies`: the page's applicability line, which is what tells two
        # variants of one document apart ("MODEL 211.0xx" / "211.2xx")
        pages = [{"page_no": p, "title": t, "applies": sec, "doc_id": d,
                  "in_scope": r < len(rank)}
                 for p, (r, d, t, sec) in sorted(best.items(), key=lambda kv: (kv[1][0], kv[0]))]
        if pages:
            out.append({"docnr": printed, "pages": pages})
    return out
