"""Attach converted graphics to records.jsonl (schema 1.3). Offline, idempotent.

    python apply_images.py [--extract <wis_extract dir>]

Reads <extract>/images/catalog.tsv (from convert_images.py) and, per record,
writes two fields (additive; nothing else changes):

  image_files   paths relative to <extract>/images/ of the record's figures
                that were converted and exist on disk, in `figures` order
                ("8/176579.svg"). Only files that resolve are listed, so a
                consumer that serves or cites them never points at nothing.
  figure_text   the text labels of those figures (component codes, wire
                colours, cross-references) -- what makes a wiring diagram,
                which has no body text, findable by keyword.

`image_refs` is left alone: in WIS it holds figure *numbers* from the RTF
("P00.20-2768-11"), not files. The manifest gets schema_version 1.3,
images: true, and image counts. Re-running recomputes both fields from the
current catalog, so it is safe after a partial or resumed conversion.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # the installed app's Python does not add a script's folder
from wis_paths import out_root  # noqa: E402  (data stays outside the repo)
OUT = out_root()
FIGURE_TEXT_CAP = 8000
FIGURE_RE = re.compile(r"\[figure: ([^\]]+)\]")


def load_catalog(path: Path) -> dict[tuple[int, int], tuple[str, str]]:
    """(cluster, graphik_id) -> (relative file, labels) for converted graphics."""
    cat = {}
    with path.open(encoding="utf-8") as f:
        next(f)
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) >= 7 and p[2]:
                cat[(int(p[0]), int(p[1]))] = (p[2], p[6])
    return cat


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--extract", default=str(OUT / "wis_extract"))
    args = ap.parse_args(argv)
    ext = Path(args.extract)
    images = ext / "images"
    catalog = load_catalog(images / "catalog.tsv")
    # trust but verify: a catalog row whose file is gone is not attached
    present = {k: v for k, v in catalog.items() if (images / v[0]).is_file()}
    print(f"catalog: {len(catalog)} converted, {len(present)} present on disk")

    manifest_path = ext / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    src, tmp = ext / "records.jsonl", ext / "records.jsonl.tmp"
    n = with_files = figure_only_with = labelled = placed = 0
    used: set[str] = set()
    with src.open(encoding="utf-8") as f, tmp.open("w", encoding="utf-8", newline="\n") as w:
        for line in f:
            rec = json.loads(line)
            n += 1
            files, texts, by_nr = [], [], {}
            for g in rec.get("figures") or []:
                hit = present.get((int(g.get("cluster") or 0), int(g.get("graphik_id") or 0)))
                if hit:
                    by_nr[str(g.get("graphik_nr") or "").lower()] = hit[0]
                if hit and hit[0] not in files:
                    files.append(hit[0])
                    if hit[1]:
                        texts.append(hit[1])
            # parse_sids leaves "[figure: <graphik_nr>]" where the figure sits,
            # after the step it illustrates; point it at the converted file, or
            # drop it when there is none (never a marker to a missing file)
            body = rec.get("body_text") or ""
            if "[figure: " in body:
                def place(m):
                    f = by_nr.get(m.group(1).lower())
                    return f"[diagram: {f}]" if f else ""
                body = FIGURE_RE.sub(place, body)
                placed += body.count("[diagram: ")
                rec["body_text"] = re.sub(r"\n{3,}", "\n\n", body).strip()
            rec["image_files"] = files
            rec["figure_text"] = " ".join(texts)[:FIGURE_TEXT_CAP]
            with_files += bool(files)
            labelled += bool(texts)
            figure_only_with += bool(files) and not (rec.get("body_text") or "").strip()
            used.update(files)
            w.write(json.dumps(rec, ensure_ascii=False) + "\n")
    if n != manifest.get("record_count"):
        tmp.unlink()
        print(f"record count {n} != manifest {manifest.get('record_count')}; aborted",
              file=sys.stderr)
        return 1
    os.replace(tmp, src)

    major, _, minor = str(manifest.get("schema_version", "1.2")).partition(".")
    if major == "1" and int(minor or 0) < 3:
        manifest["schema_version"] = "1.3"
    manifest["records_bytes"] = src.stat().st_size
    manifest["images"] = {
        "dir": "images",
        "files_field": "image_files",
        "text_field": "figure_text",
        "converted": len(present),
        "referenced": len(used),
        "records_with_images": with_files,
        "figure_only_records_with_images": figure_only_with,
        "records_with_figure_text": labelled,
        "figures_placed_inline": placed,
    }
    manifest_path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False),
                             encoding="utf-8")
    print(f"{n} records: {with_files} with images ({figure_only_with} figure-only), "
          f"{labelled} with figure text; {len(used)} distinct files referenced; "
          f"{placed} figures placed inline in their step")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
