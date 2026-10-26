"""Numbers a mechanic types into search: part numbers and manual document
numbers.

Built 2026-10-26 at the user's request ("searching by manual numbers").
Measured on the real index before it: `A2018802571` found the 190 E 2.3-16
rear bumper, but `A 201 880 25 71` -- the way the catalogue and the manuals
print it -- was searched as the words 201, 880, 25 and 71 and returned an
oil sump and a fuel tank page; WIS document numbers (`AR20.10-P-1271A`) were
found at rank 0-5 behind the pages that mention them, and the W201's old
job numbers (`88-0220`) not at all.

Part numbers. EPC prints each number three ways on its line, "A 201 880 25
71 (A2018802571, 2018802571)", and WIS prints the spaced form in its text
(15,520 WIS pages). A typed number in any of those forms becomes one FTS
term matching all three (`fts_terms`); a leading part ("A 201 880") matches
every number starting with it. Without the "A" a part needs all ten digits
(or the query to be nothing but the number): "190 E 2.3 16" or "between 120
and 150" must stay words.

Document numbers are looked up, not searched (wis_links.number_pages).
"""
from __future__ import annotations

import re

# A 201 880 25 71 / A2018802571 / 201 880 25 71 (+ a 4-digit colour code,
# dropped: EPC lists the colours in a footnote, not in the number)
_FULL = re.compile(r"(?<![\w.-])(?:([AN])\s?)?(\d{3})\s?(\d{3})\s?(\d{2})\s?(\d{2})(?:\s?\d{4})?(?![\w.-])", re.I)
# N 000000 001234 (standard parts: nuts, screws, clips)
_NORM = re.compile(r"(?<![\w.-])N\s?(\d{6})\s?(\d{6})(?![\w.-])", re.I)
# the start of a number: A 201 880 / A 201 880 25
_PART = re.compile(r"(?<![\w.-])A\s?(\d{3})\s?(\d{3})(?:\s?(\d{2}))?(?![\w.-])", re.I)
_BARE = re.compile(r"^\s*(\d{3})\s?(\d{3})(?:\s?(\d{2}))?\s*$")

# printed WIS document numbers: the modern form (wis_links.DOCNR_RE) and the
# old manuals' job numbers, "88-0220" = group 88, job 0220
DOCNR_RE = re.compile(
    r"\b[A-Z]{2}[0-9]{2}\.[0-9]{2}-[A-Z]{1,2}-[0-9]{3,4}[A-Z]?(?:-[0-9]{2}[A-Z]?)?\b", re.I)
OLD_JOB_RE = re.compile(r"(?<![\w.-])(\d{2})-(\d{4})(?![\w.-])")


def _q(s: str) -> str:
    return '"' + s.replace('"', "") + '"'


def parts(query: str) -> tuple[list[dict], str]:
    """The part numbers in `query` and the query without them:
    ([{"key": compact form, lowercase, "spaced": "201 880 25 71",
       "prefix": bool}], rest)."""
    found: list[dict] = []

    def full(m):
        letter, groups = (m[1] or "").lower(), m.groups()[1:]
        digits = "".join(groups)
        found.append({"key": letter + digits, "spaced": " ".join(groups), "prefix": False})
        return " "

    def norm(m):
        found.append({"key": "n" + m[1] + m[2], "spaced": f"{m[1]} {m[2]}", "prefix": False})
        return " "

    def part(m):
        groups = [g for g in m.groups() if g]
        found.append({"key": "a" + "".join(groups), "spaced": " ".join(groups), "prefix": True})
        return " "

    rest = _NORM.sub(norm, query)
    rest = _FULL.sub(full, rest)
    rest = _PART.sub(part, rest)
    m = _BARE.match(rest) if not found else None
    if m:                          # the whole query is the start of a number
        groups = [g for g in m.groups() if g]
        found.append({"key": "".join(groups), "spaced": " ".join(groups), "prefix": True})
        rest = ""
    return found, rest


def fts_terms(query: str) -> tuple[list[str], str]:
    """FTS5 terms for the part numbers in `query` (each matching the number in
    every printed form), and the rest of the query."""
    found, rest = parts(query)
    terms = []
    for p in found:
        star = "*" if p["prefix"] else ""
        key = p["key"]
        alts = [_q(key) + star, _q(p["spaced"]) + star]
        if key[0].isalpha() and not p["prefix"]:
            alts.append(_q(key[1:]))            # EPC's bare-digits form
        terms.append("(" + " OR ".join(alts) + ")")
    return terms, rest


def line_keys(query: str) -> list[str]:
    """What a parts line must contain (lowercased) to name a typed number:
    its compact form, found in "(A2018802571, 2018802571)"."""
    return [p["key"] for p in parts(query)[0]]


def documents(query: str) -> list[str]:
    """WIS document numbers in `query`, as typed (upper-cased), then old job
    numbers ("88-0220")."""
    return list(dict.fromkeys([m.group(0).upper() for m in DOCNR_RE.finditer(query)]
                              + [m.group(0) for m in OLD_JOB_RE.finditer(query)]))
