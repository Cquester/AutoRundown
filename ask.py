"""Ask the archive a question from the command line.

    python ask.py "torque for the front caliper anchor plate bolts"
    python ask.py --search "caliper anchor plate"     # retrieval only, no API
"""
from __future__ import annotations

import argparse
import sys

from autorundown import config, index, providers, query
from autorundown.search import scope_ids, search


def pick_vehicle(vehicles: list[dict], wanted: str | None) -> dict | None:
    if not vehicles:
        return None
    if not wanted:
        return vehicles[0]
    needle = wanted.lower()
    for v in vehicles:
        haystack = f"{v['doc_id']} {v['year_range']} {v['make']} {v['model']}".lower()
        if needle in haystack:
            return v
    return None


def money(x) -> str:
    """A logged cost; None = a model whose price is not known."""
    return "cost not tracked" if x is None else f"${x:.4f}"


def main(argv: list[str] | None = None) -> int:
    cfg = config.load()
    ap = argparse.ArgumentParser(description="Query the manual archive.")
    ap.add_argument("question", nargs="*", help="your question")
    ap.add_argument("--index", default=cfg.index_path)
    ap.add_argument("--vehicle", default=None,
                    help="substring of the vehicle to query")
    ap.add_argument("--list", action="store_true", help="list indexed vehicles")
    ap.add_argument("--usage", action="store_true",
                    help="show what has been spent, by source")
    ap.add_argument("--search", action="store_true",
                    help="keyword search only; makes no API call")
    ap.add_argument("--include-other-variants", action="store_true")
    ap.add_argument("--model", default=config.active_model(cfg),
                    help="one of " + ", ".join(m for p in providers.PROVIDERS.values()
                                               for m in p["models"])
                         + "; the provider is the model's, and its company key pays")
    args = ap.parse_args(argv)

    conn = index.connect(args.index)
    # a parts catalogue joined into a manual's car is searched through that car
    vehicles = [v for v in index.vehicles(conn) if not v.get("joined_into")]

    if args.usage:
        s = index.usage_summary(conn)
        print(f"total spent: ${s['total_usd']:.4f}\n")
        if s["by_source"]:
            print("by source:")
            for r in s["by_source"]:
                print(f"  {r['source'] or '?':<8} {r['questions']:>4} questions "
                      f"{r['calls']:>5} calls   {money(r['cost_usd'])}")
        if any(r["user"] not in ("cli", "eval", "this PC / no sign-in")
               for r in s["by_user"]):
            print("\nby user:")
            for r in s["by_user"]:
                print(f"  {r['user'] or '?':<22} {r['questions']:>4} questions   "
                      f"{money(r['cost_usd'])}")
        if s["by_day"]:
            print("\nby day:")
            for r in s["by_day"]:
                print(f"  {r['day']}  {r['questions']:>4} questions  "
                      f"{money(r['cost_usd'])}")
        recent = index.recent_usage(conn, 10)
        if recent:
            print("\nmost recent:")
            for r in recent:
                print(f"  {r['ts']}  {r['source'] or '?':<5} {money(r['cost_usd'])}"
                      f"  {r['provider']}/{r['model']}  {(r['question'] or '')[:40]}")
        if not s["by_source"]:
            print("nothing logged yet (logging started this session)")
        return 0

    if args.list or not args.question:
        if not vehicles:
            print("no vehicles indexed; run ingest.py first", file=sys.stderr)
            return 1
        print("indexed vehicles:")
        for v in vehicles:
            print(f"  {v['doc_id']}")
            print(f"    {v['year_range']} {v['make']} {v['model']}")
            print(f"    engine={v['engine'] or '-'} pages={v['indexed_pages']}")
        return 0

    vehicle = pick_vehicle(vehicles, args.vehicle)
    if vehicle is None:
        print(f"no indexed vehicle matches {args.vehicle!r}", file=sys.stderr)
        return 1
    question = " ".join(args.question)
    print(f"vehicle : {vehicle['year_range']} {vehicle['make']} {vehicle['model']}")
    print(f"question: {question}\n")

    if args.search:
        rows = search(conn, scope_ids(vehicle), question, 10,
                      include_other_variants=args.include_other_variants)
        if not rows:
            print("no matches")
            return 0
        for r in rows:
            flag = "" if r["applicability"] == "this_vehicle" else "  [OTHER VARIANT]"
            print(f"  page {r['page_no']:<8} {r['title'][:56]}{flag}")
            print(f"       {r['section_path'][:88]}")
        return 0

    try:
        provider = providers.provider_of(args.model) or providers.DEFAULT_PROVIDER
        result = query.ask(conn, vehicle, question, config.company_key(cfg, provider),
                           include_other_variants=args.include_other_variants,
                           model=args.model, source="cli")
    except query.NoApiKey as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("\nTip: retrieval works without a key -- try --search", file=sys.stderr)
        return 2
    except query.ApiUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("\nTip: keyword search still works offline -- try --search",
              file=sys.stderr)
        return 3

    a = result.answer
    print(f"searched: {', '.join(result.queries)}\n")
    if not a.found:
        print("NOT FOUND IN ARCHIVE")
        if a.caveats:
            print(a.caveats)
    else:
        print(a.answer)
        if a.specs:
            print("\nspecifications:")
            for s in a.specs:
                print(f"  - {s.name}: {s.value}")
                pages = ", ".join(str(x) for x in s.source_pages)
                print(f"    applies to: {s.applicability}   [pages {pages}]")
        if a.steps:
            print("\nprocedure:")
            for n, st in enumerate(a.steps, 1):
                print(f"  {n}. {st}")
        if a.figures:
            print("\nfigures:")
            for f in a.figures:
                print(f"  - {f.caption}  [page {f.page}: {f.image}]")
        titles = result.page_titles
        if a.source_pages:
            print("\nsources:")
            for pg in a.source_pages:
                print(f"  page {pg}: {titles.get(str(pg), '')}")
        if a.caveats:
            print(f"\ncaveats: {a.caveats}")
    if result.dropped_citations:
        print(f"\ndropped unverifiable citations: {result.dropped_citations}")

    u = result.usage
    print(f"\n[{u.model}: {u.calls} API calls, "
          f"{u.input_tokens} in / {u.output_tokens} out, ~{money(u.cost_usd)}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
