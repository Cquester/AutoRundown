"""RTF stubs from WIS's SIDS bodies (extractor/parse_sids.py) -- FREE, <1 s.

    python tests/test_parse_sids.py

A figure-only RTF stub names a graphic (&P&/&V&) and may list hotspots
(&H&: a box on the graphic and a damage-code component). The hotspot's first
fields used to be read as a graphic, leaving ".53|0.46;54001" in 17,995
records' text and a bogus image reference (measured on the real corpus,
2026-10-29: 99016741 -- the stubs below are its real text).
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo and tools/ on sys.path)
import sys

sys.path.insert(0, str(ROOT / "extractor"))
import parse_sids  # noqa: E402

fails = 0


def check(name: str, ok: bool, detail=None) -> None:
    global fails
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + ("" if ok else f" -- {detail!r}"))
    fails += not ok


STUB = "﻿{\\rtf1\\ansi argt0\\margb0\n\\v &P& 03624799|20.10|12.70|TIFF;;\n\\v &H& 0|1.07|0.86|1.53|0.46;54001;\n}"
_, body, xrefs, applic, refs = parse_sids.parse_rtf_body(STUB)
check("the graphic is the one image reference", refs == ["03624799 (TIFF 20.10x12.70)"], refs)
check("the hotspot is neither text nor a graphic", "|" not in body and "54001" not in body, body)
check("nothing readable left: an empty body", body == "", body)

MANY = ("{\\rtf1\\ansi argt0\n\\v &P& 03624799|20.10|12.70|TIFF;;\n"
        + "".join(f"\\v &H& 0|1.07|0.86|1.53|0.46;{c};\n" for c in ("00121", "00122", "0040D")) + "}")
_, body, _, _, refs = parse_sids.parse_rtf_body(MANY)
check("several hotspots: still one image, no text", len(refs) == 1 and body == "", (refs, body))

TEXT = "{\\rtf1\\ansi \\b Tightening torque\\b0 for the screw 12 Nm\n\\v &V& pe2819d200099c|70.72|11.80|DXF;;\n}"
_, body, _, _, refs = parse_sids.parse_rtf_body(TEXT)
check("a stub's own words stay", "Tightening torque" in body and "12 Nm" in body, body)
check("a DXF graphic is still found", refs == ["pe2819d200099c (DXF 70.72x11.80)"], refs)

print(f"\n{'all passed' if not fails else f'{fails} failed'}")
sys.exit(1 if fails else 0)
