"""Shop words -> the manuals' words (synonyms.py) -- FREE, <1 s.

    python tests/test_synonyms.py

A phrase may have several manual words: the repair manual (WIS) and the
parts catalogue (EPC) name some parts differently ("anti-roll bar" in WIS,
STABILIZER or TORSION BAR in EPC). translations() gives one query per
alternative; translate() is the first, as before (2026-10-21).
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo on sys.path)
import sys

from autorundown.synonyms import translate, translations

fails = 0


def check(name: str, got, want) -> None:
    global fails
    ok = got == want
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f" -- got {got!r}, want {want!r}"))
    fails += not ok


check("one manual word", translations("valve cover gasket leak"), ["cylinder head cover leak"])
check("translate() unchanged for one word", translate("water pump"), "coolant pump")
check("several manual words, one query each", translations("sway bar bushing"),
      ["anti-roll bar bushing", "stabilizer bushing", "torsion bar bushing"])
check("translate() is the first of them", translate("sway bar bushing"), "anti-roll bar bushing")
check("a one-word phrase beside a several-word one keeps its word",
      translations("coil pack and guibo"),
      ["ignition coil and flexible coupling", "ignition coil and joint disc"])
check("case and plural", translations("Sway Bars"), ["anti-roll bar", "stabilizer", "torsion bar"])
check("word boundaries: 'guibos' is not 'guibo'", translations("guibos"), [])
check("nothing to translate", translations("oil filter"), [])
check("nothing: translate() is None", translate("oil filter"), None)

print(f"\n{'all passed' if not fails else f'{fails} failed'}")
sys.exit(1 if fails else 0)
