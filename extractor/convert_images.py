"""Convert pulled WIS graphics to browser formats + extract their text labels.

    python convert_images.py [--raw <wis_images/raw>] [--out <wis_extract/images>] [--jobs N]

Input:  raw/<cluster>/<graphik_id>.bin from pull_images.js (full layout).
Output: <out>/<cluster>/<graphik_id>.<png|jpg|svg>, and <out>/catalog.tsv:
        cluster, graphik_id, file (relative to <out>), kind, width, height,
        labels (space-joined text found in vector graphics), note.
Resumable: graphics already in catalog.tsv are skipped (delete a row, or the
whole file, to redo). Offline; PyPI packages only: Pillow, ezdxf, matplotlib.

Format by magic bytes, never by GraphikKlasse (measured on the sample, see
autorundown_out/wis_images_sample/):
  TIFF CCITT G4 (1-bit)   -> PNG          JPEG -> copied        PNG -> copied
  JPEG 2000               -> PNG
  gzip/.Z SVG (iso-8859-1)-> SVG, re-encoded UTF-8 (wiring diagrams)
  gzip/.Z DXF R12         -> SVG via ezdxf (wiring diagrams)
SVG is stored uncompressed so the server can hand it to an <img> as is.

Labels are what makes a wiring diagram findable by keyword: component codes
(N26/15), wire colours, pins, cross-references (PE 54.21-D-2104AA). Raster
graphics have none (no OCR, by decision).
"""
from __future__ import annotations

import argparse
import gzip
import io
import logging
import os
import re
import sys
import time
from multiprocessing import Pool
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))  # the installed app's Python does not add a script's folder
from wis_paths import out_root  # noqa: E402  (data stays outside the repo)
OUT_ROOT = out_root()
LABEL_CAP = 4000


def unlzw_z(data: bytes) -> bytes:
    """Unix compress (.Z) decoder after Mark Adler's unlzw.c (pigz).

    parse_sids.unlzw() desyncs after the first code-width change and hangs on
    some graphics; this one follows the reference decoder.
    """
    if data[:2] != b"\x1f\x9d":
        raise ValueError("not .Z")
    flags = data[2]; maxb = flags & 0x1f; block = flags & 0x80
    n = len(data); nxt = 3; mark = 3
    bits = 9; mask = 0x1ff; end = 256 if block else 255
    prefix = [0] * 65536; suffix = bytearray(65536)
    out = bytearray()
    if nxt + 1 >= n:
        return bytes(out)
    buf = data[nxt] | (data[nxt + 1] << 8); nxt += 2
    prev = buf & mask; buf >>= bits; left = 16 - bits
    if prev > 255:
        raise ValueError("bad first code")
    final = prev; out.append(prev)
    stack = bytearray()
    while True:
        if end >= mask and bits < maxb:
            rem = (nxt - mark) % bits
            if rem:
                rem = bits - rem
                if rem > n - nxt:
                    break
                nxt += rem
            buf = 0; left = 0; mark = nxt
            bits += 1; mask = (mask << 1) | 1
        if nxt >= n and left < bits:
            break
        while left < bits:
            if nxt >= n:
                break
            buf |= data[nxt] << left; nxt += 1; left += 8
        if left < bits:
            break
        code = buf & mask; buf >>= bits; left -= bits
        if code == 256 and block:
            rem = (nxt - mark) % bits
            if rem:
                rem = bits - rem
                if rem > n - nxt:
                    break
                nxt += rem
            buf = 0; left = 0; mark = nxt
            bits = 9; mask = 0x1ff; end = 255
            continue
        temp = code
        if code > end:
            if code != end + 1 or prev > end:
                raise ValueError("invalid code")
            stack.append(final); code = prev
        while code >= 256:
            stack.append(suffix[code]); code = prefix[code]
        stack.append(code); final = code
        if end < mask:
            end += 1; prefix[end] = prev; suffix[end] = final
        prev = temp
        stack.reverse(); out += stack; stack.clear()
    return bytes(out)


def unwrap(b: bytes) -> bytes | None:
    if b[:2] == b"\x1f\x8b":
        return gzip.decompress(b)
    if b[:2] == b"\x1f\x9d":
        return unlzw_z(b)
    return None


def sniff(b: bytes) -> tuple[str, bytes | None]:
    if b[:4] in (b"MM\x00*", b"II*\x00"):
        return "tiff", None
    if b[:3] == b"\xff\xd8\xff":
        return "jpeg", None
    if b[:12] == b"\x00\x00\x00\x0cjP  \r\n\x87\n":
        return "jp2", None
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png", None
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "gif", None
    d = unwrap(b)
    if d is not None:
        if b"<svg" in d[:2000] or d[:400].lstrip().startswith(b"<?xml"):
            return "svg", d
        if re.match(rb"\s*0\s*\r?\nSECTION", d[:40]):
            return "dxf", d
        return "compressed?", d
    return "unknown", None


def clean_labels(texts) -> str:
    seen, out = set(), []
    for t in texts:
        t = " ".join(str(t).split())
        if t and t not in seen and not re.fullmatch(r"[\d.,\s-]{1,3}", t):
            seen.add(t); out.append(t)
    s = " ".join(out)
    return s[:LABEL_CAP]


def svg_labels(s: str) -> str:
    import html
    texts = re.findall(r"<text\b[^>]*>(.*?)</text>", s, re.S)
    texts = [html.unescape(re.sub(r"<[^>]+>", " ", t)) for t in texts]
    return clean_labels(texts)


def dxf_to_svg(data: bytes, dst: Path) -> tuple[str, int, int]:
    import ezdxf  # noqa: F401
    from ezdxf import recover
    from ezdxf.addons.drawing import Frontend, RenderContext, svg, layout, config
    logging.getLogger("ezdxf").setLevel(logging.ERROR)
    # WIS DXF reuses entity handles; a strict ezdxf.read() silently draws an
    # empty picture. recover.read() re-assigns handles and keeps the entities.
    doc, _ = recover.read(io.BytesIO(data))
    msp = doc.modelspace()
    labels = []
    for e in msp.query("TEXT MTEXT ATTRIB"):
        try:
            labels.append(e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text)
        except Exception:
            pass
    for ins in msp.query("INSERT"):
        for a in getattr(ins, "attribs", []):
            labels.append(a.dxf.text)
    cfg = config.Configuration(background_policy=config.BackgroundPolicy.WHITE,
                               color_policy=config.ColorPolicy.BLACK)
    be = svg.SVGBackend()
    Frontend(RenderContext(doc), be, config=cfg).draw_layout(msp)
    page = layout.Page(0, 0, layout.Units.mm, margins=layout.Margins.all(2))
    s = be.get_string(page)
    dst.write_text(s, encoding="utf-8")
    m = re.search(r'viewBox="[\d.\-]+ [\d.\-]+ ([\d.]+) ([\d.]+)"', s[:2000])
    w, h = (int(float(m.group(1))), int(float(m.group(2)))) if m else (0, 0)
    return clean_labels(labels), w, h


def convert_one(job: tuple[str, int, str, str]) -> tuple:
    """(cluster, gid, src, outdir) -> catalog row. Never raises."""
    cl, gid, src, outdir = job
    from PIL import Image
    t0 = time.time()
    rel, kind, w, h, labels, note = "", "?", 0, 0, "", ""
    try:
        b = Path(src).read_bytes()
        kind, d = sniff(b)
        base = Path(outdir) / cl / str(gid)
        base.parent.mkdir(parents=True, exist_ok=True)
        if kind in ("tiff", "jp2"):
            im = Image.open(io.BytesIO(b)); im.load()
            w, h = im.size
            im.save(base.with_suffix(".png"), optimize=True)
            rel = f"{cl}/{gid}.png"
        elif kind in ("jpeg", "png", "gif"):   # browser-native: copied as is
            im = Image.open(io.BytesIO(b)); w, h = im.size
            ext = {"jpeg": ".jpg", "png": ".png", "gif": ".gif"}[kind]
            base.with_suffix(ext).write_bytes(b)
            rel = f"{cl}/{gid}{ext}"
        elif kind == "svg":
            m = re.search(rb'encoding="([^"]+)"', d[:200])
            enc = m.group(1).decode() if m else "utf-8"
            s = d.decode(enc, errors="replace")
            s = re.sub(r'encoding="[^"]+"', 'encoding="utf-8"', s, count=1)
            base.with_suffix(".svg").write_text(s, encoding="utf-8")
            rel = f"{cl}/{gid}.svg"
            labels = svg_labels(s)
            m = re.search(r'<svg\b[^>]*?width="([\d.]+)[^"]*"[^>]*?height="([\d.]+)', s[:4000])
            if m:
                w, h = int(float(m.group(1))), int(float(m.group(2)))
        elif kind == "dxf":
            labels, w, h = dxf_to_svg(d, base.with_suffix(".svg"))
            rel = f"{cl}/{gid}.svg"
        else:
            note = "unhandled " + repr(b[:12])
    except Exception as e:  # one bad graphic must not stop the pass
        rel, note = "", f"FAILED {type(e).__name__}: {e}"[:300]
    note = note or f"{time.time() - t0:.2f}s"
    # DXF text can carry undecodable bytes (lone surrogates); they crashed the
    # catalog write, so replace them rather than lose the row
    labels = labels.encode("utf-8", "replace").decode("utf-8")
    note = note.encode("utf-8", "replace").decode("utf-8")
    return (cl, gid, rel, kind, w, h, labels.replace("\t", " ").replace("\n", " "), note)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--raw", default=str(OUT_ROOT / "wis_images" / "raw"))
    ap.add_argument("--out", default=str(OUT_ROOT / "wis_extract" / "images"))
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--limit", type=int, default=0, help="convert at most N (testing)")
    args = ap.parse_args(argv)
    raw, out = Path(args.raw), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cat = out / "catalog.tsv"
    done = set()
    if cat.is_file():
        with cat.open(encoding="utf-8") as f:
            next(f, None)
            for line in f:
                p = line.split("\t", 3)
                if len(p) > 2 and p[2]:          # failed rows are retried
                    done.add((p[0], int(p[1])))
    else:
        cat.write_text("cluster\tgraphik_id\tfile\tkind\twidth\theight\tlabels\tnote\n",
                       encoding="utf-8")
    jobs = []
    for cdir in sorted(p for p in raw.iterdir() if p.is_dir()):
        for f in cdir.glob("*.bin"):
            key = (cdir.name, int(f.stem))
            # a pull may still be writing it: leave fresh files for the next run
            if key not in done and time.time() - f.stat().st_mtime > 60:
                jobs.append((cdir.name, key[1], str(f), str(out)))
    # slow DXF first, so the long tail isn't one worker grinding at the end
    jobs.sort(key=lambda j: -os.path.getsize(j[2]))
    if args.limit:
        jobs = jobs[:args.limit]
    print(f"{len(done)} already converted, {len(jobs)} to do, {args.jobs} workers", flush=True)
    t0 = time.time(); n = fails = 0
    with cat.open("a", encoding="utf-8", newline="\n") as w, \
            Pool(args.jobs, maxtasksperchild=200) as pool:
        for row in pool.imap_unordered(convert_one, jobs, chunksize=8):
            w.write("\t".join(map(str, row)) + "\n")
            n += 1; fails += not row[2]
            if n % 2000 == 0:
                w.flush()
                rate = n / max(1e-9, time.time() - t0)
                print(f"  {n}/{len(jobs)}  {fails} failed  {rate:.0f}/s  "
                      f"eta {(len(jobs) - n) / max(rate, 1e-9) / 60:.0f} min", flush=True)
    print(f"done: {n} converted this run, {fails} failed, {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
