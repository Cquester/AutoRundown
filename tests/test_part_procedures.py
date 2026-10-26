"""Repair procedures for a parts subgroup (part_procedures.py) -- FREE, <1 s.

    python tests/test_part_procedures.py

The ranking rules, on the cases measured on the real 190 E 2.3-16 index
(2026-10-26), with the car's manual contents and search stubbed:

- the part's own construction group comes first, real jobs (remove /
  install / replace) before tests and descriptions ("FRONT WHEEL BRAKE":
  the pads job, not the ABS test program);
- in its group one word of the name is enough (the pads job never says
  "wheel"); elsewhere two ("PANE WASHER" is not a gear's thrust washer);
- a name of several things is searched part by part, and "oil pan" also as
  the old manuals' "oil sump";
- special tools' pages and a document filed twice are left out.
The endpoint end to end: tests/test_parts_diagrams.py.
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo on sys.path)

from autorundown import manual_browse, part_procedures as pp, search

fails = 0


def check(name: str, got, want) -> None:
    global fails
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f" -- got {got!r}, want {want!r}"))
    fails += not ok


def node(pages=(), children=None):
    return {"children": children or {}, "pages": [{"page_no": p, "title": t, "kind": ""} for p, t in pages],
            "count": 0}


TREE = node(children={
    "42 Brakes": node(children={"42.10": node([
        ("1", "0720 - Electrical test program - ABS equipped brake system"),
        ("2", "0160 - Checking and replacement of the brake pads   Model 201"),
        ("3", "0005 - Function of the parking brake")])}),
    "88 Attachment parts": node([
        ("9", "0200 - Removal and installation of front bumper    Model 201"),
        ("10", "0220 - Removal and installation of rear bumper     Model 201"),
        ("11", "0300 - Removal and installation of engine hood"),
        ("12", "116 589 02 59 00 Bumper holder (special tool)")]),
    "01 Engine": node([("20", "0310 - Removal and installation of the oil sump")]),
})
SEARCH = {   # query -> hits (page_no, title) from anywhere in the manual
    "wheel brake": [("30", "Clean/check/corrosion protect wheel bolting components")],
    "bumper": [("10", "0220 - Removal and installation of rear bumper"),
                    ("31", "Service Information: Painting bumpers with short range radar"),
                    ("32", "Painting rear bumpers")],
    "pane washer": [("33", "Removing and installing differential side gears with friction washers"),
                    ("34", "Removal and installation of pane washer pump")],
    "oil sump": [("20", "0310 - Removal and installation of the oil sump")],
    "oil pan": [("35", "Remove/install automatic transmission oil pan")],
}
asked: list[str] = []


def fake_search(conn, scope, q, limit, **kw):
    asked.append(q)
    return [{"page_no": p, "title": t} for p, t in SEARCH.get(q, [])]


def main() -> int:
    manual_browse.tree, search.search = (lambda *a, **k: TREE), fake_search

    def run(group, name):
        return [(r["page_no"], r["same_group"], r["job"])
                for r in pp.procedures(None, {"doc_id": "x"}, ["x"], True, group, name)]

    check("own group first, a job before a test; one word is enough in the group",
          run("42", "FRONT WHEEL BRAKE")[:2], [("2", True, True), ("1", True, False)])
    check("outside the group a page needs two of the name's words",
          "30" in [p for p, *_ in run("42", "FRONT WHEEL BRAKE")], False)
    got = run("88", "REAR BUMPER")
    check("the rear bumper's job first, then the front's, other bumper pages after, the hood never",
          [p for p, *_ in got], ["10", "9", "32", "31"])
    check("a special tool's page is left out", "12" in [p for p, *_ in got], False)
    got = run("82", "PANE WASHER,HEADLAMP WIPER/WASHER")
    check("a washer that is not the pane washer is left out", [p for p, *_ in got], ["34"])
    check("a name of several things is searched part by part",
          {"pane washer", "headlamp wiper", "washer"} <= set(asked), True)
    asked.clear()
    got = run("01", "OIL PAN & OIL LEVEL DISPLAY")
    check("'oil pan' is also searched as the old manuals' 'oil sump'", "oil sump" in asked, True)
    check("the oil sump job (own group) first", got[0], ("20", True, True))
    check("a name of nothing but filler words finds nothing",
          pp.procedures(None, {"doc_id": "x"}, ["x"], True, "88", "PARTS, ASSEMBLY"), [])
    check("one row per title (a document filed twice)",
          len({pp._title(t).lower() for p, t in [("10", "a"), ("10", "a")]}), 1)
    print(f"{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
