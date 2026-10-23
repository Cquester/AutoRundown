"""Stand-in EPC parts catalogue -> sample_epc/ (FREE, invented, no network).

    python tools/make_sample_epc.py [--out sample_epc]

The shape `extractor/pull_epc.js` writes (records.jsonl + manifest.json +
images/epc/*.png), for the sample WIS cars of tools/make_sample_corpus.py:
the W210 and W201 chassis, engines 112 and 102 (theirs) and 103 (the 190 E
2.6's, which a 2.3-16 must never list), and one option (SA). Each page has
the parts list (callout | part number | name | fits | notes) and drawings
whose callout labels ("7C") match the list's first column ("007C"), drawn
here with Pillow. Part numbers, names and drawings are made up: nothing
from Mercedes is in this repo.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]


def line(callout, pn, name, fits, notes=""):
    digits = pn.replace(" ", "")[1:]
    return f"{callout} | {pn} ({pn.replace(' ', '')}, {digits}) | {name} | {fits} | {notes}"


# (dok_id, title, series or engine, [parts lines], number of drawings)
PAGES = [
    (900001, "Parts: model 210 (EPC catalog S10), group 24 ENGINE SUSPENSION / subgroup 001 RUBBER MOUNTING",
     ("model", "210"), [
         line("007C", "A 210 240 01 17", "ENGINE MOUNT, LEFT", "qty 1: 210.082, 210.282"),
         line("007D", "A 210 240 02 17", "ENGINE MOUNT, RIGHT", "qty 1: 210.082, 210.282"),
         line("013E", "A 210 241 05 40", "HEAT SHIELD", "qty 1, all models"),
         line("064D", "N 000000 001234", "HEXAGON NUT", "qty 4, all models")], 1),
    # the same subgroup in the series' second catalog (another market): one entry
    (900002, "Parts: model 210 (EPC catalog S11), group 24 ENGINE SUSPENSION / subgroup 001 RUBBER MOUNTING",
     ("model", "210"), [
         line("007C", "A 210 240 01 17", "ENGINE MOUNT, LEFT", "qty 1: 210.082, 210.282")], 0),
    (900003, "Parts: model 210 (EPC catalog S10), group 42 BRAKES / subgroup 010 FRONT WHEEL BRAKE",
     ("model", "210"), [
         line("010", "A 210 420 07 20", "BRAKE PAD SET", "qty 1, all models"),
         line("020", "A 210 421 12 12", "BRAKE DISC", "qty 2, all models"),
         line("030", "A 210 420 05 83", "BRAKE CALIPER, LEFT", "qty 1, all models"),
         line("-", "A 210 420 06 83", "BRAKE CALIPER, RIGHT", "qty 1, all models")], 2),
    (900004, "Parts: model 210 (EPC catalog S10), group 32 SUSPENSION / subgroup 005 FRONT SPRINGS",
     ("model", "210"), [
         line("005", "A 210 321 23 04", "COIL SPRING", "qty 2: 210.082"),
         line("005", "A 210 321 24 04", "COIL SPRING, REINFORCED", "qty 2: 210.282", "wagon"),
         # EPC's word for a sway bar (synonyms.py; tests/test_parts_diagrams.py)
         line("040", "A 210 323 15 65", "STABILIZER", "qty 1, all models")], 1),
    (900005, "Parts: model 201 (EPC catalog S01), group 42 BRAKES / subgroup 010 FRONT WHEEL BRAKE",
     ("model", "201"), [
         line("010", "A 201 420 15 20", "BRAKE PAD SET", "qty 1, all models"),
         line("020", "A 201 421 03 12", "BRAKE DISC", "qty 2, all models")], 1),
    (900006, "Parts: engine 112 (EPC catalog E12), group 18 LUBRICATION / subgroup 015 OIL FILTER",
     ("engine", "112"), [
         line("010", "A 112 180 01 09", "OIL FILTER ELEMENT", "qty 1, all models"),
         line("015", "A 112 184 02 08", "OIL FILTER COVER", "qty 1, all models")], 1),
    (900007, "Parts: engine 112 (EPC catalog E12), group 01 CRANKCASE / subgroup 030 OIL PAN",
     ("engine", "112"), [
         line("020", "A 112 014 05 02", "OIL PAN, BOTTOM SECTION", "qty 1, all models"),
         line("025", "A 112 542 03 17", "OIL LEVEL SENSOR", "qty 1, all models"),
         line("030", "N 000000 004321", "SELF-TAPPING SCREW", "qty 14, all models")], 1),
    (900008, "Parts: engine 102 (EPC catalog E02), group 18 LUBRICATION / subgroup 015 OIL FILTER",
     ("engine", "102"), [
         line("010", "A 102 180 00 09", "OIL FILTER ELEMENT", "qty 1, all models")], 1),
    (900009, "Parts: engine 103 (EPC catalog E03), group 18 LUBRICATION / subgroup 015 OIL FILTER",
     ("engine", "103"), [
         line("010", "A 103 180 00 09", "OIL FILTER ELEMENT", "qty 1, all models")], 1),
    (900010, "Parts: option SA 12345 (code 550), model 210 / subgroup 001 TRAILER HITCH",
     ("model", "210"), [
         line("001", "A 210 310 00 45", "TRAILER HITCH", "qty 1, all models")], 1),
]


def font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                  # Pillow < 10.1
        return ImageFont.load_default()


def drawing(path: Path, callouts: list[str], seed: int) -> None:
    """An invented exploded drawing: outlined shapes, each with its callout,
    and beside it <name>.callouts.json -- each label's centre, as the
    extractor writes it from a real drawing's annotation layer."""
    rnd = random.Random(seed)
    img = Image.new("L", (1024, 720), 255)
    d = ImageDraw.Draw(img)
    d.rectangle((60, 60, 964, 680), outline=0, width=2)
    f = font(22)
    n = max(len(callouts), 1)
    placed = []
    for i, c in enumerate(callouts):
        x = 140 + (i % 3) * 280 + rnd.randint(-30, 30)
        y = 140 + (i // 3) * 200 + rnd.randint(-20, 20)
        if i % 2:
            d.ellipse((x, y, x + 130, y + 80), outline=0, width=3)
        else:
            d.polygon([(x, y + 80), (x + 60, y), (x + 150, y + 20), (x + 110, y + 100)], outline=0, width=3)
        d.line((x + 130, y + 40, x + 175, y + 30), fill=0, width=2)
        d.text((x + 180, y + 18), c, fill=0, font=f)
        l, t, r, b = d.textbbox((x + 180, y + 18), c, font=f)
        placed.append({"x": (l + r) // 2, "y": (t + b) // 2, "c": c})
    if n == 0:
        d.text((400, 340), "(no callouts)", fill=0, font=f)
    img.save(path, optimize=True)
    path.with_suffix(".callouts.json").write_text(json.dumps(placed), encoding="utf-8")


def callout_label(c: str) -> str:
    """"007C" -> "7C", as printed on the drawing."""
    return c.lstrip("0") or c


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(ROOT / "sample_epc"))
    out = Path(ap.parse_args(argv).out)
    if out.exists():
        shutil.rmtree(out)
    (out / "images" / "epc").mkdir(parents=True)
    with (out / "records.jsonl").open("w", encoding="utf-8", newline="\n") as f:
        for dok, title, (kind, code), lines, n_draw in PAGES:
            images = []
            callouts = list(dict.fromkeys(callout_label(l.split(" | ")[0]) for l in lines
                                          if not l.startswith("-")))
            for k in range(n_draw):
                name = f"B{dok}{k + 1:02d}.000000.png"
                drawing(out / "images" / "epc" / name, callouts[k::n_draw] or callouts, dok * 10 + k)
                images.append(f"epc/{name}")
            body = "\n".join([title, "", "Callout | Part number | Name | Fits | Notes", *lines])
            f.write(json.dumps({
                "dok_id": dok, "lang": "en", "title": title, "body_format": "sids",
                "body_text": body,
                "model_series": [code] if kind == "model" else [], "baumuster": [],
                "engine_series": [code] if kind == "engine" else [],
                "applicability_notes": [("MODEL " if kind == "model" else "ENGINE ") + code],
                "cross_refs": [], "image_refs": [], "figures": [], "image_files": images,
                "source_type": "mercedes-epc"}) + "\n")
    (out / "manifest.json").write_text(json.dumps({
        "schema_version": "1.3", "source_type": "mercedes-epc", "source_id": "sample",
        "page_no_prefix": "P", "doc_type": "parts", "language": {"iso": "en"},
        "record_count": len(PAGES), "records_file": "records.jsonl"}, indent=2), encoding="utf-8")
    print(f"sample EPC: {len(PAGES)} pages -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
