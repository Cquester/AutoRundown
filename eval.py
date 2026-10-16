"""Known-answer validation.

Two levels, because they fail for different reasons:

  (default)     does the right page reach the top of the results?
                FREE -- no API calls. Catches indexing, dedup, ranking and
                variant-filter regressions, which is most of what breaks.
  --answers     does the answer state the right value and cite the right page?
                SPENDS API CREDIT. Only needed after changing prompts, the
                retrieval shape, or the model.

THE CASES BELOW ARE ARCHIVE-SPECIFIC. They reference page numbers from one
particular 2015 F-150 manual set, so they will not pass against a different
archive. Replace them with known answers from your own: look a spec up by
hand, note the page it is printed on, and add an entry. A handful of cases is
enough to catch the failures that matter.

Every expected value below was read directly out of the indexed archive, so a
failure means the pipeline broke -- not that the manual disagrees.

`expect_page` may be a list when the same spec is printed on several pages;
citing any one of them counts as correct.

`expect_page` is always the *canonical* page. This archive stores each article
under several folders; the indexer chunks the first copy only, so retrieval
returns page 9741 rather than its duplicates 12217 / 18247 / 28666.

`retrieval_query` is the manual-terminology phrasing, used only by the
retrieval check so it measures the index rather than the planner. The full
check always uses the natural-language `question`.

    python eval.py                     # free retrieval check
    python eval.py --answers           # paid, prints an estimate first
    python eval.py --answers --case caliper-anchor-torque --model claude-haiku-4-5
"""
from __future__ import annotations

import argparse
import re
import sys

from autorundown import config, index, query
from autorundown.search import search

F150 = "2015-ford-f-150-lariat-4d-pickup-extra-cab-3-5l-eng-vin-g-4wd"

CASES = [
    {
        "id": "caliper-anchor-torque",
        "doc_id": F150,
        "question": "What is the torque for the front brake caliper anchor plate bolts?",
        "retrieval_query": "front brake caliper anchor plate removal",
        "expect_page": "6706",
        "expect_values": ["184", "250"],
    },
    {
        "id": "spark-plug-torque",
        "doc_id": F150,
        "question": "What torque do the spark plugs get?",
        "retrieval_query": "spark plugs removal torque",
        "expect_page": "9743",
        "expect_values": ["133", "15"],
    },
    {
        "id": "oil-pan-drain-torque",
        "doc_id": F150,
        "question": "Torque spec for the oil pan drain plug?",
        "retrieval_query": "oil pan removal drain plug",
        "expect_page": "9371",
        "expect_values": ["20", "27"],
    },
    {
        "id": "engine-oil-capacity",
        "doc_id": F150,
        "question": "How much engine oil does it take with a filter change?",
        "retrieval_query": "engine oil capacity",
        "expect_page": "9316",
        "expect_values": ["6.0", "5.7"],
    },
    {
        "id": "coil-on-plug-torque",
        "doc_id": F150,
        "question": "Torque for the ignition coil-on-plug bolts?",
        "retrieval_query": "ignition coil-on-plug installation",
        "expect_page": "9741",
        "expect_values": ["62", "7"],
    },
    {
        # two sub-questions in one. Global-score merging used to let the
        # capacity queries fill the context and drop the torque queries
        # entirely; retrieval now merges round-robin so both survive.
        "id": "multipart-oil-and-torque",
        "doc_id": F150,
        "question": "how much oil does it take and what's the drain plug torque",
        "retrieval_query": "oil pan drain plug torque",
        # the same drain-plug spec is printed on three pages; citing any of
        # them is correct, so don't fail an answer for picking a different one
        "expect_page": ["9371", "9391", "9344"],
        "expect_values": ["6.0", "20", "27"],
    },
    {
        # negative case: the archive is a Ford manual, so this must not answer
        "id": "absent-wrong-vehicle",
        "doc_id": F150,
        "question": "What is the valve lash clearance for a Honda K20 engine?",
        "expect_found": False,
    },
]


def _expected_pages(case: dict) -> set[str]:
    """`expect_page` is one page number or several equally valid ones."""
    want = case["expect_page"]
    return {str(want)} if isinstance(want, str) else {str(w) for w in want}


def run_retrieval(top_n: int = 10, cases: list[dict] | None = None,
                  index_path: str | None = None) -> int:
    conn = index.connect(index_path or config.load().index_path)
    failures = 0
    print("retrieval check -- no API calls, costs nothing")
    print(f"(expected page must appear in top {top_n})\n")
    for case in (cases if cases is not None else CASES):
        if "expect_page" not in case:
            continue
        probe = case.get("retrieval_query", case["question"])
        rows = search(conn, case["doc_id"], probe, top_n)
        pages = [str(r["page_no"]) for r in rows]
        wanted = _expected_pages(case)
        hit = next(((p, pages.index(p) + 1) for p in pages if p in wanted), None)
        if hit:
            print(f"  PASS  {case['id']:<24} page {hit[0]} at rank {hit[1]}")
        else:
            failures += 1
            print(f"  FAIL  {case['id']:<24} none of {sorted(wanted)} in {pages[:6]}")
    conn.close()
    print(f"\n{failures} failure(s)")
    return 1 if failures else 0


def run_full(cases: list[dict] | None = None, model: str | None = None,
             index_path: str | None = None) -> int:
    cfg = config.load()
    chosen = model or config.active_model(cfg)
    from autorundown import providers
    key = config.company_key(cfg, providers.provider_of(chosen) or "anthropic")
    if not key:
        print("No API key configured. Run without --answers for the free "
              "retrieval check, or set a key first.", file=sys.stderr)
        return 2

    cases = cases if cases is not None else CASES
    # rough per-question shape measured on this archive: ~6k in, ~900 out
    estimate = len(cases) * (providers.cost(chosen, 6000, 900) or 0.0)

    conn = index.connect(index_path or cfg.index_path)
    vehicles = {v["doc_id"]: v for v in index.vehicles(conn)}
    failures, total_cost = 0, 0.0
    print(f"answer check -- {len(cases)} case(s) on {chosen}")
    print(f"THIS SPENDS API CREDIT, roughly ${estimate:.2f}. Ctrl-C to stop.\n")

    for case in cases:
        vehicle = vehicles.get(case["doc_id"])
        if vehicle is None:
            print(f"  SKIP  {case['id']:<24} vehicle not indexed")
            continue

        result = query.ask(conn, vehicle, case["question"], key,
                           model=chosen, source="eval")
        total_cost += result.usage.cost_usd or 0.0
        answer = result.answer
        problems: list[str] = []

        if case.get("expect_found") is False:
            if answer.found:
                problems.append(f"answered when it should not have: {answer.answer[:70]}")
        else:
            if not answer.found:
                problems.append("reported not-found")
            blob = " ".join(
                [answer.answer] + [f"{s.name} {s.value}" for s in answer.specs])
            for value in case.get("expect_values", []):
                if not re.search(rf"\b{re.escape(value)}\b", blob):
                    problems.append(f"missing value {value!r}")
            cited = set(answer.source_pages)
            for sp in answer.specs:
                cited.update(str(x) for x in sp.source_pages)
            wanted = _expected_pages(case)
            if not (cited & wanted):
                problems.append(
                    f"cited none of {sorted(wanted)} (cited {sorted(cited)})")

        if result.dropped_citations:
            problems.append(f"hallucinated citations: {result.dropped_citations}")

        if problems:
            failures += 1
            print(f"  FAIL  {case['id']}")
            for p in problems:
                print(f"          - {p}")
        else:
            print(f"  PASS  {case['id']:<26} (running ${total_cost:.4f})")

    conn.close()
    print(f"\n{failures} failure(s), ~${total_cost:.4f} spent")
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Validate against known answers. Defaults to the FREE "
                    "retrieval check; --answers spends API credit.")
    ap.add_argument("--answers", action="store_true",
                    help="run the paid answer check (makes API calls)")
    ap.add_argument("--retrieval", action="store_true",
                    help="retrieval only, no API calls (this is the default)")
    ap.add_argument("--case", action="append", default=None,
                    help="run only this case id; repeatable")
    ap.add_argument("--model", default=None,
                    help="override the configured model for this run only")
    ap.add_argument("--index", default=None,
                    help="path to index.db (defaults to the configured one)")
    ap.add_argument("--top", type=int, default=10)
    args = ap.parse_args(argv)

    cases = CASES
    if args.case:
        wanted = set(args.case)
        cases = [c for c in CASES if c["id"] in wanted]
        if not cases:
            print(f"no case matches {sorted(wanted)}; have "
                  f"{[c['id'] for c in CASES]}", file=sys.stderr)
            return 1

    # Paid only when explicitly asked for. The free check catches indexing,
    # dedup, ranking, and variant-filter regressions -- which is most of what
    # actually breaks -- so it is the sensible default to run often.
    if not args.answers:
        return run_retrieval(args.top, cases, args.index)
    return run_full(cases, args.model, args.index)


if __name__ == "__main__":
    raise SystemExit(main())
