"""Draw one Mercedes WIS document from its SIDS layout, to a standalone file.

    python tools/sids_render.py <dok_id> [--corpus <wis_extract>] [--out page.html]
                                         [--base http://localhost:8756]

The renderer is `autorundown/wis_original.py` -- the same code behind the
site's "Original layout" button (/api/original/<doc_id>/<page_no>). This is
for looking at one document without the server. Figures are linked through a
running server's /api/image route (`--base`); without one they show as empty
boxes.

The corpus defaults to the folder of any Mercedes WIS vehicle in the index
(its `file_path`), so it works from a git worktree too.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from autorundown import config, index, wis_original  # noqa: E402


def wis_vehicle(index_path: str) -> dict:
    conn = index.connect(index_path)
    try:
        for v in index.vehicles(conn):
            if (v.get("source_name") or "").startswith("mercedes-wis"):
                return v
    finally:
        conn.close()
    raise SystemExit(f"no Mercedes WIS vehicle in {index_path}; pass --corpus")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dok_id", type=int)
    ap.add_argument("--corpus", default="", help="the wis_extract folder (has bodies.pack)")
    ap.add_argument("--index", default=config.load().index_path)
    ap.add_argument("--out", default="")
    ap.add_argument("--base", default="http://localhost:8756")
    a = ap.parse_args(argv)
    if a.corpus:
        corpus, doc_id = Path(a.corpus), "mercedes-wis"
    else:
        v = wis_vehicle(a.index)
        corpus, doc_id = Path(v["file_path"]), v["doc_id"]
    out = Path(a.out or f"wis_{a.dok_id}.html")
    out.write_text(wis_original.render(
        corpus, a.dok_id, str(a.dok_id),
        lambda f: f"{a.base.rstrip('/')}/api/image/{doc_id}/{f}"), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
