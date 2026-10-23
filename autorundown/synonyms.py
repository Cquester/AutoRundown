"""Shop words -> the words the manuals use.

Keyword search is exact (BM25 over tokens), and Mercedes WIS writes
"cylinder head cover", never "valve cover" -- a W212 search for "valve cover"
matched only loosely on "valve" and "cover" anywhere. The Ask path doesn't need
this (its planner rewrites the question into manual terms); keyword search
does. `translate()` returns the query with each known phrase swapped, or None;
the caller searches both and puts the manual's wording first.

A phrase may have several manual words, as a tuple: the repair manual (WIS)
and the parts catalogue (EPC) name some parts differently -- WIS says
"anti-roll bar", EPC "STABILIZER" or "TORSION BAR". `translations()` gives
the query once per alternative; `translate()` stays the first.

Phrases only, longest first, matched on word boundaries. Add a row when a
search misses because of wording -- keep it to unambiguous pairs.
"""
from __future__ import annotations

import re

PHRASES: dict[str, str | tuple[str, ...]] = {
    "valve cover gasket": "cylinder head cover",
    "valve cover": "cylinder head cover",
    "rocker cover": "cylinder head cover",
    "serpentine belt": "poly-V-belt",
    "drive belt": "poly-V-belt",
    "lug nuts": "wheel bolts",
    "lug nut": "wheel bolt",
    "lug bolts": "wheel bolts",
    # WIS's labour texts and procedures say "coolant pump" / "brake linings"
    # (checked 2026-10-11 on the W211 E 350 labour book)
    "water pump": "coolant pump",
    "brake pads": "brake linings",
    "brake pad": "brake lining",
    "brake rotors": "brake disks",
    "brake rotor": "brake disk",
    "tie rod end": "track rod end",
    "tie rod": "track rod",
    "motor mount": "engine mount",
    "o2 sensor": "oxygen sensor",
    "headlight": "headlamp",
    "tail light": "tail lamp",
    "taillight": "tail lamp",
    "muffler": "silencer",
    "gas tank": "fuel tank",
    "gas cap": "fuel filler cap",
    "antifreeze": "coolant",
    "trans fluid": "ATF",
    "transmission fluid": "ATF",
    "throttle body": "throttle valve actuator",
    # shop slang for the M272/M276 camshaft adjuster solenoids; WIS: "Remove/
    # install solenoid camshaft", "camshaft solenoid (Y49/4)" (2026-10-16,
    # a 2014 E 350 search for "cam magnets" found nothing)
    "cam magnets": "camshaft solenoid",
    "cam magnet": "camshaft solenoid",
    "camshaft magnets": "camshaft solenoid",
    "camshaft magnet": "camshaft solenoid",
    "cam solenoids": "camshaft solenoid",
    "cam solenoid": "camshaft solenoid",
    # the parts catalogue's words beside WIS's (counted in both extracts,
    # 2026-10-21: "sway bar", "coil pack", "flex disc", "guibo" and
    # "harmonic balancer" are in neither)
    "sway bars": ("anti-roll bar", "stabilizer", "torsion bar"),
    "sway bar": ("anti-roll bar", "stabilizer", "torsion bar"),
    "coil packs": "ignition coil",
    "coil pack": "ignition coil",
    "flex discs": ("flexible coupling", "joint disc"),
    "flex disc": ("flexible coupling", "joint disc"),
    "flex disk": ("flexible coupling", "joint disc"),
    "guibo": ("flexible coupling", "joint disc"),
    "harmonic balancer": "vibration damper",
    # spellings: EPC writes "MOULDING" 34k times to WIS's "molding" (a W201
    # "front window molding clip" missed CLIP, GARNISH MOULDING BELOW
    # WINDSHIELD, 2026-10-23); the typed word is still searched for WIS
    "moldings": "mouldings",
    "molding": "moulding",
    "rocker panels": "sills",
    "rocker panel": "sill",
    "weatherstrips": "sealing strips",
    "weatherstrip": "sealing strip",
    "tailpipe": "tail pipe",
}
_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k in
                                    sorted(PHRASES, key=len, reverse=True)) + r")\b", re.I)


def _alts(phrase: str) -> tuple[str, ...]:
    v = PHRASES[phrase.lower()]
    return v if isinstance(v, tuple) else (v,)


def translations(query: str) -> list[str]:
    """The query in each of the manuals' wordings (the first is translate()'s),
    or [] if nothing applies."""
    found = [_alts(m.group(1)) for m in _RE.finditer(query)]
    out: list[str] = []
    for i in range(max((len(a) for a in found), default=0)):
        t = _RE.sub(lambda m: (a := _alts(m.group(1)))[min(i, len(a) - 1)], query)
        if t.lower() != query.lower() and t not in out:
            out.append(t)
    return out


def translate(query: str) -> str | None:
    """The query in the manuals' wording, or None if nothing applies."""
    out = translations(query)
    return out[0] if out else None
