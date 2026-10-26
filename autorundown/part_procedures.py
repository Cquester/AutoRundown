"""The repair procedures for a part: the manual pages a parts subgroup's
name finds, in the car's manual scope.

Built 2026-10-26 at the user's request ("if i'm looking at a part, is it
possible for me to see all repair procedures for that?"). Nothing in either
source links a part to a procedure: measured on the 190 E 2.3-16, 7 of its
9,864 part numbers are printed anywhere in its WIS pages, so the part number
can't do it. What both sources share is the construction group (EPC group
88 ATTACHMENT PARTS = WIS 88 "Attachment parts" -- the old manuals' job
numbers too: ra88002010220z is job 88-0220) and the words of the name. Group
plus a shared title word alone found something for 20-37% of subgroups (and
missed REAR BUMPER, a scanned page with no text); a search of the manual for
the subgroup's name, kept only where a page *title* shares a word with it
and ranked same group first, real jobs (remove / install / replace) before
tests and descriptions, finds something for ~97% of the 2.3-16's subgroups.

It is a keyword match, so the UI says so ("matched by name") -- unlike the
manual's own cross-references, it is not the manual's link. Free: no AI.
"""
from __future__ import annotations

import re
import sqlite3

from . import manual_browse, search, synonyms

MAX = 6
# words of a parts subgroup's name that say nothing about the part
_STOP = set("""with without and for the of from to front rear left right upper lower
inner outer parts part kit set unit assembly mounting used attachment
attachments additional various individual covering complete""".split())
# words that match too much on their own: a title must share another word
_GENERIC = {"engine", "model", "vehicle", "system", "control", "electrical",
            "equipment", "cable", "line", "lines"}
_JOB = re.compile(r"remov|install|replac|renew|disassembl|dismantl|overhaul|adjust|bleed", re.I)
# a special tool's page is titled by its number ("116 589 02 59 00 ...")
_TOOL = re.compile(r"^\s*\d{3} \d{3} \d{2} \d{2}")


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 4 and w.endswith("s") else w


def _words(s: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z]{3,}", s.lower()) if w not in _STOP}


def _title(t: str) -> str:
    # WIS's old titles pad the applicability on after spaces:
    # "0310 - Removal and installation of the oil sump      Engine 102 KE ..."
    return re.split(r"\s{3,}", (t or "").strip())[0]


def _queries(name: str) -> list[str]:
    """One search per part of the name ("OIL PAN & OIL LEVEL DISPLAY" is two
    things), each also in the manuals' wording (synonyms.py)."""
    parts = [p.strip() for p in re.split(r"[&,/;]| AND | USED ", name, flags=re.I) if p.strip()]
    out = []
    for p in [name] + parts if len(parts) > 1 else [name]:
        words = " ".join(w for w in re.findall(r"[A-Za-z]{3,}", p) if w.lower() not in _STOP)
        if words:
            out += synonyms.translations(words.lower()) + [words.lower()]
    return list(dict.fromkeys(out))


def _group_pages(tree: dict, group: str) -> list[dict]:
    """Every page the car's manual contents (manual_browse.tree, cached per
    car) file under construction group `group` -- WIS numbers both new
    ("ar8810...") and old ("ra88002010220z") documents by group."""
    out: list[dict] = []

    def walk(n: dict) -> None:
        out.extend(n["pages"])
        for c in n["children"].values():
            walk(c)
    for name, n in tree.get("children", {}).items():
        if name == group or name.startswith(group + " "):
            walk(n)
    return out


def procedures(conn: sqlite3.Connection, vehicle: dict, manual_scope, wis: bool,
               group: str, name: str, limit: int = MAX) -> list[dict]:
    """[{page_no, title, same_group (None: no groups), job}] for a parts subgroup (`group`,
    `name`) in the car's manual scope, best first: the part's own group's
    pages that share a word with its name, then pages elsewhere sharing two
    ("PANE WASHER" is not every page about a washer) -- one, in a manual
    that files nothing by group."""
    want = _words(name)
    specific = (want - _GENERIC) or want
    if not specific or not search.as_ids(manual_scope):
        return []
    found: dict[str, tuple[str, bool, int]] = {}      # page_no -> (title, same group, rank)
    grouped = False       # does this manual file its pages by group at all?
    if wis:
        tree = manual_browse.tree(conn, vehicle, manual_scope, True)
        grouped = any(n[:2].isdigit() for n in tree.get("children", {}))
        for i, pg in enumerate(_group_pages(tree, group)):
            found.setdefault(str(pg["page_no"]), (pg["title"], True, i))
    for q in _queries(name):
        for rank, h in enumerate(search.search(conn, manual_scope, q, 25)):
            if not h.get("parts") and h["page_no"] not in found:
                found[h["page_no"]] = (h.get("title") or "", False, rank)
    out = []
    for page_no, (title, same, rank) in found.items():
        title = _title(title)
        shared = _words(title) & want
        # without groups (a manual with no document numbers) the name is all there is
        need = 1 if same or not grouped else min(2, len(specific))
        if len(shared & specific) < need or _TOOL.match(title):
            continue
        job = bool(_JOB.search(title))
        # then every word of the name, "rear" too: REAR BUMPER's own job
        # before the front bumper's
        every = len(set(re.findall(r"[a-z]{3,}", title.lower()))
                    & set(re.findall(r"[a-z]{3,}", name.lower())))
        out.append(((not same, not job, -len(shared), -every, rank),
                    {"page_no": page_no, "title": title, "job": job,
                     # None: this manual files nothing by group, so nothing to say
                     "same_group": same if grouped else None}))
    out.sort(key=lambda x: x[0])
    seen, rows = set(), []
    for _, r in out:               # one row per title: a document filed twice
        if r["title"].lower() in seen:
            continue
        seen.add(r["title"].lower())
        rows.append(r)
    return rows[:limit]
