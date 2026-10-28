"""Read one car out of Operation CHARM's data, a piece at a time.

CHARM (charm.li, 1982-2013) publishes its whole site as one torrent,
`operation-charm.torrent` (757.9 GB, 45,175 pieces of 16 MB):

    operation-charm/lmdb-pages.sqsh      60 GB  SquashFS 4.0 (gzip, 128 KB
                                                blocks) holding one LMDB,
                                                data.mdb (477 GB used)
    operation-charm/lmdb-images/data.mdb 698 GB LMDB, 20.25 M images
    operation-charm/server.js ...               the site's own code

Keys, as the site's server.js reads them:
    pages:  "/Make/Year/Car/<section>/.../"  (URL-encoded)  ->  an article key
            article key "N,M" (N is one number per car)     ->  the page's HTML
            a listing page's article key is "SPECIAL_<its path>"
    images: "/<group>/<sub>/<id>/"  ->  PNG / JPEG / GIF bytes

LMDB keeps keys sorted, so one car's paths sit together, and CHARM wrote each
car in one go, so its pages sit together on disk too: the 1987 190E 2.3-16
(12,954 paths, 5,724 articles) is in about 12 pieces with the tree above it.
Its images are not: a car's image ids are spread through its whole image
group, ~570 pieces (measured 2026-10-27, docs/notes/importing.md).

Everything read here is third-party data from strangers in a swarm. Every
page number, offset and length is checked against the file before use, a
tree deeper than LMDB allows is refused, and nothing is ever run.
"""
from __future__ import annotations

import re
import struct
import zlib
from html import escape
from pathlib import Path
from urllib.parse import quote, unquote, urljoin

PAGES_FILE = "operation-charm/lmdb-pages.sqsh"
IMAGES_FILE = "operation-charm/lmdb-images/data.mdb"

PS = 4096                 # LMDB page size (both databases; checked on open)
P_BRANCH, P_LEAF, P_OVERFLOW, P_META = 0x01, 0x02, 0x04, 0x08
F_BIGDATA = 0x01
MAX_DEPTH = 16            # LMDB's own limit is 32; CHARM's trees are 9 and 5 deep
MAX_VALUE = 64 * 2**20    # no article or image is near this
SPECIAL = b"SPECIAL_"


class Missing(Exception):
    """A read needs torrent pieces that are not downloaded yet."""

    def __init__(self, pieces):
        super().__init__(sorted(set(pieces)))
        self.pieces = sorted(set(pieces))


class BadData(Exception):
    """The data does not have the shape CHARM's files have."""


class Layout:
    """Where each file of the torrent sits, in pieces."""

    def __init__(self, piece_length: int, files: list[tuple[str, int]]):
        self.piece_length = piece_length
        self.files: dict[str, tuple[int, int]] = {}
        off = 0
        for path, size in files:
            self.files[path.replace("\\", "/")] = (off, size)
            off += size
        self.total = off
        self.num_pieces = (off + piece_length - 1) // piece_length

    def pieces(self, path: str, off: int, n: int) -> range:
        start, size = self.files[path]
        if off < 0 or n < 0 or off + n > size:
            raise BadData(f"read past the end of {path}")
        a = start + off
        return range(a // self.piece_length, (a + max(n, 1) - 1) // self.piece_length + 1)


class Store:
    """The torrent's files as they are on disk under `root`, of which only
    the pieces in `have` are real (the rest of a sparse file is zeros)."""

    def __init__(self, root: Path, layout: Layout, have=()):
        self.root = Path(root)
        self.layout = layout
        self.have = set(have)
        self._fh: dict[str, object] = {}

    def read(self, path: str, off: int, n: int) -> bytes:
        need = [p for p in self.layout.pieces(path, off, n) if p not in self.have]
        if need:
            raise Missing(need)
        f = self._fh.get(path)
        if f is None:
            f = self._fh[path] = open(self.root / path, "rb")
        f.seek(off)
        data = f.read(n)
        if len(data) != n:
            raise BadData(f"short read in {path}")
        return data

    def close(self) -> None:
        for f in self._fh.values():
            f.close()
        self._fh.clear()


# ---------------------------------------------------------------- SquashFS

class Squash:
    """One regular file inside a gzip SquashFS 4.0 image, read by offset."""

    def __init__(self, store: Store, path: str):
        self.store, self.path = store, path
        image_size = store.layout.files[path][1]
        sb = store.read(path, 0, 96)
        v = struct.unpack("<4sIIIIHHHHHHQQQQQQQQ", sb)
        if v[0] != b"hsqs" or v[9] != 4 or v[5] != 1:
            raise BadData("not a gzip SquashFS 4.0 image")
        self.block_size = v[3]
        if self.block_size != 1 << v[6] or not 4096 <= self.block_size <= 2**20:
            raise BadData("bad SquashFS block size")
        inode_table, dir_table = v[15], v[16]
        if not 96 <= inode_table < dir_table <= image_size or dir_table - inode_table > 64 * 2**20:
            raise BadData("bad SquashFS inode table")
        self.image_size = image_size
        files = self._files(self._meta(inode_table, dir_table))
        if not files:
            raise BadData("no file in the SquashFS image")
        self.file = max(files, key=lambda f: f["size"])   # data.mdb (lock.mdb is empty)

    def _meta(self, start: int, end: int) -> bytes:
        out, off = bytearray(), start
        while off < end:
            (h,) = struct.unpack("<H", self.store.read(self.path, off, 2))
            size = h & 0x7FFF
            if not size or off + 2 + size > end:
                raise BadData("bad SquashFS metadata block")
            data = self.store.read(self.path, off + 2, size)
            out += data if h & 0x8000 else _inflate(data, 8192)
            off += 2 + size
        return bytes(out)

    def _files(self, s: bytes) -> list[dict]:
        files, p = [], 0
        try:
            while p < len(s):
                typ = struct.unpack_from("<H", s, p)[0]
                p += 16
                if typ == 1:                       # basic directory
                    p += 16
                elif typ == 8:                     # extended directory
                    icount = struct.unpack_from("<IIIHHII", s, p)[4]
                    p += 24
                    for _ in range(icount):
                        p += 12 + struct.unpack_from("<IIII", s, p)[3] + 1
                elif typ in (2, 9):                # basic / extended regular file
                    if typ == 2:
                        start, frag, _, size = struct.unpack_from("<IIII", s, p)
                        p += 16
                    else:
                        start, size, _, _, frag, _, _ = struct.unpack_from("<QQQIIII", s, p)
                        p += 40
                    n = size // self.block_size
                    if frag == 0xFFFFFFFF and size % self.block_size:
                        n += 1
                    if n > (len(s) - p) // 4:
                        raise BadData("SquashFS block list runs past the table")
                    sizes = struct.unpack_from(f"<{n}I", s, p)
                    p += 4 * n
                    offs, o = [], start
                    for z in sizes:
                        offs.append(o)
                        o += z & 0xFFFFFF
                    if o > self.image_size:
                        raise BadData("SquashFS blocks run past the image")
                    files.append({"size": size, "sizes": sizes, "offs": offs})
                else:
                    raise BadData(f"SquashFS inode type {typ}")
        except struct.error:
            raise BadData("truncated SquashFS inode table")
        return files

    @property
    def size(self) -> int:
        return self.file["size"]

    def block_pieces(self, off: int) -> range:
        """The torrent pieces holding the stored block that has byte `off`."""
        i = off // self.block_size
        if i >= len(self.file["sizes"]):
            raise BadData("offset past the end of data.mdb")
        return self.store.layout.pieces(self.path, self.file["offs"][i],
                                        self.file["sizes"][i] & 0xFFFFFF)

    def read(self, off: int, n: int) -> bytes:
        if off < 0 or n < 0 or off + n > self.size:
            raise BadData("read past the end of data.mdb")
        out, bs = bytearray(), self.block_size
        need: set[int] = set()
        while n > 0:
            i, within = divmod(off, bs)
            z = self.file["sizes"][i]
            if z == 0:
                blk = bytes(bs)                    # a sparse block
            else:
                try:
                    raw = self.store.read(self.path, self.file["offs"][i], z & 0xFFFFFF)
                except Missing as m:
                    need.update(m.pieces)          # gather all, then ask once
                    blk = bytes(bs)
                else:
                    blk = raw if z & 0x1000000 else _inflate(raw, bs)
            take = min(n, len(blk) - within)
            if take <= 0:
                raise BadData("short SquashFS block")
            out += blk[within:within + take]
            off += take
            n -= take
        if need:
            raise Missing(need)
        return bytes(out)


def _inflate(data: bytes, limit: int) -> bytes:
    d = zlib.decompressobj()
    try:
        out = d.decompress(data, limit + 1)
    except zlib.error:
        raise BadData("bad gzip block")
    if len(out) > limit:
        raise BadData("gzip block larger than its block size")
    return out


# ---------------------------------------------------------------- LMDB

class Lmdb:
    """A read-only LMDB main database over a `read(off, n)` function."""

    def __init__(self, read, size: int, piece_of):
        self._read, self.size, self.piece_of_page = read, size, piece_of
        head = read(0, 2 * PS)
        best = None
        for off in (0, PS):
            flags = struct.unpack_from("<H", head, off + 10)[0]
            magic, version = struct.unpack_from("<II", head, off + 16)
            if not flags & P_META or magic != 0xBEEFC0DE or version != 1:
                raise BadData("not an LMDB file")
            free_pad = struct.unpack_from("<I", head, off + 16 + 24)[0]
            if free_pad != PS:
                raise BadData(f"LMDB page size {free_pad}, expected {PS}")
            main = struct.unpack_from("<IHHQQQQQ", head, off + 16 + 24 + 48)
            last_pg, txnid = struct.unpack_from("<QQ", head, off + 16 + 24 + 96)
            if best is None or txnid > best[0]:
                best = (txnid, main, last_pg)
        _, main, self.last_pg = best
        self.depth, self.entries, self.root = main[2], main[6], main[7]
        if self.depth > MAX_DEPTH or (self.last_pg + 1) * PS > size:
            raise BadData("LMDB tree out of range")

    def page(self, pg: int) -> bytes:
        if not 2 <= pg <= self.last_pg:
            raise BadData(f"LMDB page {pg} out of range")
        return self._read(pg * PS, PS)

    def nodes(self, pg: int) -> tuple[int, list]:
        """(flags, [(key, child page | (dsize, bigdata, data))]) of one page."""
        buf = self.page(pg)
        _, _, flags, lower, upper = struct.unpack_from("<QHHHH", buf, 0)
        if not flags & (P_BRANCH | P_LEAF) or not 16 <= lower <= upper <= PS:
            raise BadData(f"LMDB page {pg} is not a tree page")
        out = []
        for i in range((lower - 16) // 2):
            (off,) = struct.unpack_from("<H", buf, 16 + 2 * i)
            if not upper <= off <= PS - 8:
                raise BadData(f"LMDB page {pg}: bad node offset")
            lo, hi, nflags, ksize = struct.unpack_from("<HHHH", buf, off)
            kend = off + 8 + ksize
            if kend > PS:
                raise BadData(f"LMDB page {pg}: key past the page")
            key = buf[off + 8:kend]
            if flags & P_BRANCH:
                out.append((key, lo | (hi << 16) | (nflags << 32)))
                continue
            dsize = lo | (hi << 16)
            if dsize > MAX_VALUE:
                raise BadData("LMDB value too large")
            if nflags & F_BIGDATA:
                if kend + 8 > PS:
                    raise BadData(f"LMDB page {pg}: bad overflow pointer")
                out.append((key, (dsize, struct.unpack_from("<Q", buf, kend)[0], None)))
            else:
                if kend + dsize > PS:
                    raise BadData(f"LMDB page {pg}: value past the page")
                out.append((key, (dsize, None, buf[kend:kend + dsize])))
        return flags, out

    def value(self, v: tuple) -> bytes:
        dsize, big, data = v
        if big is None:
            return data
        if not 2 <= big or (big * PS + 16 + dsize) > (self.last_pg + 1) * PS:
            raise BadData("LMDB overflow page out of range")
        return self._read(big * PS + 16, dsize)

    def value_pieces(self, v: tuple) -> set[int]:
        """The torrent pieces an overflow value sits in (none if inline)."""
        dsize, big, _ = v
        if big is None:
            return set()
        last = big + (16 + dsize - 1) // PS
        if last > self.last_pg:
            raise BadData("LMDB overflow page out of range")
        return set(self.piece_of_page(big)) | set(self.piece_of_page(last))

    def _child(self, ns: list, key: bytes) -> int:
        child = ns[0][1]
        for k, c in ns[1:]:
            if k <= key:
                child = c
            else:
                break
        return child

    def get(self, key: bytes):
        """The leaf entry (dsize, bigdata, data) for `key`, or None."""
        pg = self.root
        for _ in range(MAX_DEPTH + 1):
            flags, ns = self.nodes(pg)
            if flags & P_BRANCH:
                pg = self._child(ns, key)
                continue
            for k, v in ns:
                if k == key:
                    return v
            return None
        raise BadData("LMDB tree deeper than allowed")

    def scan(self, lo: bytes, hi: bytes):
        """Every (key, entry) with lo <= key < hi, in key order as far as the
        pages are on disk. Pages that aren't are skipped and, at the end,
        Missing names every piece they need -- all of one pass at once, so
        a swarm fetches them together. Call again after fetching."""
        need: set[int] = set()
        stack = [(self.root, 0)]
        while stack:
            pg, depth = stack.pop()
            if depth > MAX_DEPTH:
                raise BadData("LMDB tree deeper than allowed")
            try:
                flags, ns = self.nodes(pg)
            except Missing as m:
                need.update(m.pieces)
                continue
            if flags & P_BRANCH:
                kids = []
                for i, (k, c) in enumerate(ns):
                    nxt = ns[i + 1][0] if i + 1 < len(ns) else None
                    if nxt is not None and nxt <= lo:
                        continue
                    if i and k >= hi:
                        break
                    kids.append(c)
                stack.extend((c, depth + 1) for c in reversed(kids))
                continue
            for k, v in ns:
                if lo <= k < hi:
                    yield k, v
        if need:
            raise Missing(need)

    def children_pieces(self, lo: bytes, hi: bytes, level: int) -> set[int]:
        """Pieces of the pages `level` levels above the leaves that cover
        [lo, hi): what fetching that range will cost, read from the branch
        pages only. level 0 = the leaves themselves."""
        out: set[int] = set()
        need: set[int] = set()
        stack = [(self.root, 0)]
        while stack:
            pg, depth = stack.pop()
            if depth >= self.depth - 1 - level:
                out.update(self.piece_of_page(pg))
                continue
            try:
                flags, ns = self.nodes(pg)
            except Missing as m:
                need.update(m.pieces)
                continue
            if not flags & P_BRANCH:
                out.update(self.piece_of_page(pg))
                continue
            for i, (k, c) in enumerate(ns):
                nxt = ns[i + 1][0] if i + 1 < len(ns) else None
                if nxt is not None and nxt <= lo:
                    continue
                if i and k >= hi:
                    break
                stack.append((c, depth + 1))
        if need:
            raise Missing(need)
        return out


class Charm:
    """CHARM's two databases over a Store."""

    def __init__(self, store: Store):
        self.store = store
        self.sq = Squash(store, PAGES_FILE)
        self.pages = Lmdb(self.sq.read, self.sq.size,
                          lambda pg: self.sq.block_pieces(pg * PS))
        size = store.layout.files[IMAGES_FILE][1]
        self.images = Lmdb(lambda off, n: store.read(IMAGES_FILE, off, n), size,
                           lambda pg: store.layout.pieces(IMAGES_FILE, pg * PS, PS))

    def article(self, entry) -> bytes:
        return self.pages.value(entry)


# ---------------------------------------------------------------- names

def enc(part: str) -> str:
    """One path part the way CHARM's keys spell it ("190E 2.3-16 (201.034)"
    -> "190E%202.3-16%20%28201.034%29"; a "/" in a name is %2F)."""
    return quote(part, safe="")


def car_key(make: str, year: str, car: str) -> bytes:
    return f"/{enc(make)}/{enc(str(year))}/{enc(car)}/".encode()


def year_key(make: str, year: str) -> bytes:
    return f"/{enc(make)}/{enc(str(year))}/".encode()


_HREF = re.compile(rb"<a\s[^>]*?href=['\"]([^'\"]+)['\"]", re.I)


def listing_names(html: bytes) -> list[str]:
    """The names a CHARM listing page links to (a year page: its cars)."""
    out = []
    for m in _HREF.finditer(html):
        h = m.group(1).decode("utf-8", "replace")
        if "/" in h.rstrip("/") or h.startswith(("#", "http", "javascript")) or not h.endswith("/"):
            continue
        name = unquote(h.rstrip("/"))
        if name and name not in out:
            out.append(name)
    return out


_CODE = re.compile(r"\d{3}\.\d{3}|\d+\.\d+L|\b[LVIHW]\d{1,2}\b", re.I)


def match_car(names: list[str], model: str, engine: str) -> tuple[str | None, list[str]]:
    """The CHARM car for a catalogue (model, engine): the exact "model engine"
    name, else None and the likeliest names, best first. Never guessed: an
    inexact match is offered, not taken."""
    want = f"{model} {engine}".strip() if engine else model.strip()
    for n in names:
        if n.lower() == want.lower():
            return n, [n]
    wt = set(re.findall(r"[a-z0-9.]+", want.lower()))
    wc = {c.lower() for c in _CODE.findall(want)}

    def score(n: str) -> tuple:
        nl = n.lower()
        codes = {c.lower() for c in _CODE.findall(n)}
        return (len(codes & wc), len(wt & set(re.findall(r"[a-z0-9.]+", nl))),
                nl.startswith(model.lower().split(" ")[0]) if model else False)
    ranked = sorted(names, key=score, reverse=True)
    return None, [n for n in ranked if score(n)[1]][:12]


# ---------------------------------------------------------------- export

_IMG = re.compile(rb"""(<img\b[^>]*?\bsrc=)(['"])/images(/[^'"]+?)/?\2""", re.I)
_LINK = re.compile(rb"""(<a\b[^>]*?\bhref=)(['"])([^'"]*)\2""", re.I)
_IMAGE_KEY = re.compile(r"/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+/")
_SAFE_PART = re.compile(r"[A-Za-z0-9_.-]+")
PROPERTIES = {"service precautions", "application and id", "description and operation",
              "adjustments", "testing and inspection", "diagrams", "locations",
              "specifications", "service and repair", "parts", "technical service bulletins",
              "tools and equipment", "labor times", "exploded diagram", "service intervals",
              "fundamentals and basics", "diagnostic trouble codes", "diagnosis and testing",
              "overview", "general procedures"}


def image_refs(html: bytes) -> list[bytes]:
    """The image keys an article shows ("/IMP49Q313/euro210/151068321/")."""
    out = []
    for m in _IMG.finditer(html):
        k = m.group(3).decode("ascii", "replace") + "/"
        if _IMAGE_KEY.fullmatch(k):
            out.append(k.encode())
    return out


def image_ext(data: bytes) -> str | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:3] == b"GIF":
        return "gif"
    return None


def image_file(key: bytes, ext: str) -> str:
    """images/<group>/<sub>/<id>.<ext>, the LEMON zip's own names; parts
    are checked so a key can never name a file outside images/."""
    parts = key.decode("ascii", "replace").strip("/").split("/")
    if not parts or not all(_SAFE_PART.fullmatch(p) and p not in (".", "..") for p in parts):
        raise BadData(f"unsafe image key {key!r}")
    return "/".join(parts) + "." + ext


def display_title(parts: list[str]) -> str:
    """The page title the site shows (server.js breadcrumbsToDisplayTitle)."""
    if not parts:
        return ""
    bcs = list(parts)
    result = bcs[-1]

    def is_property(b):
        t = b[-1]
        return t.lower() in PROPERTIES or str(_num(t)) == t or len(b) == 3
    while len(bcs) > 1 and is_property(bcs):
        bcs = bcs[:-1]
        result = bcs[-1] + ": " + result
    return result


def _num(t: str):
    try:
        return int(t)
    except ValueError:
        return None


def page_html(path_parts: list[str], numbers: dict[str, int], body: bytes,
              images: dict[bytes, str], page_no: int) -> str:
    """One page the way a LEMON zip writes it, so the `lemon` importer reads
    it unchanged: breadcrumbs Home >> Make >> Year >> Car >> sections, then
    <div class="main"><h1>title</h1> and the article, with its images and
    links pointed at the folder's own files."""
    crumbs = ['<a class="breadcrumb-part" href="../pages/1.html">Home</a>']
    for i, part in enumerate(path_parts):
        key = "/" + "/".join(enc(p) for p in path_parts[:i + 1]) + "/"
        n = numbers.get(key)
        href = f"../pages/{n}.html" if n else "../pages/1.html"
        crumbs.append(f'<a class="breadcrumb-part" href="{href}">{escape(part)}</a>')
    here = "/" + "/".join(enc(p) for p in path_parts) + "/"

    def img(m):
        key = m.group(3) + b"/"
        f = images.get(key)
        src = f"../images/{f}" if f else "../404.html"
        return m.group(1) + m.group(2) + src.encode() + m.group(2)

    def link(m):
        h = m.group(3).decode("utf-8", "replace")
        if not h or h.startswith(("#", "javascript", "http:", "https:", "mailto:")):
            return m.group(0)
        target, _, frag = urljoin(here, h).partition("#")
        if not target.endswith("/"):
            target += "/"
        n = numbers.get(target)
        new = f"../pages/{n}.html" if n else "../404.html"
        if frag:
            new += "#" + frag
        return m.group(1) + m.group(2) + escape(new).encode() + m.group(2)

    text = _IMG.sub(img, body)
    text = _LINK.sub(link, text)
    title = display_title(path_parts)
    return ("<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
            f"<title>{escape(title)}</title></head><body><div class=\"theme-colors header\">"
            + " &gt;&gt; ".join(crumbs) + "</div><div class=\"main\">"
            f"<h1>{escape(title)}</h1>" + text.decode("utf-8", "replace")
            + f"</div><!-- CHARM page {page_no} --></body></html>")
