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

print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
