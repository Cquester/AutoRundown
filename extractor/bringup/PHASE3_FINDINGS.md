# AutoRundown — Phase 3: WIS format mapping (COMPLETE)

Full read access to the live WIS Transbase database achieved 2026-09-09, and
the document format is mapped end-to-end. This answers the three Phase 3
questions: how documents key to applicability, how figures link to text, and
what the normalized output should look like.

## How we connect (read-only, host-side)

The database runs inside the VM. NAT can't reach it (the Transbase multiplexer
redirects to the guest's own IP), so:

1. VM snapshot `AutoRundown-base` protects the base disk.
2. App launched in-guest ("Mercedes WSM EPC") starts Tomcat + Transbase.
3. VM NIC switched to the host-only network -> guest = 192.168.56.101.
4. Guest Windows Firewall off (host-only net is isolated, reachable only from
   this PC).
5. Host connects over JDBC: `jdbc:transbase://192.168.56.101:2054/wisnet`,
   user `tbadmin`, empty password, using the bundled 32-bit JRE's Nashorn:
   `java -cp tbjdbc.jar jdk.nashorn.tools.Shell <script>.js`.

## Schema (dumped to schema/)

- `wis.systable.tsv`   — 337 tables
- `wis.syscolumn.tsv`  — 1700 columns
- `wis.rowcounts.tsv`  — rows per table

### Document master: `W_SprachDokumente` (3.0M rows)
One row per document-per-language. Key columns:

| column | meaning |
|---|---|
| `DokID` | document instance id (this language) |
| `DokNrID` | document-number id (shared across languages) — ties to the Lucene `docnr` |
| `SprachCode` | language: 1=de 2=en 3=fr 4=es 5=pt 6=it 9=sv 10=fi 12=el 17=tr 20=? 22=ru 25=pl 28=zh 29=ro 86=ko … |
| `DokTitel` | title (clean text, verified) |
| `DokTypCode` | document type |
| **`FzgArtCodes` bits(32)** | vehicle-type applicability bitmask (the FzgArt 1–10) |
| `KurzgueltigkeitID` | model/short-validity reference |
| `Publikationen` bits(32) | publication applicability |

### Body text: gzip-compressed SIDS in `ID2Blob_<cluster>`
- `ID2Cluster(DokID → GraphikID, Cluster)` gives the cluster number.
- The body blob is `ID2Blob_<Cluster>.DokGraphikBlob` where `DokID` matches and
  `GraphikID=0`.
- Blob is **gzip** (`1f 8b 08`). Decompressed it is **SIDS** (`ASCII-SIDS2.00`):
  a coordinate + text-run page format. Text lives in `T"..."` tokens; other
  documents are referenced as quoted docnrs (e.g. `"SI05.00-P-0002A"`);
  applicability appears as text too (e.g. `"611.### ## as of 359084 in MODELS
  202, 203, 210"`).
- Graphic blobs for the same document sit in the same `ID2Blob_N` at other
  `GraphikID`s; `AW_SVGReferenz` holds SVG links; DXF tables hold technical
  drawings.

### Applicability keying (the core Phase 3 question)
Three complementary mechanisms:
1. `FzgArtCodes` bitmask on each document row (vehicle type).
2. `KurzgueltigkeitID` → model validity.
3. `BM2Dok_*` tables: Baumuster (model designation) → document.
Plus human-readable applicability embedded in the SIDS body text.

### Cross-references
`DokVerweis` (15M rows: DokID → DokNrIDZu) plus inline quoted docnrs in SIDS.

## Proven end-to-end
DokID 1002308 — English "Service Information: modifications to intake camshaft
and valve springs": pulled its title, FzgArtCodes, and body; decompressed the
SIDS; read the title, `ENGINE`, model applicability (611 / 202,203,210), and a
cross-reference. Saved: `sample_doc_1002308.bin` (raw gzip), `.html` (SIDS).

## Proposed normalized output for AutoRundown
Per document (join on DokNrID across languages):
```
{ docnr, dok_nr_id, language,
  title, body_text,                 # text runs extracted from SIDS
  applicability: { fzg_art:[…], models:[…], engine:[…] },  # bitmask + BM2Dok + parsed SIDS
  figures: [ { graphic_id, kind:'svg|raster|dxf', ref } ],
  cross_refs: [ docnr… ] }
```

## Next (Phase 4 — bulk, not yet done)
Iterate `W_SprachDokumente` (filter SprachCode=2 for English = ~124k docs),
join `ID2Cluster` → pull + gunzip each `ID2Blob_<cluster>` body, parse SIDS text
runs, resolve figures and applicability. A SIDS text-run parser is the main new
code. Bulk pulls over JDBC are light on host I/O, but writing extracted output
must still respect the host's sustained-I/O crash limit (throttle, resumable).
