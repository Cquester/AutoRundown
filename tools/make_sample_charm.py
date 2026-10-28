"""A stand-in for Operation CHARM's torrent data -> sample_charm/ (free, no network).

The same shape as the real `operation-charm` torrent (autorundown/charm.py):
an images LMDB, a gzip SquashFS 4.0 image holding the pages LMDB, and a
layout.json giving the torrent's file order and piece length, which
charm_fetch.LocalSwarm reads in place of a .torrent. The text is invented;
the images are tiny generated PNGs. Small pieces (4 KB), and filler cars and
images around the real ones, so a test can see one car fetch only some of
the pieces.

    python tools/make_sample_charm.py [--out sample_charm]
"""
from __future__ import annotations

import argparse
import json
import struct
import zlib
from pathlib import Path
from urllib.parse import quote

PS = 4096
PIECE = 4096
BLOCK = 8192                 # SquashFS block size of the sample (real: 128 KB)

CARS = [
    # make, year, CHARM name, article number
    ("Mercedes Benz", "1987", "190E 2.3-16 (201.034) L4-2.3L (102.983)", 46795),
    ("Mercedes Benz", "1987", "300D (124.133) L6-3.0L DSL Turbo (603.960)", 46796),
    ("Ford Truck", "2005", "F-150 4WD V8-5.4L", 51001),
]
SECTIONS = [
    ("Repair and Diagnosis", "Engine, Cooling and Exhaust", "Spark Plug", "Service and Repair"),
    ("Repair and Diagnosis", "Engine, Cooling and Exhaust", "Spark Plug", "Specifications"),
    ("Repair and Diagnosis", "Brakes and Traction Control", "Disc Brake System", "Brake Caliper", "Service and Repair"),
    ("Repair and Diagnosis", "Brakes and Traction Control", "Disc Brake System", "Brake Caliper", "Specifications"),
    ("Repair and Diagnosis", "Maintenance", "Fluids", "Engine Oil", "Capacity Specifications"),
    ("Repair and Diagnosis", "Specifications", "Mechanical Specifications", "Engine", "Torque Specifications"),
    ("Parts and Labor", "Brakes and Traction Control", "Brake Caliper", "Labor Times"),
]


def enc(s: str) -> str:
    return quote(s, safe="")


def png(seed: int) -> bytes:
    """A 4x4 grey PNG, different for each seed."""
    raw = b"".join(b"\0" + bytes([(seed * 37 + i) % 256] * 4) for i in range(4))
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


# ---------------------------------------------------------------- LMDB writer

class LmdbWriter:
    """Writes a valid read-only LMDB main database from sorted items, bottom
    up: leaves in key order with their overflow pages beside them (as CHARM's
    were written), then each branch level, then the two meta pages."""

    def __init__(self):
        self.pages: list[bytes] = [b"", b""]      # 0 and 1: meta, filled last
        self.stats = {"branch": 0, "leaf": 0, "overflow": 0}

    def _new(self, data: bytes) -> int:
        self.pages.append(data)
        return len(self.pages) - 1

    def _overflow(self, value: bytes) -> int:
        n = (16 + len(value) + PS - 1) // PS
        first = len(self.pages)
        buf = bytearray(n * PS)
        struct.pack_into("<QHHI", buf, 0, first, 0, 0x04, n)
        buf[16:16 + len(value)] = value
        for i in range(n):
            self.pages.append(bytes(buf[i * PS:(i + 1) * PS]))
        self.stats["overflow"] += n
        return first

    @staticmethod
    def _page(pgno: int, flags: int, nodes: list[bytes]) -> bytes:
        buf = bytearray(PS)
        upper = PS
        ptrs = []
        for n in nodes:
            upper -= len(n) + (len(n) & 1)
            buf[upper:upper + len(n)] = n
            ptrs.append(upper)
        lower = 16 + 2 * len(ptrs)
        assert lower <= upper, "page overfull"
        struct.pack_into("<QHHHH", buf, 0, pgno, 0, flags, lower, upper)
        for i, p in enumerate(ptrs):
            struct.pack_into("<H", buf, 16 + 2 * i, p)
        return bytes(buf)

    def build(self, items: list[tuple[bytes, bytes]]) -> bytes:
        items = sorted(items)
        level: list[tuple[bytes, int]] = []
        nodes: list[bytes] = []
        first_key = None
        used = 16

        def flush():
            nonlocal nodes, used, first_key
            if nodes:
                pg = len(self.pages)
                self.pages.append(self._page(pg, 0x02, nodes))
                self.stats["leaf"] += 1
                level.append((first_key, pg))
            nodes, used, first_key = [], 16, None

        for k, v in items:
            if len(v) > 1000:
                over = self._overflow(v)
                node = struct.pack("<HHHH", len(v) & 0xFFFF, len(v) >> 16, 0x01, len(k)) + k + struct.pack("<Q", over)
            else:
                node = struct.pack("<HHHH", len(v) & 0xFFFF, len(v) >> 16, 0, len(k)) + k + v
            if used + len(node) + 3 > PS:
                flush()
            if first_key is None:
                first_key = k
            nodes.append(node)
            used += len(node) + 3
        flush()
        depth = 1
        while len(level) > 1:
            depth += 1
            up: list[tuple[bytes, int]] = []
            i = 0
            while i < len(level):
                group, used = [], 16
                while i < len(level):
                    k, c = level[i]
                    key = b"" if not group else k
                    node = struct.pack("<HHHH", c & 0xFFFF, (c >> 16) & 0xFFFF, c >> 32, len(key)) + key
                    if used + len(node) + 3 > PS and group:
                        break
                    group.append((k, node))
                    used += len(node) + 3
                    i += 1
                pg = len(self.pages)
                self.pages.append(self._page(pg, 0x01, [n for _, n in group]))
                self.stats["branch"] += 1
                up.append((group[0][0], pg))
            level = up
        root = level[0][1]
        last = len(self.pages) - 1
        for i in (0, 1):
            buf = bytearray(PS)
            struct.pack_into("<QHHHH", buf, 0, i, 0, 0x08, 0, 0)
            struct.pack_into("<IIQQ", buf, 16, 0xBEEFC0DE, 1, 0, 2**31)
            struct.pack_into("<IHHQQQQQ", buf, 16 + 24, PS, 0, 0, 0, 0, 0, 0, 0xFFFFFFFFFFFFFFFF)
            struct.pack_into("<IHHQQQQQ", buf, 16 + 24 + 48, 0, 0, depth, self.stats["branch"],
                             self.stats["leaf"], self.stats["overflow"], len(items), root)
            struct.pack_into("<QQ", buf, 16 + 24 + 96, last, 10 + i)
            self.pages[i] = bytes(buf)
        return b"".join(self.pages)


# ---------------------------------------------------------------- SquashFS writer

def squashfs(data: bytes) -> bytes:
    """A gzip SquashFS 4.0 image holding one file (data.mdb), enough for
    charm.Squash: superblock, data blocks, inode table, directory table."""
    out = bytearray(96)
    start = len(out)
    sizes = []
    for i in range(0, len(data), BLOCK):
        blk = data[i:i + BLOCK]
        if not blk.strip(b"\0"):
            sizes.append(0)                       # sparse, like the real 2 TB file
            continue
        z = zlib.compress(blk, 6)
        if len(z) >= len(blk):
            sizes.append(len(blk) | 0x1000000)    # stored uncompressed
            out += blk
        else:
            sizes.append(len(z))
            out += z
    # the last block may be short: a real file's final block is too
    inode = struct.pack("<HHHHII", 9, 0o644, 0, 0, 0, 1)
    inode += struct.pack("<QQQIIII", start, len(data), 0, 1, 0xFFFFFFFF, 0, 0xFFFFFFFF)
    inode += struct.pack(f"<{len(sizes)}I", *sizes)
    inode += struct.pack("<HHHHII", 1, 0o755, 0, 0, 0, 2) + struct.pack("<IIHHI", 0, 2, 3, 0, 3)
    inode_table = len(out)
    for i in range(0, len(inode), 8192):
        chunk = inode[i:i + 8192]
        z = zlib.compress(chunk)
        if len(z) < len(chunk):
            out += struct.pack("<H", len(z)) + z
        else:
            out += struct.pack("<H", len(chunk) | 0x8000) + chunk
    dir_table = len(out)
    dirent = struct.pack("<III", 0, 0, 1) + struct.pack("<HhHH", 0, 0, 2, len("data.mdb") - 1) + b"data.mdb"
    out += struct.pack("<H", len(dirent) | 0x8000) + dirent
    id_table = len(out)
    out += struct.pack("<Q", 0)
    sb = struct.pack("<4sIIIIHHHHHHQQQQQQQQ", b"hsqs", 2, 0, BLOCK, 0, 1, BLOCK.bit_length() - 1,
                     0, 1, 4, 0, 0, len(out), id_table, 0xFFFFFFFFFFFFFFFF,
                     inode_table, dir_table, id_table, 0xFFFFFFFFFFFFFFFF)
    out[0:96] = sb
    return bytes(out)


# ---------------------------------------------------------------- content

def article(car: str, section: tuple, n: int, imgs: list[str]) -> bytes:
    """Invented manual text: a procedure, a spec table, links, images."""
    name = section[-2] if len(section) > 1 else section[-1]
    kind = section[-1]
    body = [f"<p>Sample text for the {car}: {name}, {kind.lower()}. Invented for testing.</p>"]
    if "Specifications" in kind or "Capacity" in kind:
        body.append(f"<table><tr><td>{name} bolt</td><td>{20 + n % 30} Nm</td></tr>"
                    f"<tr><td>{name} capacity</td><td>{4 + n % 3}.5 L</td></tr></table>")
    else:
        for i in range(1, 6):
            body.append(f"<p>{i}. Step {i} of removing the {name.lower()}; "
                        f"tighten to {10 + i * n % 40} Nm.</p>")
        body.append("<p>See also <a href='../Specifications/'>Specifications</a>.</p>")
    for im in imgs:
        body.append(f"<div class='oxe-image'><img class='big-img' src='/images{im}'></div>")
    html = "".join(body).encode()
    if n % 3 == 0:
        html += ("<p>" + "Additional invented filler text for an overflow-sized page. " * 30 + "</p>").encode()
    return html


def listing(names: list[str]) -> bytes:
    return ("<ul>" + "".join(f"<li><a href='{enc(n)}/'>{n}</a>" for n in names) + "</ul>").encode()


def build(out: Path) -> dict:
    pages: dict[bytes, bytes] = {}
    images: dict[bytes, bytes] = {}
    img_seq = 1000
    cars = list(CARS) + [(f"Make {m:02d}", str(1990 + y), f"Filler {m}-{y} L4-2.0L", 60000 + m * 10 + y)
                         for m in range(8) for y in range(4)]
    by_year: dict[tuple, list] = {}
    for make, year, car, num in cars:
        by_year.setdefault((make, year), []).append(car)
        ck = f"/{enc(make)}/{enc(year)}/{enc(car)}/"
        pages[ck.encode()] = b"SPECIAL_" + ck.encode()
        tops = sorted({s[0] for s in SECTIONS})
        pages[b"SPECIAL_" + ck.encode()] = listing(tops)
        for i, sec in enumerate(SECTIONS):
            imgs = []
            for j in range(2):
                img_seq += 7
                # filler cars share the image group, interleaved by id, as in CHARM
                imgs.append(f"/SAMPLE/grp1/{img_seq}/")
                images[imgs[-1].encode()] = png(img_seq)
            path = ck + "".join(enc(s) + "/" for s in sec)
            akey = f"{num},{1000 + i * 13}".encode()
            pages[path.encode()] = akey
            pages[akey] = article(car, sec, num + i, imgs)
            # a second path to the same article (CHARM files pages under several)
            if i == 0:
                pages[(ck + "Repair%20and%20Diagnosis/Spark%20Plug%20Index/").encode()] = akey
        for top in tops:
            tk = f"{ck}{enc(top)}/"
            pages[tk.encode()] = b"SPECIAL_" + tk.encode()
            pages[b"SPECIAL_" + tk.encode()] = listing(sorted({s[1] for s in SECTIONS if s[0] == top}))
    for (make, year), names in by_year.items():
        yk = f"/{enc(make)}/{enc(year)}/"
        pages[yk.encode()] = b"SPECIAL_" + yk.encode()
        pages[b"SPECIAL_" + yk.encode()] = listing(sorted(names))
    # an image an article names but CHARM lacks
    pages[f"{CARS[0][3]},9999".encode()] = b"<p>Missing picture: <img src='/images/SAMPLE/grp1/1/'></p>"
    pages[f"/{enc(CARS[0][0])}/{enc(CARS[0][1])}/{enc(CARS[0][2])}/Repair%20and%20Diagnosis/Odd%20Page/".encode()] = \
        f"{CARS[0][3]},9999".encode()

    pdb = LmdbWriter().build(list(pages.items()))
    idb = LmdbWriter().build(list(images.items()))
    sq = squashfs(pdb)
    out.mkdir(parents=True, exist_ok=True)
    files = [("operation-charm/README.nfo", b"Sample stand-in for Operation CHARM (invented text).\n"),
             ("operation-charm/lmdb-images/data.mdb", idb),
             ("operation-charm/lmdb-pages.sqsh", sq),
             ("operation-charm/server.js", b"// not the real server\n")]
    for rel, data in files:
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    meta = {"piece_length": PIECE, "files": [[rel, len(data)] for rel, data in files],
            "note": "layout of the sample torrent; charm_fetch.LocalSwarm reads it"}
    (out / "layout.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    total = sum(len(d) for _, d in files)
    return {"pages": len(pages), "images": len(images), "bytes": total,
            "pieces": (total + PIECE - 1) // PIECE, "cars": [c[:3] for c in CARS]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "sample_charm"))
    a = ap.parse_args()
    info = build(Path(a.out))
    print(f"sample CHARM -> {a.out}: {info['pages']} page keys, {info['images']} images, "
          f"{info['pieces']} pieces of {PIECE // 1024} KB")


if __name__ == "__main__":
    main()
