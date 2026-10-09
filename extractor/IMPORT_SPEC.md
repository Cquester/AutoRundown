# AutoRundown Import Subsystem — Specification (schema v1.1)

Everything the AutoRundown program needs to know to import service data — designed
so today's single source (a Mercedes WIS VirtualBox VM) is just the **first adapter**,
and new sources slot in later without touching the website.

---

## 1. Design goal

```
   source(s)                 adapter                  stable output              AutoRundown
 ─────────────      ─────────────────────────      ──────────────────      ───────────────────────
 Mercedes WIS VM ─▶ mercedes-wis-virtualbox  ─▶  records.jsonl + ─▶  import loader ─▶ search index
 (future) OEM DB ─▶ (future adapter)              manifest.json         (dedup by key)   (website reads
 (future) PDFs   ─▶ (future adapter)                                                      ONLY the index)
```

Two hard rules that keep this clean:

1. **Every adapter emits the same thing** — a `records.jsonl` (§3) plus a
   `manifest.json` (§4) in an output directory. The importer never needs to know
   *how* a source works, only this output contract.
2. **The website reads only the imported corpus at runtime** — never a VM, database,
   or network. Extraction/import is an **offline, one-time-per-source** job.

---

## 2. What an import produces

A single output directory containing:

| file | required | what |
|------|----------|------|
| `records.jsonl` | ✅ | one normalized document per line (§3) |
| `manifest.json` | ✅ | provenance + summary of this import run (§4) |
| `models.jsonl` | recommended | model catalogue: one line per model series with its production-year span + Baumuster list (§3a) |
| `PARSE_SUMMARY.tsv` | optional | per-document stats (debug/QA) |
| `bodies.pack`, `metadata.tsv`, `figures.tsv`, `baumusterzeilen.tsv`, `dok2reihe.tsv` | optional | raw source cache (lets you re-parse without re-reading the source) |

The importer needs **`records.jsonl` + `manifest.json`**. The rest is adapter internals.

---

## 3. The normalized record (THE contract)

One JSON object per line in `records.jsonl`. Fields marked **core** are what any
future adapter should provide; **wis** fields are specific to this source and may be
absent for other sources.

| field | type | scope | meaning |
|-------|------|-------|---------|
| `dok_id` | int | core | unique document id **within this source+language** |
| `lang` | string | core | ISO language code: `en`, `de`, `zh-CN`, `en-US`, … |
| `title` | string | core | human-readable document title |
| `body_text` | string | core | readable text (may be `""` for figure-only docs) |
| `body_format` | string | core | `"sids"` \| `"rtf"` \| `"xml"` \| `"none"` (see matrix) |
| `applicability_notes` | string[] | core | free-text vehicle model/engine strings scraped from the body, e.g. `"MODEL 176"`, `"611.### as of 359084 in MODELS 202, 203, 210"` (incomplete — many docs don't name the model in prose; prefer `model_series` from schema 1.2) |
| `model_series` | string[] | core | model series (Baureihe) this doc applies to, e.g. `["201"]` (W201 = 190/190E). Schema 1.2: the **vehicle** role of the WS/WC join (see below). **⚠ In 1.1 this field is invalid.** |
| `baumuster` | string[] | core | full Baumuster codes when known to the sub-model, e.g. `["201.034"]` (190E 2.3-16). Subset of `model_series` refined to 6-digit models. 1.2: fully specific vehicle-role Baumuster only; wildcard rows mean the whole series. **⚠ Invalid in 1.1.** |
| `engine_series` | string[] | 1.2 | engine series by role (WS2/WC2), e.g. `["102"]` — never confused with a vehicle of the same number |
| `transmission_series` | string[] | 1.2 | transmission series by role (WS3/WC3), e.g. `["722"]` |
| `applicability_summary` | string | 1.2 | `W_Kurzgueltigkeit` one-line validity text for `kurzgueltigkeit_id` ("MODEL 129 up to 31.12.93") |
| `image_files` | string[] | 1.3 | converted graphics of this record that exist on disk, relative to `<corpus>/images/` (`"8/176579.svg"`), in `figures` order. Written by `apply_images.py`; only resolvable files are listed |
| (in `body_text`) | | 1.3 | `[diagram: <file>]` markers where a figure belongs, after the step it illustrates (from the SIDS inline graphic token); figures not placed inline are only in `image_files` |
| `figure_text` | string | 1.3 | text labels inside the record's vector graphics (component codes, wire colours, `PE 54.21-D-2104AA` refs); empty for raster graphics (no OCR) |
| `model_year_from` | string\|null | core | **null in 1.2** — the only dates are `W_Baumusterzeile` documentation windows, not production years |
| `model_year_to` | string\|null | core | latest production date (ISO) across the doc's applicable models; `null` = still current / open-ended. |
| `applies_all_series` | bool | core | true if the doc is tagged to a whole series (`201/*/*/*`), i.e. applies to every variant of that series. |
| `cross_refs` | string[] | core | referenced document numbers (link to other records' `dok_nr_id`/docnr) |
| `image_refs` | string[] | core | figure/graphic pointers (populated for `rtf`/`xml`) |
| `figures` | object[] | core | linked graphics: `{graphik_id, graphik_nr, klasse, cluster}` |
| `dok_nr_id` | int | wis | document-number id; the join key `cross_refs`/Lucene use |
| `dok_type` | int | wis | WIS document type code |
| `fzg_art_bits` | string(32) | wis | vehicle-type applicability bitmask (32 chars of `0/1`) |
| `fzg_art_indices` | int[] | wis | set-bit positions of `fzg_art_bits` (convenience) |
| `kurzgueltigkeit_id` | int | wis | model-validity id |
| `cluster` | int | wis | storage cluster the body came from |
| `sids_header` | string\|null | wis | raw SIDS header line (provenance; null for non-SIDS) |

**Body-format matrix** — what to expect per `body_format`:

| body_format | share (en) | `body_text` | `applicability_notes` | `cross_refs` | `image_refs` | what it is |
|-------------|-----------|-------------|-----------------------|--------------|--------------|------------|
| `sids` | 85% | full prose | often | usually | rare | repair procedures / bulletins |
| `rtf`  | 15% | empty | none | none | **always** | figure-only doc (DXF/TIFF/PNG pointer) |
| `xml`  | <0.1% | short | usually | usually | often | maintenance checklists |
| `none` | ~0 | empty | none | none | none | doc row with no stored body |

**Applicability is layered** — to answer "does this apply to vehicle X", use in order:
1. **`model_series` / `baumuster`** + `engine_series` / `transmission_series` (schema 1.2: the
   structured filter, validated 99.94% against the documents' own MODEL lines). **In schema 1.1
   these are invalid** (see below); a consumer should verify them against layer 2 before use.
2. `applicability_notes` (explicit model/engine text scraped from the body, when present — incomplete),
3. `fzg_art_indices` / `fzg_art_bits` (coarse vehicle-type bitmask, present on every record),
4. `kurzgueltigkeit_id` (model-validity id; free text lives in `W_Kurzgueltigkeit`, not pulled by default).

> **Schema 1.3 images (2026-10-01).** `pull_images.js` reads each graphic once from
> `ID2Blob_<cluster>` (`DokID = 0 AND GraphikID = ?`; graphics are not stored under the
> citing document) into `autorundown_out/wis_images/raw/<cluster>/<gid>.bin`;
> `convert_images.py` turns TIFF G4 / JPEG 2000 into PNG, keeps JPEG/PNG, and writes gzip'd
> SVG and DXF R12 (via `ezdxf.recover`, since WIS DXF reuses entity handles) as plain SVG,
> into `<corpus>/images/<cluster>/<gid>.<ext>` plus `images/catalog.tsv` (with labels);
> `apply_images.py` adds `image_files` / `figure_text`. `image_refs` keeps its old meaning
> (RTF figure *numbers*). Manifest: `images` object with counts.

> **Schema 1.2 applicability join (2026-10-01).** Schema 1.1 joined `W_Dok2Reihe.ReihenID`
> straight to `W_Baumusterzeile.BMZeilenNr` — different keys whose ranges overlap — and 98.4% of
> documents that state their model in text were linked to a different one. The right join:
> `W_Dok2Reihe (DokNrID, ReihenID)` -> `WS1-3 / WC1-8 (BMZeilenNr, ReihenID)` ->
> `W_Baumusterzeile.BMReihe`; the table is the role (1 vehicle, 2 engine, 3 transmission,
> 4-8 other); ReihenID -1 = all. Implemented offline by `apply_applic.py` over
> `doc_applicability.tsv` from `pull_applic2.js`. AutoRundown's importer still measures
> `model_series` against the text on every corpus and ignores it below 50% agreement.

### 3a. The model catalogue (`models.jsonl`)

One JSON object per **model series (Baureihe)**, so the loader can resolve a series to its
production span and known Baumuster without re-reading the source:

```json
{ "reihe": "201", "year_from": "1983-10-01", "year_to": "2000-04-30",
  "baumuster": ["201.018", "201.024", "201.028", "201.034", "201.035", ...], "bm_zeilen": 80 }
```

- `year_from`/`year_to` are the series' overall production span (min/max across the catalogue).
- `baumuster` lists the specific 6-digit models seen for that series.
- A record's `model_year_from`/`model_year_to` are derived from exactly this catalogue, so a
  whole-series doc inherits the series span (that's how a "201, all variants" doc correctly
  reports a window covering 1987).

### 3b. Specific models and VIN decoding (`variants.jsonl`, `vin_us.json`) — optional

Written by `parse_vehicles.py` from the tables `pull_vehicles.js` dumps
(`ASRA_FahrzeugII`, `AW_Fahrzeuge`, `USTypBM2TypBM`, …), and recorded in the
manifest as `variants_file` / `vin_us_file`. Additive: a loader that ignores
them still works.

`variants.jsonl` — one line per (model series, sales name):

```json
{"model_series": "201", "name": "190 E 2.3 16", "baumuster": ["201.034"],
 "wmi": ["WDB"], "engines": ["102.983"]}
```

- `name` is `Verkaufsbezeichnung` as stored (char(18), so some are clipped).
- `body_by_bm` (optional): Baumuster → body style (`Sedan`, `Wagon`, `Coupe`,
  `Convertible`, `Roadster`, `Shooting Brake`), from `ASRA_Typkennzahl`'s
  names. One sales name can span bodies ("E 320 4MATIC" = sedan 210.082 and
  wagon 210.282); AutoRundown splits such a line into one model per body.
- `engines` are engine Baumuster. **Engine and vehicle series share one number
  space** (906 = OM906 engine *and* Sprinter), so a consumer must not treat
  every engine number as a separate document bucket — AutoRundown only links
  engine numbers no vehicle uses.

`vin_us.json` — `{"WDB": {"D": "201", "G": "140", …}, …}`: WMI + the letter at
VIN position 4 → model series. A US VIN does not carry the Baumuster; a
European FIN does, in positions 4-9.

**No model years here, deliberately.** `ASRA_FahrzeugII` model-year columns
are filled on 317 of 92,787 rows (engines only), and its `ProduktionVon/Bis`
are chassis-number ranges, not dates. Per-Baumuster `W_Baumusterzeile` dates
start late (124.030 → 1990 for a 1985 car). Use the series span in §3a.

**Uniqueness / keys.** `dok_id` is unique within one (source, language) import. For a
multi-source or multi-language store, use the composite key
`(source_type, source_id, lang, dok_id)`, or synthesize a global id
`"{source_type}:{lang}:{dok_id}"`. Cross-references resolve within the same source.

---

## 4. The import manifest (`manifest.json`)

Provenance + summary the loader reads before ingesting. Example (real, English run):

```json
{
  "schema_version": "1.1",
  "source_type": "mercedes-wis-virtualbox",
  "source_id": "192.168.56.101",
  "database": "wisnet",
  "language": { "sprach_code": 2, "iso": "en" },
  "record_count": 272607,
  "empty_body_count": 24972,
  "error_count": 0,
  "body_format_counts": { "sids": 232266, "rtf": 40238, "xml": 103 },
  "applicability": {
    "model_lines": 33085,
    "linked_docs": 307650,
    "model_series_count": 190,
    "records_with_models": 268000,
    "models_file": "models.jsonl"
  },
  "records_file": "records.jsonl",
  "records_bytes": 824680579,
  "extracted_at": "2026-09-10T16:28:50Z"
}
```

`applicability` summarises the model/model-year join (§3a): `model_lines` = rows in the model
catalogue, `linked_docs` = distinct documents with a model link, `records_with_models` = how
many emitted records got structured model fields. If `models_file` is `null`, the applicability
pull did not run and records carry empty model fields (still valid, just less filterable).

- **Idempotency key** for a corpus is `(source_type, source_id, language.iso)`. On
  re-import of the same key, **replace** that slice rather than appending.
- `schema_version` gates the loader; bump it if the record shape (§3) changes.
- `record_count` lets the loader verify it read the whole file.

---

## 5. Source Adapter interface (the pluggable part)

Each source is an adapter that implements this contract (language-agnostic — express
it however AutoRundown is written, e.g. a class or a module):

```
Adapter {
  type            : string     // stable id, e.g. "mercedes-wis-virtualbox"
  displayName     : string     // "Mercedes WIS (VirtualBox VM)"
  capabilities    : {
     languages    : bool       // supports per-language selection
     discover     : bool       // can enumerate what's available before extracting
     resumable    : bool       // a run can resume after interruption
     images       : bool       // extracts figure image blobs (not just links)
     manualSteps  : bool       // may require user action mid-run
  }
  configSchema    : {...}       // fields the adapter needs (see §6 for this one)

  discover(config)  -> [{ key, label, count }]   // optional; e.g. list languages
  validate(config)  -> { ok, problems[] }          // check prerequisites, reachability
  run(config, report) -> { outDir, manifestPath, recordsPath }
       // report(event) surfaces progress + any manual-step prompts to the UI
}
```

**Output contract (all adapters):** `run()` must leave `records.jsonl` (§3) and
`manifest.json` (§4) in `outDir`; records must validate against the schema.

**Manual-step reporting:** if an adapter needs the user to do something mid-run, it
must emit a structured event (`report({type:"await_user", message, resolved:false})`)
rather than blocking silently, so a UI can show instructions and a "continue" affordance.

---

## 6. Adapter #1 — `mercedes-wis-virtualbox` (the only one for now)

**What it is:** reads a customer's licensed Mercedes **EWA net / WIS** install running
in a VirtualBox VM and produces one language's corpus. Implemented as a wrapper around
`extract.ps1` (this folder).

**Requirements:** Windows host; VirtualBox (`VBoxManage`); Python 3; the licensed VM;
the bundled JRE + `tbjdbc.jar` (auto-found inside the install). The user must own the
WIS license — the adapter only reads their own installed copy.

**capabilities:** `languages:true, discover:true, resumable:true, images:false, manualSteps:true`

**configSchema:**

| key | required | default | notes |
|-----|----------|---------|-------|
| `vm` | ✅ | — | VirtualBox machine name |
| `guestUser` / `guestPassword` | recommended | — | guest Windows login; enables fully-automatic firewall handling |
| `lang` | | `2` (en) | WIS SprachCode; use `discover()` to list |
| `database` / `port` | | `wisnet` / `2054` | `2034`/`SPECIALFILES` for EPC |
| `outDir` | | `autorundown_out/wis_extract` | where the output lands |
| `nicName` | | `VirtualBox Host-Only Ethernet Adapter` | host-only adapter |
| `skipVmSetup` / `noSnapshot` | | false | for already-running VMs / to skip the safety snapshot |

**Lifecycle → CLI mapping:**

- `discover(config)` →
  `extract.ps1 -VM <vm> -ListLanguages` → prints `SprachCode | iso | docs`.
- `validate(config)` → the script's tool-location + VM-exists + DB-reachable checks.
- `run(config)` →
  `extract.ps1 -VM <vm> -Lang <n> [-GuestUser <u> -GuestPassword <p>] [-OutDir <d>]`
  → snapshot → boot → host-only NIC → firewall off → find DB → pull bodies →
  **pull applicability** (`W_Baumusterzeile` + `W_Dok2Reihe`, body-free & language-independent) →
  parse → writes `records.jsonl` + `models.jsonl` + `manifest.json`.

**Languages** (this DB, from its own `W_SprachCode2ISOSprachCode` table): 22 populated —
`en de es fr it tr ja zh-CN pt pl ru el ro ko cs sv da hu fi nl bg sl` (+ `en-US`);
counts range 272,607 (en) down to ~17,000. `discover()` returns the live list for any DB.

**Manual-step points** (why `manualSteps:true`):
1. The DB only starts once the guest workshop app ("Mercedes WSM EPC") is running.
   Often it auto-starts on boot; if not, the script prompts and waits.
2. Guest firewall: automatic when `guestUser`/`guestPassword` + Guest Additions are
   present; otherwise the user turns it off in the VM when prompted.
   → For a headless/UI-driven import, **supply guest credentials** to avoid both prompts.

**Resumability / performance:** the pull checkpoints to `watermark.txt`; re-running
resumes. One language ≈ 10–15 min (≈270k docs). Re-parsing is offline and idempotent.

**Not extracted yet:** figure **image blobs**. Figures/graphics are *linked*
(`figures`, `image_refs`) but the SVG/DXF/TIFF/PNG bytes remain in the source; a future
`images:true` pass would fetch them from the same `ID2Blob_<cluster>` tables.

---

## 7. Website runtime consumption (read-only)

1. Read `manifest.json` for provenance/version/counts; verify `schema_version`.
2. Stream `records.jsonl` line by line (never load ~800 MB at once); `JSON.parse` each.
3. Index `body_text` + `title` (full-text / embeddings). Keep `applicability_notes`,
   `fzg_art_indices`, `cross_refs`, `dok_nr_id`, `lang` as filters/keys.
4. Answer a repair question: retrieve by text → optionally filter by the vehicle's
   model/engine (applicability) → follow `cross_refs` for related procedures.
5. `body_format == "rtf"`/`"none"` → empty `body_text`; surface via `image_refs`/`figures`,
   not as text answers.

No VM, database, or network at query time.

---

## 8. Adding future adapters

A new source only has to emit §3 records + §4 manifest. Guidance:

- **Provide the core fields**; leave the `wis`-scoped fields out (or null). Add your
  own source-specific fields freely — the loader ignores unknown fields.
- **Set `source_type`/`source_id`** in the manifest (and consider stamping `source_type`
  on each record) so a merged, multi-source store can key/dedup cleanly (§3 keys).
- **Map applicability to `applicability_notes`** in whatever human form the source uses;
  the website treats it as searchable/filterable text.
- Reuse the manifest's idempotency rule so re-imports replace, not duplicate.

Sketch: *another OEM DB* → adapter queries it, maps rows to core fields. *A folder of
PDFs* → adapter OCRs/segments, one record per section, `body_format:"pdf"`. *A REST
API* → adapter pages the API. All three land as the same `records.jsonl` + `manifest.json`.

---

## 9. File inventory (this folder = the import tooling)

| file | role |
|------|------|
| `IMPORT_SPEC.md` | this specification |
| `README.md` | how to run the WIS adapter |
| `extract.ps1` | the WIS adapter's orchestrator (one command) |
| `langs.js` | language discovery (`-ListLanguages`) |
| `pull.js` | reads document bodies + metadata → raw cache |
| `pull_applic.js` | reads the model catalogue + doc→model links (`W_Baumusterzeile`, `W_Dok2Reihe`) → raw cache |
| `parse_sids.py` | decoders for all four WIS body formats |
| `parse.py` | raw cache → `records.jsonl` + `models.jsonl` + `manifest.json` |

Delivered English corpus: `..\autorundown_out\wis_en\records.jsonl` (+ `manifest.json`).
Deeper background on the WIS internals: `..\autorundown_out\wis_en\REPRODUCTION.md`.
