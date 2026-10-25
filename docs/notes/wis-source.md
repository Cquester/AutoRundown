# The Mercedes WIS source: extractor, `records` importer, reference tables

<!-- Moved out of CLAUDE.md on 2026-10-12 to keep that file short; the text
is unchanged apart from file paths (tests/, tools/). "Above" / "below" and
section names may refer to CLAUDE.md or another file in docs/notes/. -->

## Running the extractor end to end

Mercedes WIS, end to end from this one session (VM -> index). The extractor
is `extractor/` in this repo (the only copy since 2026-10-08); its data --
the EWA install it reads, `autorundown_out/` it writes -- stays in
`../Mercedes Documents/`, found by `extractor/wis_paths.py` (`AR_OUT_ROOT` /
`AR_HOME` override; an output folder inside the repo is refused).

```bash
python extractor/wis_paths.py                     # where it reads + writes
# 1. everything, resumable (~4 h from scratch, mostly images): docs ->
#    applic -> vehicles -> links -> images (VM on, headless, 15k-image
#    batches with the VM off between) -> build (offline: parse.py,
#    apply_applic.py --rebuild-join, parse_vehicles.py, parse_links.py,
#    convert_images.py, apply_images.py)
python extractor/vm_pipeline.py --vm "Mercedes_Ultimate_207"
#    a subset: --steps vehicles,build  (or images,build). The Import dialog
#    runs exactly this, then rebuild_wis.py.
# 1b. offline pieces on their own (defaults read <out root>/...):
python extractor/apply_applic.py      # schema 1.2 join; NOT optional after a fresh parse.py
python extractor/parse_vehicles.py    # vehicle names, variants, VIN tables
python extractor/convert_images.py --jobs 4
python extractor/apply_images.py
#     After a parse_sids.py change: reparse_bodies.py (offline, ~18 min,
#     --dry-run first) updates body_text only; then re-run apply_images.py.
python extractor/reparse_bodies.py --dry-run
# 2. rebuild the WIS part of the main index: removes WIS in place
#    (remove_manuals.py --source mercedes-wis), re-ingests; EPC, LEMON and
#    folder manuals and the spend log stay
python rebuild_wis.py
```

The old route (`extractor/extract.ps1`, which also snapshots the VM and
changes its NIC and firewall, and the `pull_images_*.sh` batch scripts, not
in the repo) is superseded by `vm_pipeline.py`. Without `parse.py` doing the
WS/WC join itself, a fresh `parse.py` is schema 1.1 again until
`apply_applic.py` runs -- the importer would then file by text only (it
prints "structured model links IGNORED"); the `build` step does both.

**Never rebuild by copying `index.before-wis.db` over `index.db` by hand.**
The spend log (`usage_log`) lives inside the index, so that silently deletes
every paid question asked since the backup — it happened on several rebuilds
in this project before `rebuild_wis.py` existed.

**`rebuild_wis.py` no longer restores the backup either** (2026-10-16).
It used to restore `index.before-wis.db` and carry the newer usage-log rows
across, but that backup predates the EPC parts catalogue and every LEMON or
folder manual imported after WIS first went in, so a WIS-only rebuild
(also what Shop setup's Mercedes VM job runs after extracting) silently
dropped them. It now removes WIS in place (`remove_manuals.py --source
mercedes-wis`: chunks with their FTS rows, pages, connectors, vehicles,
per vehicle) and re-ingests. It refuses a corpus whose manifest
`source_type` is not `mercedes-wis*` (an EPC corpus would be added while
WIS was gone) before removing anything, and checks every other vehicle's
page and chunk counts and the usage-log row count after the removal and
after the re-ingest. `tests/test_rebuild_wis.py` builds the case that lost
data (backup with one LEMON car; then WIS, EPC, a second car, a folder
import and paid questions) and fails on the old script (Camry, folder
import and all EPC lost). The backup file is left on disk untouched. A
failed re-ingest leaves no WIS, as before: run the script again. Not yet
run on the real index -- the removal of ~530k pages will be slower than
the old file copy.

The WIS DB drops its connection under load. `pull.js` now aborts and
checkpoints at the last good document instead of carrying on, so if step 1
stops with `ABORT`, power-cycle the VM headless and re-run the same command.

To work on the UI for the WIS source without the VM (all free):

```bash
python tools/make_sample_corpus.py       # uses the REAL 406-series catalogue
python ingest.py --archive sample_corpus --index sample_wis.db
python serve.py --index sample_wis.db
```

The sample now draws its vehicles from the **real** `models.jsonl` preserved at
`../Mercedes Documents/autorundown_out/wis_applic/` — real Baureihe codes, real
production spans, real Baumuster, so the picker and the VIN lookup behave as
they will on the real corpus. **The document text is still invented.**
It also writes `variants.jsonl`, `vin_us.json`, `vin2fin.json` and four
engine documents (E102/E272/E273/E276) from a small hand-made table
(`SAMPLE_VARIANTS`: the W211 E 500/550 4MATIC and E 63 AMG, a W212 E 350
that is M272 or M276, a W210 sedan/wagon, the two documented VIN
prefixes), so `tests/test_ui_picks.py` runs on sample data -- in CI too.
Putting the old in-place variant sort back fails it with 106 failures.
`--catalogue <path>` points it elsewhere; without a catalogue it falls back to
a small invented one.

## The `records` importer (second source: Mercedes WIS)

The WIS extractor is `extractor/` in this repo (its data stays outside, in
`Desktop/Mercedes Documents/autorundown_out/`) and emits a source-agnostic
corpus: `records.jsonl` (one normalized doc per line) + `manifest.json`
(provenance, counts, `schema_version`). `extractor/IMPORT_SPEC.md` is the
contract.

**One importer for the whole spec, not one per source.** Future adapters
(another OEM DB, a folder of PDFs) emit the same two files, so they need an
adapter over there and no code here. `records.py` gates on
`schema_version` major == 1.

**Grouping is a mode, not a rewrite** — `--importer records` or
`records:model`. `get()` splits an importer name on `:` and passes the tail to
the constructor; that's the only reason `base.Importer` has an `__init__`.

- `passenger` (default): per model series, **passenger vehicles only** — the
  user's decision. Uses Mercedes' own passenger-car division (ASRA
  `SparteAbk` 00; for the few series with no division, the vehicle type):
  cars, G-Class, smart and the passenger vans (Vito/Viano, V-Class, Vaneo,
  Citan) stay; the commercial division (Sprinter/T1/Vario, trucks, buses,
  Unimog) and engine buckets only commercial vehicles use are left out. 200
  series dropped; the index goes from 3.3 GB to 2.4 GB.
- `model`: per model series, everything — `--importer records:model`.
- `corpus`: one vehicle per (source, language), the whole set in one.
Both per-series modes add a bucket for documents tied to no series at all.

**Model applicability: schema 1.2's WS/WC join, verified against the text.**
This is the single most important thing in this file about the WIS source,
and it has been wrong twice. Schema 1.1 joined `W_Dok2Reihe.ReihenID` straight
to `W_Baumusterzeile.BMZeilenNr` — different keys whose ranges overlap — so on
the 97,891 documents that also state their model in their own text, **98.4%
were linked to a different model** (a Sprinter bulletin, MODEL 901-904, linked
to the 447 V-Class). Each car's list was mostly other cars' pages; the user
found it as "no documents for repair procedures / torque sequence". An "0
misfiled" check written then was circular (it compared the filing with the
field it was filed from) and missed it — which is why `eval_wis.py` checks
page *text*, never buckets.

The right join (schema 1.2, `apply_applic.py` in the extractor; HANDBACK §0a):
`W_Dok2Reihe (DokNrID, ReihenID)` -> `WS1-3 / WC1-8 (BMZeilenNr, ReihenID)` ->
`W_Baumusterzeile.BMReihe`, where the table is the role — 1 vehicle, 2 engine,
3 transmission, 4-8 other aggregates; ReihenID -1 = all. Records carry
`model_series` (vehicle role), `baumuster`, `engine_series`,
`transmission_series`, `applicability_summary` (W_Kurzgueltigkeit's one-line
validity text, put in the page as "Validity: …"), and null years. Measured
independently on our side: the vehicle role agrees with the documents' own
MODEL lines on **99.94%** of 108,853, the engine role with ENGINE lines on
99.1%. 223,968 of 272,607 records get a vehicle series (1.1's prose-only
fallback reached ~98k).

**The importer still measures before it trusts.** `structured_agreement()`
compares `model_series` with the prose on every document carrying both; below
`STRUCTURED_MIN_AGREEMENT` (0.5) it ignores the field for the whole corpus and
prints `! structured model links IGNORED` (1.1 scored 1.6%). Tests:
`ar_badlink_` and `ar_v12_` in `tests/test_records.py`. If a future pull regresses
the join, filing falls back to the text automatically.

`record_series()` rules, each fixing an observed failure:
- vehicle series from the table; **if the table names no vehicle, the text's
  MODEL lines** (the table's "Capacities: Cross-country vehicles" is
  engine-only yet says MODEL 460/461/463 — it reached a 190E through E102);
- **only a document tied to no vehicle goes to its engine bucket** (`E102`).
  One that names vehicles is already under each of them, and every car with
  that engine searches the engine bucket — filing "463 with engine 102" there
  put G-Class oil capacities in front of a 190E and Viano glow plugs in front
  of a C 220 CDI (`eval_wis.py` caught both);
- neither: the general pool, never a guessed car. Prose codes must be series
  in `models.jsonl` (drops part numbers like "MODEL 346 332 04 16"), and the
  slash parser below applies.
`transmission_series` is carried but not filed on: 5,410 of the 6,138 722
gearbox documents name their cars and are under them; the rest are general.

**WIS slash lists are variants, not series — this was a wrong-vehicle bug.**
`MODEL 639.601/603/605/701/703` is five variants of the one series 639; the
suffixes are three digits too. The original regex took every 3-digit number
as a series, so `906.111 /113 /211 /213` filed Sprinter documents under 211
and 213 — the E-Class. 59,634 such suffixes occur; it inflated the picker from
~400 to 621 entries. `_block_families()`: a dotted head claims every slash
suffix after it; a bare head gains a second series only as an exact *pair*
(`MODELS 202/210`), because longer bare chains are variant lists with the dot
dropped (`203 /018 /065 /218` — 218 is the CLS). Test cases use the exact
strings from the corpus.

**The general bucket is narrowed by vehicle type.** It is 28,829 pages since
schema 1.2 (126,641 when only a document's own text could file it) —
and not all of it is universal: bus engine specs, truck capacities, Smart
bulletins. On the real index a Sprinter oil-capacity search returned
"Capacities: Medium-heavy trucks". WIS flags every record with vehicle-type
bits (`fzg_art_indices`, IMPORT_SPEC applicability layer 3), and each series'
own documents carry a consistent set — so each model vehicle stores
`vehicle_types` and a general page is only in scope if it shares one
(`search.Scope.types`; unflagged pages always pass). The vehicle's *own*
pages are never type-filtered: being filed under the model is stronger
evidence than a bit. Deriving a model's types, three rules each fixed a real
failure: ignore documents flagged for 7+ types (the "every type" flag — 18,166
of them, against 64 with six); use only documents naming *just that series*,
because a multi-model document carries the union of its models' bits; keep
bits on ≥1% of those (drops single mis-tags). A series with no informative
documents gets no types, which means no narrowing — the safe default. Note the
bits are WIS's divisions, not "car vs van": the Sprinter shares bit 0 with the
passenger cars.

**`model` mode reads byte offsets, not records.** `discover()` scans the corpus
once and buckets file offsets per family; `pages()` seeks. The alternative is
re-reading ~800 MB once per model vehicle.

**Figure-only records become pages when their graphic exists (schema 1.3).**
Records with no `body_text` — 24,972 in the English set — are graphics:
wiring diagrams, tightening sequences, scanned manual pages. 24,968 now carry
converted images and are indexed; a record with no text *and* no image on
disk is still dropped and counted (`figure-only` in the tally, 4 today —
malformed DXF). The test is *empty body*, not `body_format`: the spec says
RTF is always figure-only, but 15,266 of the 40,238 real RTF records carry
text.

**The image pass (extractor side).** `pull_images.js` reads each graphic
once from `ID2Blob_<cluster>` (`DokID = 0 AND GraphikID = ?` — graphics are
not stored under the citing document) into
`autorundown_out/wis_images/raw/<cluster>/<gid>.bin`, resumable;
`convert_images.py` (Pillow, ezdxf) writes `wis_extract/images/` +
`catalog.tsv`: TIFF G4 / JPEG 2000 -> PNG, JPEG/PNG/GIF copied, gzip'd SVG
re-encoded, DXF R12 -> SVG via `ezdxf.recover` (WIS DXF reuses entity
handles; a strict read silently draws nothing), plus the text labels of
vector graphics; `apply_images.py` adds `image_files` / `figure_text` to
records. **Pull in batches** (`pull_images_batches.sh`, 15k each, VM off
between): the in-VM database resets its connection after ~19.5k graphics in
one session, and the host crashed twice (bugcheck 0x9F, VirtualBox 7.1.4)
during long VM runs — none since updating to 7.2.18 and batching. Full
English pass: 272,185 graphics, ~2 h in 11 batches, conversion minutes.

**Every line is accounted for, and the arithmetic is printed.** Ingest reports
`N records = K indexed + F figure-only + M MALFORMED` and checks `N` against
the manifest's `record_count` (IMPORT_SPEC section 4 exists for exactly this).
A malformed line is called out as data loss, not filtering. Before this the
importer dropped unparseable lines silently, so a truncated `records.jsonl`
was indistinguishable from a smaller corpus — the one failure mode nobody
notices. In model mode it also prints the fan-out (`227 page rows from 167
documents`), because a document is filed under every model it names and the
page total otherwise looks like it invented records. **If someone asks whether
records are missing, this output is the answer** — check it before suspecting
the extractor.

**WIS pages render line by line, not as paragraphs (UI only, WIS only).**
Measured on real WIS text, consecutive lines are rows, not wrapped prose: the
extractor emits one whole paragraph per line, only ~0.2% of adjacent pairs are
a wrapped sentence, and joining them (LEMON's rule in `renderSource()`) ran
torque tables, legends and steps together ("Guide pin … Nm 25 25 Bleed screw …
Nm 14 14"). `showPage()` turns on `layout` mode when the page's owning
vehicle's `source_name` matches `/^mercedes-wis/`, and then `renderSource`
keeps each line, joins a line only when the next one finishes its value
(`wisJoins`: next starts with unit+value like `Nm 25`/`(° 90`, is values only,
previous ends in a bare `Nm`, or a hyphenated word continues), and splits
rows flattened inside one line (`wisRows`: after an `Nm`/`(°` value run
followed by a capital). Checked by eye on 607 line pairs over 42 pages: 0
wrong joins found. **LEMON is untouched** — 300/300 sampled pages rendered
byte-identical before and after. The connector view and answer rendering do
not use layout mode.

**WIS writes the unit before the value** (`Stage 1 Nm 115 115`), so the old
value-then-unit torque highlight lit up "1 Nm" — the *stage number* shown as
a torque. In layout mode `inline()` uses `WIS_TORQUE` and highlights
`Nm 115 115`; value-first readings (`120 to 130 Nm`) still highlight.

**WIS images (schema 1.3): only files that exist, as `[diagram:]` markers.**
`server.py` serves figures from `<vehicle.file_path>/images/<name>`; for WIS
`file_path` is the corpus dir, and every bucket shares it, so
`images/<cluster>/<graphik_id>.<ext>` resolves from any WIS vehicle with no
server change. `query.py` treats anything in `images` as a citable figure,
so `record_images()` keeps only `image_files` entries that exist on disk
(and refuses `..`) — `_verify_citations()` can never accept a figure that
resolves to no file. Each image is a `[diagram: 4/74443.png]` marker in the
page text — the answer model only sees the retrieved chunk, and can only cite
a figure whose marker it was shown; the UI renders the marker in place.

**Figures sit after their step (the user asked: step, then picture).** The
raw SIDS carries each graphic inline (`".P.w1518102401|8.5|6.4|TIFF"`), just
before the step number it illustrates. `parse_sids.py` turns it into
`[figure: <graphik_nr>]` and emits it at the end of that step — before the
next figure or the next step number; a figure with no text after it belongs
to the step about to start. `apply_images.py` rewrites it to
`[diagram: <file>]`, or drops it if nothing was converted; the importer puts
only figures *not* placed inline up front, and removes any inline marker
whose file failed `record_images()`. 380,428 of 380,429 placed. Approximate
where Mercedes lays several steps beside one figure column (steps 1-2 then
one picture). Printed figure numbers ("W15.18-1024-01") are stripped as
clutter. Landed via `reparse_bodies.py`, which re-parses only `body_text`
and refuses to write if more than 50 bodies lose or change a word: 44,625
*gained* text the old cleanup had eaten (`\S*\|w\|h\|FMT` swallowed whatever
was glued to a graphic token, e.g. the last "00" of tool number
"611 589 00 40 00"); 39 gained stray graphic ids, none lost text.

Diagram labels and figure numbers are
appended as "Diagram labels: …" / "Figures: …" for BM25 and the model, and
hidden in the page view. Images open full size on click (wiring diagrams are
~6,800 px wide SVG). Cross-refs (`SI00.00-D-0026A`) stay text in the index;
the server turns them into links when a page opens (see "WIS reference
tables" below).

**"Original layout": the document redrawn from WIS's own page layout.** The
user found the reading view "bunched up" next to WIS. WIS pages get a
Reading view / Original layout toggle (`viewToggle()` in `index.html`,
choice remembered per viewer in `localStorage`); the original is an iframe on
`/api/original/<doc_id>/<page_no>`, served by `autorundown/wis_original.py`
(self-contained; `tools/sids_render.py <dok_id>` is a CLI over it). Free, no
API calls. **The original is the default** (2026-10-24, the user: "I dont
think the way we have reading view looks good. Lets default to original
document"): stored as `autorundown.pageView.v3` = "reading" only when the
viewer picks Reading view (v2's click handler matched `<body data-view>`,
so any click on the page saved "reading" and pages kept opening as text --
fixed 2026-10-25, and the key renamed so that stray value doesn't stick); a page with no original falls back to its text by
itself ("No original layout for this page; showing its text", Original
layout greyed), leaving the choice as it was. **LEMON and folder HTML
pages have an original too** (`html_original.py`, `has_original` on
`/api/page`): the page's `div.main` only (not LEMON's header, breadcrumbs,
footer, stylesheet or script), cleaned to an allow-list of tags and
attributes, pictures re-pointed at `/api/image` (inside `images/` only),
links to other pages turned into `data-page` that the frame posts to the
app (`openPage`), and served with a CSP that runs only the frame's own
script (a nonce per response), pictures and styles from this site only.
Not a sandboxed iframe: the app's browser pane blocked a sandboxed
frame's picture requests (`ERR_BLOCKED_BY_CLIENT`). All 3,000 first pages
of the real F-150 render. Tests: `tests/test_lemon_import.py` (hostile
markup: scripts, handlers, iframes, forms, svg, javascript: links, outside
pictures and styles all dropped). What it knows about SIDS, each measured on a corpus sample:
- **Reads one body, not the pack.** A sidecar `bodies.pack.idx.sqlite` next
  to `bodies.pack` holds DokID -> offset/length and graphic nr -> image file
  (figures.tsv + images/catalog.tsv). Built on first request (~2.5 s), and
  rebuilt by itself when any of the three inputs changes (size + mtime).
- **SIDS bytes are UTF-8.** The prototype read Latin-1 and printed "â" for
  every en dash in torque tables ("Nm – 30").
- **`PC,T,x` starts a table row, `PC,,x` adds a column ending at x**, `PI`
  is a new paragraph in the same cell, a plain `P` leaves the table. Fields
  15-18 are the cell's top / bottom / right / left rule, field 4 the
  alignment (C/R). That turned the torque summaries (10008414: Number |
  Designation | M8 Nm | per-model values) and wiring legends (25016103: Code
  | Designation | Coordinates) into real tables; the prototype's two-column
  guess scattered them.
- **Fonts come from the document's own table** (`F96,,"SWISS 10 BOLD"`),
  sizes from the run's half-points. A run code not starting with 0 and with
  no text (`11`, `2`, `12`) is a tab, as is `L<n>`.
- **Icon and symbol fonts are not guessed.** Each `LOGO*` font is its own
  glyph set (LOGO1C357 = test-equipment symbols); only glyphs whose meaning
  is clear from the text after them in a sample are drawn (danger, note, see
  document, tool), the rest are a neutral box. `GREEK` follows the Symbol
  font (`4.5 W` = 4.5 Ω). `MATH`/`SYMBOLS` have no known mapping and show
  the raw letter underlined in red with a tooltip -- a guessed `±` or `<` in
  a spec is worse than an obvious unknown.
- **RTF bodies** (40,238 -- wiring diagrams, scanned manual pages) are stubs
  naming a graphic: the original layout is that graphic page-wide. Some
  start with a UTF-8 BOM; `&H&` lines are hotspots, not graphics, and are
  ignored. They are **not** links to DokIDs (measured 2026-10-11: none of
  16,954 targets is a document): `0|x|y|w|h;code;` in cm on the graphic,
  on the 99xxxxxx picture menus of WIS's warranty damage-code catalogue
  (Schadensschlüssel), `code` a damage-code component (`SSTeilID` ->
  `SSTeilBenennung`). Boxes line up exactly when drawn. Not used yet; they
  also leak into those 17,995 records' body_text as `.53|0.46;5441R;`
  junk (a `parse_sids.py` fix, not done). The 103 XML bodies have no
  layout (404).
- **Most WIS pages are landscape** (~15,600 units, ~1,330 px drawn). The
  sheet is zoomed to fit the frame like a PDF (a minimum zoom with sideways
  scroll was worse on a phone); "Open full screen" opens it alone for
  pinch-zoom. The page reports its sheet height to the parent by
  `postMessage` -- not `scrollHeight`, which never drops below the frame's
  height and only let it grow.
Checked by eye on 49013390, 49014567 (R107 exhaust, steps + tools),
10008414 (torque table), 25016103 (legend), 25147935 (RTF wiring diagram),
49009029 (scanned page), at desktop and 375 px. A sweep of 4,000 random
DokIDs: 3,999 render (1 XML), 0 unresolved pictures, slowest 0.9 s.

**WIS reference tables: cross-refs, wiring diagrams, option codes, oil
(2026-10-11).** A read-only survey of all 319 WIS tables
(`extractor/probe_wis_tables.js`; output `autorundown_out/wis_tables_survey/`)
found **no parts catalogue** -- no part numbers with quantities, no exploded
drawings; that is EPC, locked (above). What it did find, now pulled:
- `pull_vehicles.js` also pulls `W_Dokumente` (DokNrID -> docnr), `SACode` /
  `SANummer`, `AW_AssystFuellmengen` (oil), `SSTeilID` / `SSTeilBenennung`
  (damage codes), the `ASRA_*` labour tables and `Gruppen` (function group
  and subgroup names per vehicle type, 2026-10-24 -> `wis_group`, the
  Manual tab's section names) -- English rows only
  (`WHERE` per table, `AR_LANG`). `pull_links.js` (new `links` step in
  `vm_pipeline.py`, resumable, 400 DokIDs per query) pulls `DXF`,
  `DXFLegendePos`, `DXFLegende`, `AW_DXFReferenz`, `AW_SVGReferenz` for the
  corpus's 40,341 non-SIDS documents (~30 s). `parse_links.py` (in `build`)
  writes **`wis_links.sqlite` next to the corpus**; the server reads it
  through `autorundown/wis_links.py`. No rebuild of the index needed. Every
  lookup treats a missing sidecar or table as "no data", never a 500.
  **The server opens the sidecar per lookup and never holds it** (2026-10-23):
  it used to keep one connection per process, and on Windows that open
  handle made a re-extraction's `parse_links.py` fail with `WinError 5` on
  the final `os.replace` (an AutoRundown left running from before an MSI
  update held it). `parse_links.py` now retries for ~a minute and then stops
  with a message naming the cause; `tests/test_wis_links.py` replaces the
  sidecar after lookups.
- **Cross-references open their page** (`wis_links.resolve`, `/api/page`
  returns `xrefs`). **A printed number leaves off the variant letters**:
  `AR15.40-P-5710E` is stored as `...5710ea`, `...eb`, `...ecm`, one per
  model, and WIS picks by the car. So a reference matches every stored
  number that is the printed one plus *letters only* (never the sub-document
  `...-01A`), and the variant filed under the chosen car's scope comes first.
  This happens at request time, with the scope -- resolving at import (tried
  first, reverted) matched only 46% exactly and would have picked another
  model's version blind. Measured: 69-76% of printed refs open a page on
  W211/W201/W124, ~3 ms per page; the rest are mostly owner's-manual (BA)
  numbers WIS doesn't hold. UI: one target opens directly; several jump to a
  "Referenced documents" list showing what each applies to. A page filed
  only under other models opens with an `outside_scope` warning
  (`server._wis_page`); search and answers never come through that path.
  WIS's own titles can mislead: BT40.15-P-0080-01A is "Valve body gasket
  modified." and really is the tyre-pressure-monitor valve torque.
- **Wiring diagrams are tappable** (`/api/diagram`, `wis_links.diagram`):
  22,231 English diagram pages have component positions + names. Positions
  are 1/1000 mm; two image forms, both checked by eye: DXF conversions (SVG
  in mm, **Y flipped**) and WIS's own SVGs (pt + viewBox, no flip, laid out
  `xMinYMid meet` -- fractions are of what is drawn, not of the viewBox).
  UI: a tap target per printed code over the `<img>`, plus "Components on
  this diagram" (name, keyword search, linked document via `AW_SVGReferenz`).
  The full-size view has no tap targets.
- **Option codes: removed 2026-10-16** (the user: nobody knows their car's
  option codes). It was a dialog beside VIN (`/api/wis/sa`, `sa_codes`): a
  code means different things per vehicle type (494 = "US version" on a
  car, a tyre on a bus), so the car's type (`FZG_ART`) picked one. The
  sidecar's `sa_code` tables are still pulled; reading a car's data card is
  a possible later way back to them.
- **Oil capacity** (`/api/wis/oil`) in the scope note **only once the engine
  is known**: a variant's engines, narrowed by the car box's year-picked
  family (an E 350 that is M272 or M276 by year showed both before that fix).
  Only 359 English rows / 35 series.
- **Labour times** (`/api/wis/labour`, `wis_links.labour_*`; the "Labour
  times" tab -- a dialog until 2026-10-15; its button beside VIN and the
  scope note's link went 2026-10-16: Ask or Search fills the tab, which is
  empty before a question). **A search shows the job, not every operation
  naming its words** (2026-10-16, the user: "a lot of labour times shown,
  we should only show the correct one"): `_rank_labour` matches the words
  against the operation's *core* -- its component, without the action
  ("Remove/install"), the bracketed conditions and the equipment it applies
  to ("Veh. with transmission 722.6") -- a word matching the words it
  starts ("pad" -> "pads", never "change" in "exchanger"). The `best` are
  the plain operations with the fewest extra words; add-on times ("(with
  transm. removed)", "(Coolant drained)") and campaign items (group 02)
  rank after, and anything that only mentions the words folds away under
  "N other operations mention it". **Where the job differs by gearbox, both
  are best** (the user: the car may be automatic or manual, so give both --
  the car box does not know the gearbox): an automatic's operation brings
  the manual's of the same group and action, and per-automatic operations
  (722.6, 722.9) share a core and are both shown. On the real index:
  "starter" 2 of 5 (Remove/install, Exchange), "transmission fluid change"
  on a W210 E 320 the two automatic fluid changes plus the manual's
  lubricant change, "brake pads" the 8-lining job. WIS ships Mercedes' ASRA flat rates. The chain:
  Baumuster (+ engine) -> `ASRA_Fahrzeug` -> family + type code ("book");
  a time is the row for the exact type code, else its group (`40` = 4x),
  else `00` -- never overlapping (all 478,402 operation x family cases);
  the stored value is **raw**, and the flat rate is the region's formula
  (`ASRA_Formel` / `Formelverwendung`, International) applied to it, **in
  minutes**. Proven against LEMON's "Manufacturer Warranty Hours" for the
  same 190E 2.3-16: voltage regulator 44 min / 0.7 h, bleed brakes 19 /
  0.3, ignition switch 84 / 1.5, starter 110 / 1.8, thermostat 25 / 0.5
  (raw minutes alone are ~30% low). The `ASRA_Vorwort` preface is only
  release notes -- it does not state the unit. Formulas are parsed as
  arithmetic only (`_formula`, never `eval`). **A car can be in several
  books** (sedan/wagon, engine change, chassis-range splits like "124 (up
  to 09/92)"): the UI asks, never picks -- 22 of 29 W211 variants on the
  current index, mostly sedan vs wagon because the body-style split is not
  yet in the real index. Extra work (category Z) has no formula and is left
  out; raw 0 means "not this type". Search also tries the manual's words
  (`synonyms.py`: water pump -> coolant pump, brake pads -> brake linings).
- **Pulled, not used**: the damage-code tables (hotspot names).
Tests: `tests/test_wis_links.py` (free). Browser-checked against the real index
(Playwright, no API key): links, diagram tap targets, the dialog, the oil
line, 375 px.

---
