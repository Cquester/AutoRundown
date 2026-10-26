"""Typed part and document numbers (numbers.py) -- FREE, <1 s.

    python tests/test_numbers.py

A part number typed in any printed form is one search term; words that only
look like numbers stay words. Search end to end: tests/test_epc_join.py
(part numbers), tests/test_wis_links.py (document numbers).
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo on sys.path)

from autorundown import numbers
from autorundown.search import build_match

fails = 0


def check(name: str, got, want) -> None:
    global fails
    ok = got == want
    print(f"  {'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f" -- got {got!r}, want {want!r}"))
    fails += not ok


def main() -> int:
    full = '("a2018802571" OR "201 880 25 71" OR "2018802571")'
    for typed in ("A 201 880 25 71", "A2018802571", "a 201 880 25 71", "A 201 880 25 71 9040"):
        check(f"{typed!r}: one term, every printed form", build_match(typed), full)
    check("without the A: digits and the spaced form",
          build_match("201 880 25 71"), '("2018802571" OR "201 880 25 71")')
    check("a standard part (N number)", build_match("N 000000 001234"),
          '("n000000001234" OR "000000 001234" OR "000000001234")')
    check("the start of a number, with its A: a prefix",
          build_match("A 201 880"), '("a201880"* OR "201 880"*)')
    check("the start of a number on its own: a prefix",
          build_match("201 880"), '("201880"* OR "201 880"*)')
    check("words around a number stay words",
          build_match("rear bumper A 201 880 25 71"), full + ' AND "rear" AND "bumper"')
    check("OR mode keeps the number whole",
          build_match("bumper A2018802571", "or"), full + ' OR "bumper"')
    # things that only look like numbers
    check("a model name is not a number", build_match("190 E 2.3 16"), '"190" AND "2.3" AND "16"')
    check("two numbers in a sentence are not one", build_match("between 120 and 150"),
          '"between" AND "120" AND "150"')
    check("an engine code with a dot is not a part", build_match("102.983 timing"),
          '"102.983" AND "timing"')
    check("a 6-digit start needs its A unless it is the whole query",
          build_match("120 150 torque"), '"120" AND "150" AND "torque"')
    check("line keys: the compact form a parts line carries",
          numbers.line_keys("A 201 880 25 71 and A 201 880"), ["a2018802571", "a201880"])
    # document numbers
    check("a WIS document number, any case", numbers.documents("see ar42.10-p-0100a"),
          ["AR42.10-P-0100A"])
    check("an old job number", numbers.documents("air cleaner (09-0400)"), ["09-0400"])
    check("a year range is not a job number", numbers.documents("2015-2016 bumper"), [])
    print(f"{fails} failure(s)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
