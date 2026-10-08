"""Re-parse document bodies in place, touching only `body_text`. Offline.

    python reparse_bodies.py [--extract <wis_extract dir>] [--dry-run]

For a parser change that should reach the corpus without a full `parse.py`
run -- which would rewrite records.jsonl and manifest.json from scratch and
drop everything added since (schema 1.2 applicability, vehicle sidecars,
schema 1.3 images). Reads bodies.pack, runs parse_sids.parse_doc on each SIDS
record, and replaces body_text. Every other field is kept byte for byte.

Safety check: with the new [figure: ...] markers and figure captions taken
out of both, the new body must keep every word of the old one in order; it
may only add words (text the old graphic-token cleanup ate).
Records that differ are counted and listed; with more than --max-diff of them
the file is not written.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import parse  # noqa: E402
from parse_sids import parse_doc, strip_fig_captions  # noqa: E402

from wis_paths import out_root  # noqa: E402  (data stays outside the repo)
OUT = out_root()
# [figure: ...] from parse_sids, [diagram: ...] once apply_images.py has run --
# ignoring only the first made every illustrated body look like a loss
MARKER_RE = re.compile(r"\[(?:figure|diagram): [^\]]+\]")


def comparable(s: str) -> str:
    s = MARKER_RE.sub(" ", s or "")
    s = strip_fig_captions(s, " ")
    return " ".join(s.split())


def only_additions(old: str, new: str) -> bool:
    """True if `new` keeps every word of `old`, in order, and only adds words.

    The old parser's `\\S*|w|h|FMT` cleanup also ate whatever was glued to a
    graphic token -- "611 589 00 40 00.P.611589004000|..." lost the final
    "00" of a special-tool number. The new parser keeps it, so new text may
    contain words the old one didn't; it must never lose or change one.
    """
    import difflib
    a, b = old.split(" "), new.split(" ")
    return all(op in ("equal", "insert")
               for op, *_ in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--extract", default=str(OUT / "wis_extract"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-diff", type=int, default=50)
    args = ap.parse_args(argv)
    ext = Path(args.extract)
    blobs = parse.read_pack(str(ext / "bodies.pack"))
    src, tmp = ext / "records.jsonl", ext / "records.jsonl.tmp"
    n = sids = changed = with_markers = markers = recovered = 0
    diffs = []
    with src.open(encoding="utf-8") as f, \
            (open(os.devnull, "w", encoding="utf-8") if args.dry_run
             else tmp.open("w", encoding="utf-8", newline="\n")) as w:
        for line in f:
            n += 1
            rec = json.loads(line)
            if rec.get("body_format") == "sids" and rec["dok_id"] in blobs:
                sids += 1
                fmt, _, body, *_ = parse_doc(str(rec["dok_id"]), blobs[rec["dok_id"]],
                                             rec.get("title") or "")
                old_c, new_c = comparable(rec.get("body_text")), comparable(body)
                if old_c != new_c:
                    recovered += 1
                    if not only_additions(old_c, new_c):
                        diffs.append(rec["dok_id"])
                if body != rec.get("body_text"):
                    changed += 1
                k = len(MARKER_RE.findall(body))
                markers += k
                with_markers += bool(k)
                rec["body_text"] = body
                line = json.dumps(rec, ensure_ascii=False) + "\n"
            w.write(line)
    print(f"{n} records, {sids} SIDS re-parsed, {changed} bodies changed, "
          f"{with_markers} with figure markers ({markers} markers); "
          f"{recovered} recover text the old parser dropped; "
          f"{len(diffs)} lose or change text {diffs[:10]}")
    if args.dry_run:
        return 0
    if len(diffs) > args.max_diff:
        tmp.unlink()
        print(f"more than {args.max_diff} unexpected differences; not written", file=sys.stderr)
        return 1
    os.replace(tmp, src)
    print("records.jsonl updated (body_text only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
