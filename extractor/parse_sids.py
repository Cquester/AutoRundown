#!/usr/bin/env python3
"""Phase 4 SIDS parser -> normalized AutoRundown records.

Input : autorundown_out/phase4_proof/{metadata.tsv, figures.tsv, blobs/<DokID>.gz}
Output: autorundown_out/phase4_proof/parsed/<DokID>.txt   (human-readable body)
        autorundown_out/phase4_proof/parsed/<DokID>.json  (structured record)
        autorundown_out/phase4_proof/records.jsonl        (all records, one/line)
        autorundown_out/phase4_proof/PARSE_SUMMARY.tsv

SIDS = Daimler "ASCII-SIDS2.00" page-description format. Records end in ';',
fields are comma-separated, quoted strings hold the text. Two text carriers:
  T"..."               a text run
  <font>,<sz>,..,"..." a positioned text field (font/size codes lead the record)
We classify every quoted string by content: structural marker ('<...'),
cross-reference docnr, vehicle applicability note, icon glyph, or body text.
"""
import gzip, io, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wis_paths import out_root  # noqa: E402  (data stays outside the repo)

# the phase-4 proof folder main() works in; importing this module (parse.py,
# reparse_bodies.py) reads nothing from it and creates nothing
BASE = os.environ.get("AR_PROOF") or os.path.join(str(out_root()), "phase4_proof")
OUTP = os.path.join(BASE, "parsed")

# WIS document number, e.g. SI05.00-P-0002A, AR20.00-P-0100-01, BT05.20-P-0002-01A
DOCNR_CORE = r'[A-Z]{2}[0-9]{2}\.[0-9]{2}-[A-Z]{1,2}-[0-9]{3,4}[A-Z]?(?:-[0-9]{2}[A-Z]?)?'
DOCNR_RE = re.compile(r'^' + DOCNR_CORE + r'$')
DOCNR_ANY = re.compile(DOCNR_CORE)          # embedded inside a run
# applicability annotations: "611.### ## as of 359084 in MODELS 202, 203, 210"
APPLIC_RE = re.compile(r'(\bMODELS?\b|\bas of\b|[0-9]{3}\.#|\bENGINE\b|\bup to\b)', re.I)
# SIDS font-definition trailer entries: "TIMES 10", "MODERN 12 BOLD", "LOGO1C0 12"
FONTDEF_RE = re.compile(r'^(TIMES|MODERN|SWISS|COURIER|HELV\w*|LOGO\w*|SYMBOL)\b[0-9A-Z ]*$')
# inline graphic placeholder: "<id>|17.959436|13.726631|TIFF" (id|width|height|format)
GFX_TOKEN_RE = re.compile(r'\S*\|\d[\d.]*\|\d[\d.]*\|[A-Za-z]{2,5}')
# the same token with its graphic number captured: ".P.w1518102401|8.5|6.3|TIFF"
GFX_PLACE_RE = re.compile(r'\.P\.([A-Za-z0-9]+)\|\d[\d.]*\|\d[\d.]*\|[A-Za-z]{2,5}')
# a printed figure number: "W15.18-1024-01", "P01.30-0230-05", and the older
# style with no dotted group, "P01-5220-13" (34,294 in the English corpus)
FIG_NO = r'[A-Z][0-9]{2}(?:\.[0-9]{2})?-[0-9]{4}-[0-9]{2}'
FIG_CAPTION_RE = re.compile(r'\b' + FIG_NO + r'\b ?')
# ...unless the text refers to it: "(See Fig. D27-0007-13 page 8)", "wiring
# diagrams W54-2328-71, W54-2329-71 and W54-2349-71", "see also tables
# P15-0397-13 and P15-0398-13". Same line only; ~50 kept, the rest are captions.
FIG_REF_BEFORE_RE = re.compile(
    r'(?:\bfig(?:ure)?s?\.?|\bdiagrams?|\btables?|\bsee|\(|\b' + FIG_NO + r'(?:,| and| or))[ \t]*$',
    re.I)


def strip_fig_captions(s, repl=''):
    """Remove printed figure numbers, keeping the ones the text refers to."""
    return FIG_CAPTION_RE.sub(
        lambda m: m.group(0) if FIG_REF_BEFORE_RE.search(s, max(0, m.start() - 24), m.start())
        else repl, s)
# a run that is only a step number: "1", "2.1", "12"
STEP_NO_RE = re.compile(r'^\d{1,2}(\.\d{1,2})?$')
# inline SIDS control token inside a quoted run, e.g. ".T.1;"
INLINE_CTL_RE = re.compile(r'\.T\.[0-9]+;')
# standalone label runs that belong to the applicability block, not the body
LABEL_DROP = {"ENGINE", "ENGINES", "MODEL", "MODELS"}


def clean_run(s):
    """Strip inline SIDS control tokens and embedded docnr fragments from a run."""
    s = INLINE_CTL_RE.sub(' ', s)
    s = DOCNR_ANY.sub(' ', s)          # docnrs are captured separately as xrefs
    s = s.replace(';', ' ')
    # a lone bold/style glyph 'k' or 'c' glued before a capitalized word
    s = re.sub(r'(^|[\s.,;:)])[kc](?=[A-Z][a-z])', r'\1', s)
    return s


def split_records(text):
    """Split SIDS into records on ';' honoring double-quoted strings."""
    recs, buf, inq, i, n = [], [], False, 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            # doubled "" inside a quote is an escaped quote
            if inq and i + 1 < n and text[i + 1] == '"':
                buf.append('"'); i += 2; continue
            inq = not inq; buf.append(c); i += 1; continue
        if c == ';' and not inq:
            recs.append(''.join(buf)); buf = []; i += 1; continue
        buf.append(c); i += 1
    if buf:
        recs.append(''.join(buf))
    return recs


def split_fields(rec):
    """Comma-split a record honoring quotes; return (fields, quoted_strings)."""
    fields, quoted, buf, inq, i, n = [], [], [], False, 0, len(rec)
    cur_q = None
    while i < n:
        c = rec[i]
        if c == '"':
            if inq and i + 1 < n and rec[i + 1] == '"':
                cur_q.append('"'); buf.append('"'); i += 2; continue
            if not inq:
                inq = True; cur_q = []
            else:
                inq = False; quoted.append(''.join(cur_q)); cur_q = None
            buf.append(c); i += 1; continue
        if c == ',' and not inq:
            fields.append(''.join(buf)); buf = []; i += 1; continue
        if inq:
            cur_q.append(c)
        buf.append(c); i += 1
    fields.append(''.join(buf))
    return fields, quoted


def classify(q):
    q_str = q.strip()
    if q_str == '':
        return ('blank', q)
    if q_str.upper() in LABEL_DROP:
        return ('text', q)          # bare label -> handled by pending-label logic
    if q_str.startswith('<'):
        return ('marker', q_str)
    if DOCNR_RE.match(q_str):
        return ('xref', q_str)
    if APPLIC_RE.search(q_str) and (len(q_str) < 90 or APPLIC_RE.search(q_str)):
        # applicability lines are short annotations; long prose with "as of"
        # is rare but we still tag - refined below by also requiring a code/MODELS
        if re.search(r'\bMODELS?\b', q_str) or re.search(r'[0-9]{3}\.#', q_str):
            return ('applic', q_str)
    return ('text', q)


def unlzw(data):
    """Decompress Unix compress (.Z) LZW data, after Mark Adler's unlzw.c.

    Replaces a port of ncompress' decompress() that lost its bit position
    after the first code-width change (9 -> 10 bits): it garbled the tail of
    10 of the 103 LZW XML bodies (e.g. DokID 4086425 turns to junk after
    ~1.5 KB) and hung on one LZW DXF graphic. Same decoder as
    convert_images.unlzw_z.
    """
    from convert_images import unlzw_z
    if len(data) < 3 or data[0] != 0x1F or data[1] != 0x9D:
        raise ValueError("not .Z data")
    return unlzw_z(bytes(data))


def decompress_body(blob):
    """WIS bodies: gzip (1f 8b)->SIDS, Unix-compress LZW (1f 9d)->XML, or
    uncompressed (RTF graphic stubs, plain XML/text). Return raw bytes."""
    if len(blob) >= 2 and blob[0] == 0x1F and blob[1] == 0x8B:
        return gzip.decompress(blob)
    if len(blob) >= 2 and blob[0] == 0x1F and blob[1] == 0x9D:
        return unlzw(blob)
    return bytes(blob)                            # uncompressed


# graphic reference inside an RTF stub: "&V& pe2819d200099c|70.72|11.80|DXF;;"
RTF_GFX_RE = re.compile(r'&[A-Za-z]&\s*([^\s|]+)\|([\d.]+)\|([\d.]+)\|(\w+)')


def parse_rtf_body(text):
    """Tiny RTF stubs are figure-only docs pointing at a DXF/TIFF/PNG graphic.
    Returns (None, body, xrefs, applic, image_refs)."""
    image_refs = []
    for m in RTF_GFX_RE.finditer(text):
        gid, w, h, fmt = m.groups()
        image_refs.append(f"{gid} ({fmt} {w}x{h})")
    # strip RTF controls/groups; whatever readable text is left becomes body
    t = re.sub(r'\\[a-zA-Z]+-?\d* ?', ' ', text)
    t = t.replace('{', ' ').replace('}', ' ')
    t = RTF_GFX_RE.sub(' ', t)
    t = re.sub(r'&[A-Za-z]&', ' ', t)
    t = re.sub(r'\s+', ' ', t).strip(' ;')
    body = t if len(t) > 3 else ''
    return None, body, [], [], image_refs


def _localname(tag):
    return tag.rsplit('}', 1)[-1] if '}' in tag else tag


def parse_xml_body(raw_text):
    """Daimler maintenance-checklist XML (AP-KD-MP / ChecklistService).
    Returns (title, body, xrefs, applic, image_refs)."""
    import xml.etree.ElementTree as ET
    xrefs, applic, image_refs, body_lines = [], [], [], []
    title = None
    BODY_TAGS = {'Pruefumfang', 'Pruefkriterium', 'Pruefpunkttext', 'Hinweis',
                 'Text', 'Bezeichnung', 'Arbeitstext', 'Beschreibung', 'Sollwert',
                 'Massnahme', 'Frage', 'Antwort'}
    try:
        root = ET.fromstring(raw_text)
        for el in root.iter():
            ln = _localname(el.tag)
            txt = (el.text or '').strip()
            if ln == 'DokTitel' and txt:
                title = txt
            elif ln == 'GenerierteGueltigkeit':
                g = re.sub(r'\s+', ' ', ' '.join(t.strip() for t in el.itertext() if t.strip())).strip()
                if g and g not in applic:
                    applic.append(g)
            elif ln == 'Navigationslink' and txt and txt not in xrefs:
                xrefs.append(txt)
            elif ln == 'Bild':
                for k, v in el.attrib.items():
                    if _localname(k) == 'bildnummer' and v:
                        image_refs.append(v)
            elif ln in BODY_TAGS and txt:
                body_lines.append(txt)
    except Exception:
        body = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', raw_text)).strip()
        for m in DOCNR_ANY.finditer(raw_text):
            if m.group(0) not in xrefs:
                xrefs.append(m.group(0))
        return title, body, xrefs, applic, image_refs
    for m in DOCNR_ANY.finditer(raw_text):      # catch any other docnr refs
        if m.group(0) not in xrefs:
            xrefs.append(m.group(0))
    if not body_lines and title:
        body_lines = [title]
    return title, '\n'.join(body_lines).strip(), xrefs, applic, image_refs


def parse_doc(dokid, blob_bytes, title=""):
    raw = decompress_body(blob_bytes)
    if raw[:3] == b'\xef\xbb\xbf':     # strip UTF-8 BOM for detection/decoding
        raw = raw[3:]
    # WIS bodies: SIDS page-markup, Daimler checklist XML, or RTF graphic stubs.
    head = raw[:64].lstrip()
    if head[:5] == b'{\\rtf':
        t, body, xrefs, applic, img = parse_rtf_body(raw.decode('latin-1'))
        return ('rtf', None, body, xrefs, applic, img)
    if head[:5] == b'<?xml' or head[:1] == b'<':
        t, body, xrefs, applic, img = parse_xml_body(raw.decode('utf-8', 'replace'))
        return ('xml', None, body, xrefs, applic, img)
    text = raw.decode('latin-1')       # SIDS is 8-bit; latin-1 is lossless
    recs = split_records(text)

    body_parts = []          # list of ('text'|'break', value)
    xrefs, applic, markers = [], [], []
    header = None
    pending_label = None     # a bare MODEL/ENGINE label primes the next code run
    CODE_RE = re.compile(r'^[0-9][0-9 ,./#-]*$')
    pending_fig = []         # [graphic nr, step text seen since?] awaiting their step's end

    def flush_figure(only_after_text=False):
        keep = []
        for nr, seen in pending_fig:
            if only_after_text and not seen:
                keep.append([nr, seen])   # placed right before the step now starting
                continue
            body_parts.append(('break', ''))
            body_parts.append(('text', f'[figure: {nr}]'))
            body_parts.append(('break', ''))
        pending_fig[:] = keep

    for idx, rec in enumerate(recs):
        rec_s = rec.strip()
        if not rec_s:
            continue
        if idx == 0 and rec_s.startswith('ASCII-SIDS'):
            header = rec_s
            continue
        fields, quoted = split_fields(rec)
        lead = fields[0].strip() if fields else ''

        # second numeric field == 185 marks the symbol/icon font -> glyphs, drop
        icon_font = False
        if len(fields) > 1:
            f1 = fields[1].strip()
            if f1 == '185':
                icon_font = True

        # empty text run 'T' (no quote) => paragraph/line break
        if lead == 'T' and not quoted:
            body_parts.append(('break', ''))
            continue
        if lead in ('L', 'W', 'V', 'P') and not quoted:
            # layout/line records with no text: soft break only for L
            if lead == 'L':
                body_parts.append(('break', ''))
            continue

        for q in quoted:
            kind, val = classify(q)
            # inline graphic: ".P.w1518102401|8.506|6.389|TIFF" sits where the
            # figure is laid out -- just before the step it illustrates. Keep
            # its position as a marker (resolved to a file by apply_images.py)
            # and emit it after that step, so the page reads step, then figure.
            gm = GFX_PLACE_RE.search(q) if kind == 'text' else None
            if gm:
                flush_figure()
                pending_fig.append([gm.group(1).lower(), False])
                continue
            if kind == 'text' and STEP_NO_RE.match(val.strip()) and pending_fig \
                    and body_parts and body_parts[-1][0] == 'break':
                # a new step starts: figures whose step text has already run
                # end here; one with no text since belongs to this new step
                flush_figure(only_after_text=True)
            elif kind == 'text' and val.strip():
                for f in pending_fig:
                    f[1] = True
            if kind == 'text':
                vs = val.strip()
                if icon_font and len(vs) <= 2:
                    continue                      # icon glyph, not prose
                if len(vs) == 1 and vs.isalpha():
                    continue                      # symbol-font bullet glyph (k/c/p/i)
                if FONTDEF_RE.match(vs):
                    continue                      # SIDS font-definition trailer
                if vs.upper() in LABEL_DROP:
                    pending_label = vs.upper()    # primes next code run as applic
                    continue
                if pending_label:
                    if CODE_RE.match(vs):         # "408, 418, 427, 437" after MODEL
                        note = pending_label + " " + vs
                        if note not in applic:
                            applic.append(note)
                        pending_label = None
                        continue
                    pending_label = None          # label wasn't followed by codes
                body_parts.append(('text', clean_run(val)))
            elif kind == 'xref':
                if val not in xrefs:
                    xrefs.append(val)
            elif kind == 'applic':
                if val not in applic:
                    applic.append(val)
            elif kind == 'marker':
                markers.append(val)
            # blank -> ignore

    flush_figure()           # the last step's figure

    # global scan: catch docnrs embedded inside runs (mini reference tables)
    for m in DOCNR_ANY.finditer(text):
        d = m.group(0)
        if d not in xrefs:
            xrefs.append(d)

    def norm(s):
        s = re.sub(r'\s+', ' ', s)
        s = re.sub(r'\s+([.,;:)])', r'\1', s)   # no space before punctuation
        s = re.sub(r'(\()\s+', r'\1', s)         # no space after '('
        return s.strip()

    # assemble readable body: join runs on a line with spaces, break -> blank line
    lines, cur = [], []
    for kind, val in body_parts:
        if kind == 'break':
            if cur:
                lines.append(norm(' '.join(cur))); cur = []
            elif lines and lines[-1] != '':
                lines.append('')
        else:
            cur.append(val)
    if cur:
        lines.append(norm(' '.join(cur)))
    # squeeze multiple blanks
    body_lines, prev_blank = [], False
    for ln in lines:
        if ln == '':
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False
        body_lines.append(ln)
    # drop a leading line that just repeats the metadata title (heading echo)
    t = title.strip()
    while body_lines and t and body_lines[0].startswith(t[:40]):
        body_lines.pop(0)
    while body_lines and body_lines[0] == '':
        body_lines.pop(0)
    body = '\n'.join(body_lines).strip()
    body = GFX_TOKEN_RE.sub('', body)                 # drop inline graphic tokens
    # figure captions ("W15.18-1011-01"): the figure itself is now placed by its
    # [figure: ...] marker, so its printed number is only clutter in the step
    body = strip_fig_captions(body)
    body = re.sub(r'[ \t]{2,}', ' ', body).strip()
    return ('sids', header, body, xrefs, applic, [])


def bits_to_indices(bitstr):
    return [i for i, ch in enumerate(bitstr) if ch == '1']


def main():
    os.makedirs(OUTP, exist_ok=True)
    # load metadata
    meta = {}
    with open(os.path.join(BASE, "metadata.tsv"), encoding="utf-8") as f:
        hdr = f.readline().rstrip("\n").split("\t")
        for line in f:
            p = line.rstrip("\n").split("\t")
            row = dict(zip(hdr, p))
            meta[row["DokID"]] = row
    # load figures
    figs = {}
    with open(os.path.join(BASE, "figures.tsv"), encoding="utf-8") as f:
        fhdr = f.readline().rstrip("\n").split("\t")
        for line in f:
            p = line.rstrip("\n").split("\t")
            r = dict(zip(fhdr, p))
            figs.setdefault(r["DokID"], []).append(r)

    summ = open(os.path.join(BASE, "PARSE_SUMMARY.tsv"), "w", encoding="utf-8")
    summ.write("DokID\tDokNr\tbody_chars\tbody_words\txrefs\tapplic\tfigures\ttitle\n")
    jsonl = open(os.path.join(BASE, "records.jsonl"), "w", encoding="utf-8")

    ok = 0
    for dokid, row in meta.items():
        gzpath = os.path.join(BASE, "blobs", dokid + ".gz")
        try:
            with open(gzpath, "rb") as bf:
                blob = bf.read()
            fmt, header, body, xrefs, applic, markers = parse_doc(dokid, blob, row["DokTitel"])
        except Exception as e:
            summ.write(f"{dokid}\t{row.get('DokNrID','')}\tERROR\t\t\t\t\t{e}\n")
            continue

        doc_figs = [g for g in figs.get(dokid, []) if g["GraphikID"] != "0"]
        rec = {
            "dok_id": int(dokid),
            "dok_nr_id": int(row["DokNrID"]),
            "dok_type": int(row["DokTypCode"]),
            "lang": "en",
            "title": row["DokTitel"].strip(),
            "fzg_art_bits": row["FzgArtCodes"],
            "fzg_art_indices": bits_to_indices(row["FzgArtCodes"]),
            "kurzgueltigkeit_id": int(row["KurzgueltigkeitID"]),
            "cluster": int(row["Cluster"]),
            "applicability_notes": applic,
            "cross_refs": xrefs,
            "figures": [
                {"graphik_id": int(g["GraphikID"]),
                 "graphik_nr": g["GraphikNr"],
                 "klasse": int(g["GraphikKlasse"]),
                 "cluster": int(g["Cluster"])}
                for g in doc_figs
            ],
            "body_text": body,
            "sids_header": header,
        }
        with open(os.path.join(OUTP, dokid + ".json"), "w", encoding="utf-8") as jf:
            json.dump(rec, jf, ensure_ascii=False, indent=2)
        with open(os.path.join(OUTP, dokid + ".txt"), "w", encoding="utf-8") as tf:
            tf.write(rec["title"] + "\n" + "=" * len(rec["title"]) + "\n\n")
            if applic:
                tf.write("APPLICABILITY:\n  " + "\n  ".join(applic) + "\n\n")
            tf.write(body + "\n")
            if xrefs:
                tf.write("\nSEE ALSO: " + ", ".join(xrefs) + "\n")
        jsonl.write(json.dumps(rec, ensure_ascii=False) + "\n")
        summ.write(f"{dokid}\t{row['DokNrID']}\t{len(body)}\t{len(body.split())}\t"
                   f"{len(xrefs)}\t{len(applic)}\t{len(doc_figs)}\t{rec['title'][:60]}\n")
        ok += 1

    summ.close(); jsonl.close()
    print(f"parsed {ok}/{len(meta)} docs -> {OUTP}")


if __name__ == "__main__":
    main()
