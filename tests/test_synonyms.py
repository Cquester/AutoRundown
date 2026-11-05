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
check("US spelling -> the catalogue's British one", translations("front window molding clip"),
      ["front window moulding clip"])
check("... plural, and other spellings", translations("rocker panel moldings"), ["sill mouldings"])
check("drain plug: WIS's drain screw", translations("oil drain plug torque"), ["oil drain screw torque"])
check("... sump plug and a bare drain plug", (translate("sump plug"), translate("drain plug")),
      ("oil drain screw", "drain screw"))
check("taillight: WIS's tail lamp and taillamp, the F-150's rear lamp", translations("taillight"),
      ["tail lamp", "taillamp", "rear lamp"])

# merge_wordings: titles holding the whole phrase first (agent review, 2026-10-28)
from autorundown.search import merge_wordings  # noqa: E402


def rows(*titles):
    return [{"page_no": t, "title": t} for t in titles]


got = merge_wordings("oil pan", ["oil sump", "oil pan"],
                     [rows("Sump volume modified", "EGR valve"), rows("Notes", "Remove/install oil pan")])
check("oil pan: the typed words' own title before the synonym's loose hits",
      [r["title"] for r in got][:2], ["Remove/install oil pan", "Sump volume modified"])
got = merge_wordings("taillight", ["tail lamp", "rear lamp", "taillight"],
                     [rows("Care of tail pipe finishers", "Removing and installing tail lamp on tailgate"),
                      rows("Rear dome lamp", "Rear Lamp Assembly: Removal"), rows()])
check("taillight: each wording's titled hits first, taking turns",
      [r["title"] for r in got][:3],
      ["Removing and installing tail lamp on tailgate", "Rear dome lamp", "Rear Lamp Assembly: Removal"])
check("one wording only: as before", [r["title"] for r in merge_wordings("x", ["x"], [rows("a", "b")])],
      ["a", "b"])

check("tailgate: the labour books' rear-end door and liftgate", translations("tailgate hinge"),
      ["rear-end door hinge", "liftgate hinge"])

# labour times take every wording: one book says "rear-end door", another
# "liftgate" (2026-11-04: a wagon's "tailgate" found no labour time)
from autorundown.wis_links import _rank_labour  # noqa: E402

book = [{"kg": "72", "op": "72-2667", "text": "Hinges on rear-end door, Location:..... replace (with door removed)"},
        {"kg": "72", "op": "72-9001", "text": "Hinges (2) on liftgate replace"},
        {"kg": "88", "op": "88-8007", "text": "Trunk lid replace (Rear cover removed)"}]
q = "tailgate hinges"
got = _rank_labour(book, [w.split() for w in [q, *translations(q)]], 200)
check("labour: 'tailgate hinges' finds both books' wordings, not the trunk lid",
      sorted(i["op"] for i in got["items"]), ["72-2667", "72-9001"])

check("nothing to translate", translations("oil filter"), [])
check("nothing: translate() is None", translate("oil filter"), None)

print(f"\n{'all passed' if not fails else f'{fails} failed'}")
sys.exit(1 if fails else 0)
