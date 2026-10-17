"""WIS value tables, read from the document's own layout, one car's column.

The index holds WIS pages as reading text, and a WIS value table flattens
badly there: the cells of a row land on two lines, so a value sits next to
the *next* row's name ("Nm 20 - Oil level sensor on oil pan top part": the
20 Nm is the screw plug's), and the columns -- one per engine or model --
run together ("Liters 8.0 8.0 7.5"). The Torque specs and Capacities tabs
showed exactly that (2026-10-16, checked on the live W210 index).

The SIDS layout in `bodies.pack` keeps the table: a header row ("Number |
Designation | | ENGINE 112 (except 112.915/...) | ENGINE 112.916/...") and a
row per value ("BA18.40-P-1001-01B | Screw plug on crankcase | Nm | 20 |
-"). `rows()` pairs each value with its column's label and keeps only the
columns whose label fits the chosen car (`label_fits`); a value table it
can't pair cell for cell is left out, never guessed. A dash means "does not
apply" and is never a value.

Labels are English or German ("MOTOR 104.945/995 im TYP 210", "TYP 210.003/
004 außer CODE 450"), list codes in WIS shorthand ("112.910/911/ 912" =
112.910, 112.911, 112.912; "210.006/.../28" = 210.28x) and can carry
conditions the car box does not know (a code, a chassis number, a date):
those columns are kept with their label as the condition.
"""
from __future__ import annotations

import functools
import html
import re
from pathlib import Path

from . import wis_original as wo

# a row's unit, at the end of the cell before the values
_UNITS = {"nm": "torque", "n·m": "torque",
          "liter": "capacity", "liters": "capacity", "litre": "capacity", "litres": "capacity",
          "l": "capacity", "ml": "capacity", "cm³": "capacity", "cm3": "capacity",
          "ccm": "capacity", "g": "capacity", "kg": "capacity",
          "sheet": "sheet", "°": "torque"}
# "Stage 2 ( °": WIS prints a turn as its own row, unit in brackets
_UNIT_AT_END = re.compile(r"(?:^|\s|\()(Nm|N·m|Liters?|Litres?|l|ml|cm³|cm3|ccm|g|kg|Sheet|°)\s*\)?\s*$", re.I)
_DASH = re.compile(r"^[-–—]+$")
# one value inside a cell that holds several ("14 14"): a number, a range
_VALUE_TOKEN = re.compile(r"[-–—]|\d+(?:[.,]\d+)?(?:\s*(?:\.\.\.|to|-)\s*\d+(?:[.,]\d+)?)?"
                          r"(?:\s*\+\s*\d+\s*°)*")
_DOCNR = re.compile(r"^[A-Z]{2}\d{2}\.\d{2}-[A-Z]-\d{4}")


def _text(cell) -> str:
    raw = " ".join(p for p, _ in cell.paras).replace("<br>", " / ")
    raw = re.sub(r'<span class="ico[^"]*"[^>]*>.*?</span>', " ", raw)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw)).split()).strip(" /")


def _caption(text: str) -> str:
    return text.lstrip("▸ ").strip()


def _tables(raw: str) -> list[dict]:
    """[{caption, labels: [str], rows: [[cell text]]}] for every value table."""
    out, caption, cur = [], "", None

    def walk(box):
        nonlocal caption, cur
        for c in box.children:
            if c[0] == "box":
                walk(c[1])
                continue
            if c[0] == "p":
                t = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", c[1])).split())
                if t.startswith("▸") or (t and len(t) < 80 and cur is None):
                    caption = _caption(t)
                continue
            if c[0] != "row":
                continue
            cells = [_text(x) for x in c[1]]
            if len(cells) == 1 and cells[0].startswith("▸"):
                caption, cur = _caption(cells[0]), None
                continue
            if len(cells) >= 2 and cells[0] == "Number" and cells[1].startswith("Designation"):
                # column labels: the header's text after "Designation"; WIS
                # sometimes tabs the first label into the Designation cell
                first = re.sub(r"^Designation\s*/?\s*", "", cells[1]).strip(" /")
                labels = [t for t in [first, *cells[2:]] if t and t != "/"]
                cur = {"caption": caption, "labels": labels, "rows": []}
                out.append(cur)
                continue
            if cur is not None and any(cells):
                cur["rows"].append(cells)
    walk(wo.layout(raw, {}))
    return out


@functools.lru_cache(maxsize=512)
def _doc_tables(corpus: str, dok_id: int) -> tuple:
    try:
        raw = wo.sids_text(wo.PackIndex(Path(corpus)).body(dok_id))
    except (wo.NotAvailable, OSError, ValueError, EOFError):
        return ()
    try:
        return tuple(_tables(raw))
    except Exception:          # a layout this reader doesn't know: no table, never a crash
        return ()


def has_tables(corpus: Path, dok_id: int) -> bool:
    """Whether the document has a value table this reader can read (a
    "Repair materials" list of order numbers is a table, not a value one)."""
    return any(_UNIT_AT_END.search(c) for t in _doc_tables(str(corpus), int(dok_id))
               for r in t["rows"] for c in r[1:])


def has_layout(corpus: Path, dok_id: int) -> bool:
    try:
        wo.sids_text(wo.PackIndex(Path(corpus)).body(dok_id))
        return True
    except (wo.NotAvailable, OSError, ValueError, EOFError):
        return False


# -- does a column label fit this car? ---------------------------------------

_ALT_SPLIT = re.compile(r"[,;]\s*(?=(?:ENGINES?|MOTOR(?:EN)?|MODELS?|TYP|Engines?|Models?)\b)")
_TOKEN = re.compile(
    r"(?P<kw>\b(?:ENGINES?|MOTOR(?:EN)?|MODELS?|TYPE?|TRANSMISSIONS?|GETRIEBE|CODE|"
    r"except|außer|ausser|excluding|without|ohne|with|mit|in|im|up to|bis|as of|ab|from)\b)"
    r"|(?P<dot>\d{3}\.[\d#]{1,3})|(?P<num>\d+)|(?P<sep>[/,()])", re.I)
_ENGINE_KW = {"engine", "engines", "motor", "motoren"}
_MODEL_KW = {"model", "models", "typ", "type"}
_NEG_KW = {"except", "außer", "ausser", "excluding", "without", "ohne"}
_COND_KW = {"up to", "bis", "as of", "ab", "from", "code", "transmission", "transmissions",
            "getriebe", "with", "mit"}


def _parse_label(alt: str) -> dict:
    """{"engine": ([pos], [neg]), "model": ([pos], [neg]), "cond": bool}"""
    out = {"engine": ([], []), "model": ([], []), "cond": False}
    kind, neg, fam, last_sep = None, False, None, ""
    for m in _TOKEN.finditer(alt):
        kw, dot, num, sep = m.group("kw"), m.group("dot"), m.group("num"), m.group("sep")
        if kw:
            k = kw.lower()
            if k in _ENGINE_KW:
                kind, neg, fam = "engine", False if kind != "engine" else neg, None
            elif k in _MODEL_KW:
                kind, neg, fam = "model", False if kind != "model" else neg, None
            elif k in _NEG_KW:
                neg = True
            elif k in _COND_KW:
                if k in ("with", "mit"):
                    # "with CODE M005": a condition; "with engine 113": a constraint
                    nxt = alt[m.end():].lstrip().lower()
                    if nxt.startswith(("engine", "motor", "model", "typ")):
                        continue
                if k in ("code",) and neg:
                    kind = "code"     # "except CODE 450": a code we don't know
                    out["cond"] = True
                    continue
                kind = "cond"
                out["cond"] = True
            elif k in ("in", "im"):
                neg = False
            last_sep = ""
            continue
        if sep:
            last_sep = sep
            if sep == ")":
                neg = False
            continue
        if kind not in ("engine", "model"):
            continue
        bucket = out[kind][1 if neg else 0]
        if dot:
            fam = dot[:3]
            bucket.append(dot.replace("#", ""))
        elif num:
            if len(num) >= 4:          # a chassis or engine serial number
                out["cond"] = True
                continue
            if last_sep == "/" and fam:
                bucket.append(f"{fam}.{num}")          # "112.910/911" -> 112.911
            elif len(num) == 3:
                fam = num
                bucket.append(num)                     # a whole family / series
        last_sep = ""
    return out


def _hit(code: str, wanted: str) -> bool:
    """Does the car's exact code `code` ("112.941") fall under `wanted`
    ("112", "112.941", "210.28" -> 210.28x)?"""
    return code == wanted or code.startswith(wanted + ".") or (
        "." in wanted and code.startswith(wanted))


def label_fits(label: str, codes: dict | None) -> str | None:
    """"yes", "maybe" (fits but under a condition the car box doesn't know),
    "no", or None (the label names no engine or model)."""
    if not codes:
        return None
    best = "unset"
    rank = {"unset": -1, "no": 0, None: 1, "maybe": 2, "yes": 3}
    for alt in _ALT_SPLIT.split(label) or [label]:
        p = _parse_label(alt)
        verdict = None
        for kind, mine, fams in (("engine", codes.get("engines") or set(), codes.get("families") or set()),
                                 ("model", codes.get("models") or set(), codes.get("series") or set())):
            pos, neg = p[kind]
            if not pos and not neg:
                continue
            if mine:
                ok = (not pos or any(_hit(c, w) for c in mine for w in pos)) \
                    and not all(any(_hit(c, w) for w in neg) for c in mine)
                v = "yes" if ok else "no"
            else:
                # only the family / series is known: an exact code is a maybe
                ok = not pos or any(w[:3] in fams for w in pos)
                v = ("yes" if all(len(w) == 3 for w in pos) and not neg else "maybe") if ok else "no"
            if v == "no":
                verdict = "no"
                break
            verdict = "maybe" if "maybe" in (v, verdict) else v
        if verdict == "yes" and p["cond"]:
            verdict = "maybe"
        if verdict is None and p["cond"]:
            verdict = None
        if rank[verdict] > rank[best]:
            best = verdict
    return None if best == "unset" else best


def _note(label: str) -> str:
    """What a label says besides codes: "(shortened maintenance interval)"."""
    t = re.sub(r"\b(?:ENGINES?|MOTOR(?:EN)?|MODELS?|TYP|Engine|Model)\b\s*[\d./#, ]*", " ", label)
    t = re.sub(r"\b(?:except|außer)\s*[\d./, ]+", " ", t)
    t = " ".join(t.replace("()", " ").split()).strip(" ,;/")
    return t


_LEAD_KW = re.compile(r"^(ENGINES?|MOTOR(?:EN)?|MODELS?|TYP|Engines?|Models?)\b")


def _split_labels(labels: list[str]) -> list[str]:
    """One header cell can hold two columns' labels ("Engine 112.910/...
    Engine 113.940/..."): split it where its own leading keyword starts
    again -- not after "in"/"with" ("ENGINE 112 in MODEL 210" is one label)."""
    out = []
    for label in labels:
        m = _LEAD_KW.match(label)
        if not m:
            out.append(label)
            continue
        kw = m.group(1).rstrip("Ss").rstrip("EN") if m.group(1).upper().startswith("MOTOR") else m.group(1)
        kw = re.escape(re.sub(r"[sS]$", "", kw))
        parts = re.split(rf"(?<!\bin )(?<!\bim )(?<!\bwith )(?<!\bmit )(?=\b{kw}[sS]?\b)", label)
        out.extend(p.strip(" /,") for p in parts if p.strip(" /,"))
    return out


def _split_values(cells: list[str], n: int) -> list[str] | None:
    if len(cells) == n:
        return cells
    toks = [t.strip() for c in cells for t in _VALUE_TOKEN.findall(c) if t.strip()]
    return toks if len(toks) == n else None


def rows(corpus: Path, dok_id: int, kind: str, codes: dict | None,
         sheet_title=None, strict: bool = False) -> list[dict]:
    """The car's values of `kind` ("torque" / "capacity") in one document:
    [{"name", "value", "condition"}]. "capacity" rows carry the oil
    specification sheets of the same table ("MB 229.5") in "spec" when
    `sheet_title(docnr)` names them. `strict`: only columns that name this
    car (a sheet from the general pool: one naming no car is not ours)."""
    def ok(label):
        v = label_fits(label or "", codes)
        return v in ("yes", "maybe") if strict else v != "no"
    out = []
    for t in _doc_tables(str(corpus), int(dok_id)):
        labels = t["labels"]
        name = ""
        specs: list[str] = []
        table_rows = []
        for cells in t["rows"]:
            body = cells[1:]
            unit_at = None
            for i, c in enumerate(body):
                if _UNIT_AT_END.search(c):
                    unit_at = i
            if unit_at is None:
                continue
            m = _UNIT_AT_END.search(body[unit_at])
            unit = m.group(1)
            what = _UNITS.get(unit.lower())
            mids = [c for c in body[:unit_at] if c] + [body[unit_at][:m.start()].strip(" (")]
            mids = [c for c in mids if c]
            if body[0]:
                name = body[0]
            elif not name and mids:
                name = mids[0]
            sub = " · ".join(c for c in mids if c != name)
            vals = [c for c in body[unit_at + 1:]]
            if not labels:
                vals = [" ".join(v for v in vals if v)] if any(vals) else []
                cols = [(None, v) for v in vals]
            else:
                use = labels
                split = _split_values(vals, len(use))
                more = _split_labels(labels)
                if len(more) > len(labels):
                    # "14 14" under "Engine 112... Engine 113...": one value each
                    split2 = _split_values([" ".join(vals)], len(more))
                    if split2 is not None:
                        use, split = more, split2
                if split is None:
                    continue           # can't pair values with columns: leave out
                cols = list(zip(use, split))
            table_rows.append((what, name, sub, unit, cols))
        for what, name, sub, unit, cols in table_rows:
            if what == "sheet" and kind == "capacity" and sheet_title:
                for label, v in cols:
                    if v and not _DASH.match(v) and ok(label):
                        s = sheet_title(v)
                        if s and s not in specs:
                            specs.append(s)
        for what, name, sub, unit, cols in table_rows:
            if what != kind:
                continue
            fits = [(label, v.removeprefix("Y ").strip()) for label, v in cols
                    if v and not _DASH.match(v.strip())
                    and ok(label)]
            if not fits:
                continue
            verdicts = {label_fits(label or "", codes) for label, _ in fits}
            distinct = list(dict.fromkeys(v for _, v in fits))
            sep = "" if unit == "°" else " "
            if len(distinct) == 1:
                value = f"{distinct[0]}{sep}{unit}"
                cond = "" if verdicts & {"yes", None} else "; ".join(l for l, _ in fits if l)
                if codes is None and len(fits) > 1 and len({l for l, _ in cols}) > 1:
                    cond = ""
            else:
                parts = []
                for label, v in fits:
                    note = _note(label or "") if codes else (label or "")
                    parts.append(f"{v}{sep}{unit}" + (f" ({note})" if note else ""))
                value = "; ".join(dict.fromkeys(parts))
                cond = ""
            full = name + (f" · {sub}" if sub and sub != name else "")
            row = {"name": full, "value": value, "condition": cond,
                   "table": t["caption"]}
            if kind == "capacity" and specs and re.search(r"engine oil|motor ?öl", full, re.I):
                row["spec"] = ", ".join(specs)
            out.append(row)
    return out


def sheet_name(title: str) -> str:
    """"Multigrade engine oils (specification 229.1)" -> "MB 229.1"."""
    m = re.search(r"specification\s+(\d{3}\.\d+)", title or "", re.I)
    return f"MB {m.group(1)}" if m else ""


def is_docnr(text: str) -> bool:
    return bool(_DOCNR.match(text or ""))
