"""Shop words -> the words the manuals use.

Keyword search is exact (BM25 over tokens), and Mercedes WIS writes
"cylinder head cover", never "valve cover" -- a W212 search for "valve cover"
matched only loosely on "valve" and "cover" anywhere. The Ask path doesn't need
this (its planner rewrites the question into manual terms); keyword search
does. `translate()` returns the query with each known phrase swapped, or None;
the caller searches both and puts the manual's wording first.

Phrases only, longest first, matched on word boundaries. Add a row when a
search misses because of wording -- keep it to unambiguous pairs.
"""
from __future__ import annotations

import re

PHRASES: dict[str, str] = {
    "valve cover gasket": "cylinder head cover",
    "valve cover": "cylinder head cover",
    "rocker cover": "cylinder head cover",
    "serpentine belt": "poly-V-belt",
    "drive belt": "poly-V-belt",
    "lug nuts": "wheel bolts",
    "lug nut": "wheel bolt",
    "lug bolts": "wheel bolts",
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
}
_RE = re.compile(r"\b(" + "|".join(re.escape(k) for k in
                                    sorted(PHRASES, key=len, reverse=True)) + r")\b", re.I)


def translate(query: str) -> str | None:
    """The query in the manuals' wording, or None if nothing applies."""
    out = _RE.sub(lambda m: PHRASES[m.group(1).lower()], query)
    return out if out.lower() != query.lower() else None
