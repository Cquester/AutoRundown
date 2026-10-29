"""Citation verification, the core safety property -- FREE, no API calls, <1 s.

    python tests/test_citations.py

query._verify_citations() must drop every page and figure the retrieval did
not return, and withhold an answer left with no surviving citation (CLAUDE.md:
"a confident wrong torque is worse than no answer"). The model's output is
built by hand here; nothing is sent to Anthropic.
"""
from __future__ import annotations

from _paths import ROOT  # noqa: F401  (puts the repo on sys.path)
import os
import sys
import tempfile

os.environ["AUTORUNDOWN_HOME"] = tempfile.mkdtemp(prefix="ar_cite_")  # before importing autorundown

from autorundown.query import AnswerModel, _verify_citations  # noqa: E402

fails = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global fails
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f" -- {detail}" if detail and not ok else ""))
    fails += not ok


# what retrieval returned: pages 12 and 40, one diagram on page 40
PASSAGES = [
    {"page_no": 12, "text": "Caliper bolts 120 Nm", "images": ""},
    {"page_no": "40", "text": "Tightening sequence", "images": "4/74443.png\n4/74444.png"},
]


def answer(**kw) -> AnswerModel:
    base = {"found": True, "answer": "Tighten the caliper bolts to 120 Nm."}
    base.update(kw)
    return AnswerModel.model_validate(base)


def spec(name: str, pages: list) -> dict:
    return {"name": name, "value": "120 Nm", "applicability": "all engines", "source_pages": pages}


# 1. a spec citing a real page survives, untouched
a = answer(specs=[spec("Caliper bolt", ["12"])], source_pages=["12"])
dropped = _verify_citations(a, PASSAGES)
check("real citation kept", a.found and len(a.specs) == 1 and not dropped, f"{dropped}")

# 2. passages carry page_no as int or str (WIS DokIDs arrive as either)
a = answer(specs=[spec("Caliper bolt", ["12"])], source_pages=["40"])
_verify_citations(a, PASSAGES)
check("int and str page_no both match", len(a.specs) == 1 and a.source_pages == ["40"], f"{a}")

# 3. a spec citing only an invented page is removed
a = answer(specs=[spec("Caliper bolt", ["12"]), spec("Wheel bolt", ["999"])], source_pages=["12"])
dropped = _verify_citations(a, PASSAGES)
check("invented-page spec dropped", [s.name for s in a.specs] == ["Caliper bolt"], f"{a.specs}")
check("drop is reported", any("999" in d for d in dropped), f"{dropped}")

# 4. a spec citing one real and one invented page keeps only the real one
a = answer(specs=[spec("Caliper bolt", ["999", "12"])], source_pages=["12"])
_verify_citations(a, PASSAGES)
check("mixed citation trimmed to real page", a.specs and a.specs[0].source_pages == ["12"], f"{a.specs}")

# 5. invented source pages are removed
a = answer(specs=[spec("Caliper bolt", ["12"])], source_pages=["12", "77"])
_verify_citations(a, PASSAGES)
check("invented source page dropped", a.source_pages == ["12"], f"{a.source_pages}")

# 6. a figure must be an image retrieval returned -- not just a plausible name
a = answer(source_pages=["40"], figures=[
    {"caption": "sequence", "image": "4/74443.png", "page": "40"},
    {"caption": "made up", "image": "4/99999.png", "page": "40"},
    {"caption": "traversal", "image": "../../secret.png", "page": "40"},
])
_verify_citations(a, PASSAGES)
check("only returned figures kept", [f.image for f in a.figures] == ["4/74443.png"], f"{a.figures}")

# 7. nothing real left -> the answer is withheld, not shown uncited
a = answer(specs=[spec("Caliper bolt", ["999"])], source_pages=["998"],
           figures=[{"caption": "x", "image": "nope.png", "page": "998"}])
_verify_citations(a, PASSAGES)
check("uncited answer withheld", not a.found and a.answer == "" and not a.specs, f"{a}")
check("withheld answer says why", "withheld" in a.caveats.lower(), a.caveats)

# 7b. steps cite nothing themselves: a procedure whose pages were all
#     invented is withheld too, steps and all
a = answer(steps=["Remove the wheel.", "Remove the caliper bolts."], source_pages=["999"])
_verify_citations(a, PASSAGES)
check("uncited procedure withheld", not a.found and not a.steps and a.answer == "", f"{a}")

# 7c. ...but a procedure with a real source page stays
a = answer(steps=["Remove the wheel."], source_pages=["12", "999"])
_verify_citations(a, PASSAGES)
check("cited procedure kept", a.found and a.steps == ["Remove the wheel."]
      and a.source_pages == ["12"], f"{a}")

# 7d. the page opened beside the answer must be a returned page too
a = answer(document_page="40", specs=[spec("Caliper bolt", ["12"])], source_pages=["12"])
dropped = _verify_citations(a, PASSAGES)
check("real document page kept and cited", a.document_page == "40"
      and "40" in a.source_pages and not dropped, f"{a} {dropped}")

a = answer(document_page="999", specs=[spec("Caliper bolt", ["12"])], source_pages=["12"])
dropped = _verify_citations(a, PASSAGES)
check("invented document page falls back to a cited one", a.document_page == "12"
      and any("999" in d for d in dropped), f"{a} {dropped}")

a = answer(specs=[spec("Caliper bolt", ["12"])])
_verify_citations(a, PASSAGES)
check("no document page: the spec's page is opened", a.document_page == "12", f"{a}")

# 7e. an invented document page is not a citation that saves an answer
a = answer(document_page="999", steps=["Remove the wheel."])
_verify_citations(a, PASSAGES)
check("only an invented document page -> withheld", not a.found and a.document_page == "", f"{a}")

# 8. an answer that was already "not found" stays as it is
a = answer(found=False, answer="", caveats="Not in this manual.")
_verify_citations(a, PASSAGES)
check("not-found unchanged", not a.found and a.caveats == "Not in this manual.", f"{a}")

# 9. no passages at all (empty retrieval) withholds any claimed answer
a = answer(specs=[spec("Caliper bolt", ["12"])], source_pages=["12"])
_verify_citations(a, [])
check("empty retrieval withholds", not a.found and not a.specs, f"{a}")

# 10. parts: current numbers that fit this car and that the answer left out
# are added (query._add_omitted_parts). The passage is the real OM651 glow plug
# snippet; the answer is the one Haiku gave (2026-10-15), without 66 01.
from autorundown.query import _add_omitted_parts  # noqa: E402

GLOW = "\n".join([
    "Parts: engine 651 (EPC catalog 69L), group 15 / subgroup 105",
    "Callout | Part number | Name | Fits | Notes",
    "(lines of this page matching: glow, plug)",
    "005 | A 001 159 58 01 (A0011595801, 0011595801) | GLOW PLUG | qty 4: 651.911, 651.912, 651.925 | replaced by A 001 159 66 01 (A0011596601, 0011596601)",
    "005 | A 001 159 66 01 (A0011596601, 0011596601) | GLOW PLUG | qty 4, all models | ",
    "005 | A 001 159 73 01 (A0011597301, 0011597301) | PENCIL-TYPE GLOW PLUG | qty 4: 651.911, 651.912, 651.916 | replaced by A 001 159 80 01 (A0011598001, 0011598001); fn 003: up to engine 32946334 (change date 2015-06-11)",
    "005 | A 001 159 80 01 (A0011598001, 0011598001) | PENCIL-TYPE GLOW PLUG | qty 4: 651.911, 651.912, 651.916 | ",
    "006 | A 001 159 99 01 (A0011599901, 0011599901) | GLOW PLUG, HIGH OUTPUT | qty 4: 651.960 | ",
])
PARTS = [{"page_no": "P48608", "doc_type": "parts", "body": GLOW, "images": ""},
         {"page_no": "P9", "doc_type": "parts", "images": "",
          "body": "Callout | Part number | Name | Fits | Notes\n"
                  "001 | A 642 159 01 01 (A6421590101, 6421590101) | GLOW PLUG | qty 6, all models | "}]


class _Scope(list):
    def __init__(self, codes=((), ()), engines=()):
        super().__init__()
        self.codes, self.engines = codes, engines


C220 = _Scope(codes=(("204.008",), ("651.911",)), engines=("651",))


def pencil_answer(**kw) -> AnswerModel:
    base = {"found": True, "answer": "It is A 001 159 80 01 (pencil-type), qty 4.",
            "document_page": "P48608", "source_pages": ["P48608"],
            "specs": [{"name": "Glow plug", "value": "A 001 159 80 01 (A0011598001)",
                       "applicability": "651.911", "source_pages": ["P48608"]}]}
    base.update(kw)
    return AnswerModel.model_validate(base)


a = pencil_answer()
added = _add_omitted_parts(a, PARTS, C220)
check("the omitted standard plug 66 01 is added", [x["value"] for x in added]
      == ["A 001 159 66 01 (A0011596601)"], f"{added}")
check("... as a spec citing the page it is on",
      a.specs[-1].source_pages == ["P48608"] and "also listed for this car" in a.specs[-1].name, f"{a.specs}")
check("... and said in caveats", "A 001 159 66 01" in a.caveats, a.caveats)
check("superseded numbers (58 01, 73 01) are not added",
      not any(x in str(added) for x in ("A0011595801", "A0011597301")), f"{added}")
check("another engine variant's part (651.960 only) is not added", "A0011599901" not in str(added), f"{added}")
check("a parts page the answer does not cite is not mined", "A6421590101" not in str(added), f"{added}")
dropped = _verify_citations(a, PARTS)
check("the added spec survives the citation check", not dropped and len(a.specs) == 2, f"{dropped}")

a = pencil_answer(answer="A 001 159 80 01 or the standard A 001 159 66 01, qty 4.")
check("nothing added when the answer already names it (spaced)", _add_omitted_parts(a, PARTS, C220) == [])
a = pencil_answer(caveats="Also 0011596601.")
check("... or names it as bare digits", _add_omitted_parts(a, PARTS, C220) == [])
a = pencil_answer()
check("nothing added with no car or engine chosen", _add_omitted_parts(a, PARTS, _Scope()) == [])
a = pencil_answer()
only_engine = _Scope(engines=("651",))
got = [x["value"] for x in _add_omitted_parts(a, PARTS, only_engine)]
check("only an engine chosen: its variants' parts count (66 01 and 99 01)",
      got == ["A 001 159 66 01 (A0011596601)", "A 001 159 99 01 (A0011599901)"], f"{got}")
a = pencil_answer(found=False, answer="", specs=[], source_pages=[], document_page="")
check("a not-found answer gets nothing added", _add_omitted_parts(a, PARTS, C220) == [])

# only parts of what was asked (2026-10-28: "front brake pads" added calipers,
# anchor, disc and screw from the same page, 7 rows burying the pads)
BRAKE = [{"page_no": "P1314", "doc_type": "parts", "images": "", "body": "\n".join([
    "Callout | Part number | Name | Fits | Notes",
    "(lines of this page matching: brake, pad)",
    "010 | A 201 420 06 83 (A2014200683, 2014200683) | BRAKE CALIPER HOUSING | qty 1, all models | ",
    "020 | A 002 420 02 20 (A0024200220, 0024200220) | TS BRAKE PAD, PARTS KIT | qty 1, all models | ",
    "030 | A 001 420 96 20 (A0014209620, 0014209620) | BRAKE PADS, PARTS KIT | qty 1, all models | ",
    "040 | A 201 421 10 12 (A2014211012, 2014211012) | BRAKE DISK | qty 2, all models | ",
    "050 | A 201 540 06 17 (A2015400617, 2015400617) | BRAKE PAD WEAR SENSOR | qty 1, all models | "])}]
W201 = _Scope(codes=(("201.028",), ("102.985",)), engines=("102",))
a = AnswerModel.model_validate({
    "found": True, "answer": "Front pads: A 002 420 02 20.", "document_page": "P1314",
    "source_pages": ["P1314"], "specs": [{"name": "Front brake pads", "value": "A 002 420 02 20",
                                          "applicability": "", "source_pages": ["P1314"]}]})
got = [x["value"] for x in _add_omitted_parts(a, BRAKE, W201, "front brake pads part number")]
check("only the other pad lines are added, not the caliper or disc",
      got == ["A 001 420 96 20 (A0014209620)", "A 201 540 06 17 (A2015400617)"], f"{got}")
a = pencil_answer()
got = [x["value"] for x in _add_omitted_parts(a, PARTS, C220, "glow plug part number")]
check("the glow plug case still adds the standard plug", got == ["A 001 159 66 01 (A0011596601)"], f"{got}")

# the planner's own words ("disc brake linings", "assembly") must not put the
# disc, its screw and the wear sensor back: seen on a paid Haiku answer for
# a 190 E 2.3 (2026-10-29, page P5570); the user's words come first
PLANNER = "disc brake linings specification front axle brake assembly brake lining replacement component"
BRAKE2 = [{"page_no": "P5570", "doc_type": "parts", "images": "", "body": "\n".join([
    "Callout | Part number | Name | Fits | Notes",
    "010 | A 001 420 81 20 (A0014208120, 0014208120) | BRAKE PAD, PARTS KIT | qty 1, 201.028 | ",
    "020 | A 001 420 96 20 (A0014209620, 0014209620) | TS BRAKE PAD, PARTS KIT | qty 1, all models | ",
    "030 | A 201 540 03 17 (A2015400317, 2015400317) | BRAKE WEAR SENSOR, BRAKE LINING WEAR INDICATOR | qty 2, all models | ",
    "040 | A 201 421 15 12 (A2014211512, 2014211512) | BRAKE DISC | qty 2, 201.028 | ",
    "050 | A 201 421 01 71 (A2014210171, 2014210171) | HEXAGON SOCKET SCREW, BRAKE DISC TO WHEEL HUB | qty 2, all models | "])}]
a = AnswerModel.model_validate({
    "found": True, "answer": "Front pads: A 001 420 81 20.", "document_page": "P5570",
    "source_pages": ["P5570"], "specs": [{"name": "Front brake pad", "value": "A 001 420 81 20",
                                          "applicability": "", "source_pages": ["P5570"]}]})
got = [x["value"] for x in _add_omitted_parts(a, BRAKE2, W201, "front brake pads part number", PLANNER)]
check("the planner's 'disc' and 'lining' do not add the disc, screw or sensor",
      got == ["A 001 420 96 20 (A0014209620)"], f"{got}")


def fresh():
    return AnswerModel.model_validate({
        "found": True, "answer": "Fitted.", "document_page": "P5570", "source_pages": ["P5570"],
        "specs": [{"name": "x", "value": "A 000 000 00 00", "applicability": "", "source_pages": ["P5570"]}]})


got = [x["value"] for x in _add_omitted_parts(fresh(), BRAKE2, W201, "wear sensor", PLANNER + " disc")]
check("the question's own words decide, whatever the planner adds",
      got == ["A 201 540 03 17 (A2015400317)"], f"{got}")
got = [x["value"] for x in _add_omitted_parts(fresh(), BRAKE2, W201, "squeaky stopper bits", "brake lining wear indicator")]
check("words that match nothing fall back to the planner's",
      got == ["A 201 540 03 17 (A2015400317)"], f"{got}")

# 11. a part number in the text must be on a page the answer cites
# (query._check_part_numbers). Haiku called the rear pad kit, on a parts page
# in the passages it did not cite, a superseded front pad (2026-10-28).
from autorundown.query import _check_part_numbers  # noqa: E402

a = pencil_answer(answer="It is A 001 159 80 01; the older A 642 159 01 01 was superseded.",
                  caveats="Some cars take A0011596601.")
dropped = _check_part_numbers(a, PARTS)
check("a number only on an uncited page is taken out of the text",
      "642 159 01 01" not in a.answer and "[part number removed" in a.answer, a.answer)
check("... and said in caveats and in dropped",
      "not on the pages it cites" in a.caveats and any("A 642 159 01 01" in d for d in dropped), f"{dropped}")
check("numbers on the cited page stay, spaced or compact",
      "A 001 159 80 01" in a.answer and "A0011596601" in a.caveats, f"{a.answer} / {a.caveats}")
a = pencil_answer(specs=[{"name": "Glow plug", "value": "A 642 159 01 01", "applicability": "",
                          "source_pages": ["P48608"]}])
dropped = _check_part_numbers(a, PARTS)
check("a spec whose number is not on its own cited page is dropped",
      a.specs == [] and any("Glow plug" in d for d in dropped), f"{dropped}")
a = pencil_answer(source_pages=[], document_page="",
                  specs=[{"name": "Glow plug", "value": "A 642 159 01 01", "applicability": "",
                          "source_pages": ["P48608"]}])
_check_part_numbers(a, PARTS)
_verify_citations(a, PARTS)
check("... and an answer left with no citation is withheld", not a.found, a.caveats)
a = pencil_answer()
check("a right answer is untouched", _check_part_numbers(a, PARTS) == [] and len(a.specs) == 1)

print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
