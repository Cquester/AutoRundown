# AutoRundown — working notes for Claude

Local repair-manual search over the user's own service-manual archive.
Retrieval is local (SQLite FTS5); the only network call is to the Anthropic
API. Read `README.md` for the product description — this file is the working
context: what is already decided, what is already tried, and what will break.

**Status: working end to end.** Importer, index, retrieval, Claude query
layer, web UI, Windows MSI (see "Windows installer"). Not built: macOS/Linux
packages (they run from source, by the user's decision), code signing.

---

## First: this folder is transport, not a workspace

If you are running inside `OneDrive\AutoRundown`, **set up a local copy before
doing anything else.** Do not build the index or run the server from here:
SQLite writes on every question, and a sync daemon touching an open database
corrupts it.

```bash
# 1. copy source + index.db to a local folder (NOT archives/)
# 2. extract archives/*.zip locally, e.g. C:\Users\<you>\Manuals\
# 3. repoint the index at that archive location:
python ingest.py --archive "C:/Users/<you>/Manuals" --index index.db --repath
# 4. verify (free, no API calls):
python eval.py --index index.db      # expect 0 failure(s)
```

`SETUP-ON-NEW-PC.md` has the long version. Step 3 is not optional if the user
wants diagrams: page *text* is in the index, but *images* are served from the
archive on disk, and the index stores the absolute path from whichever machine
built it.

---

## Commands

```bash
python serve.py                                   # web UI, localhost + LAN
python ingest.py --archive <dir> --index index.db # build index (~40s)
python ingest.py --archive <dir> --repath         # fix archive path only
python ask.py --vehicle f-150 "<question>"        # CLI, costs money
python ask.py --vehicle f-150 --search "<terms>"  # keyword only, FREE
python ask.py --usage                             # spend, by source
python eval.py                                    # FREE retrieval check
python eval.py --answers                          # PAID, prints estimate
python test_records.py                            # FREE, `records` importer
python eval_wis.py                                # FREE, WIS retrieval + wrong-vehicle guards
python make_sample_corpus.py                      # FREE, stand-in WIS corpus
python test_ui_picks.py                           # FREE, UI sends the car it shows (~45 s)
python -m autorundown.launcher                    # what the Windows shortcut runs
python test_access.py                             # FREE, admin/sign-in/limits/HTTPS (~2 s)
python serve.py --https                           # TLS from a local CA (tls.py)
python dev.py                                     # FREE, throwaway instance, :8757
python test_lemon_import.py                       # FREE, LEMON import source (~3 s)
python test_lemon_catalogue.py                    # FREE, LEMON tab's catalogue tree (~3 s)
python tools/build_lemon_catalogue.py <lemon/index.json>  # rebuild the catalogue snapshot
python remove_manuals.py --doc-id <id> [--compact]  # remove a LEMON / folder manual
python remove_manuals.py --source mercedes-wis    # remove all WIS (never one model)
python test_remove_manuals.py                     # FREE, removing manuals (~20 s)
python test_citations.py                          # FREE, _verify_citations (<1 s)
python tools/check_js.py                          # FREE, index.html's JS parses (needs node)
powershell -File tools/sandbox/start.ps1 [-Source] # fresh install in Windows Sandbox, :18756
```

**Try UI and import changes with `python dev.py`, on the user's PC.** It
builds sample data (`make_sample_lemon.py`, `make_sample_corpus.py`; invented
text) into `.dev/`, runs with its own `AUTORUNDOWN_HOME` there (config,
accounts, TLS) and serves on localhost:8757, so the real index, spend log and
the real server on 8756 are never touched. It drops `ANTHROPIC_API_KEY`, so
Ask costs nothing unless someone types a key into the dev Settings. One of
the three sample LEMON vehicles is imported, two are left for the import
dialog; `--empty` gives the first-start dialog, `--reset` rebuilds.
**The user prefers local sessions to cloud sessions (2026-10-09): never start
a cloud session or remote agent without asking.** In a cloud container,
`dev.py --no-browser` + Playwright (`executable_path="/opt/pw-browsers/chromium"`)
gives screenshots, but the user cannot open that server.

**A fresh install, isolated: `tools/sandbox/start.ps1`** (2026-10-11, the
user's choice over WSL/Hyper-V/Docker). Windows Sandbox (Windows 11 Pro;
the feature must be turned on once, elevated, by the user -- the script
prints the command) gets an MSI (`-Msi`, `-Run <CI run id>`, `-Release`,
else the newest `dist\*.msi`, else the latest release), installs it like
a user (`AUTOSTART=0`), starts it from the Start-menu shortcut, and the
PC's browser reaches it at **http://localhost:18756 as admin** (Shop
setup works); `http://<sandbox IP>:8756` is the mechanic/phone view.
`-Source` lays this checkout's code (uncommitted too) over the installed
app -- the way to try a branch without building an MSI; it deletes the
MSI's `unchecked-hash` .pyc, or the old code would keep running. Admin
needs loopback peer AND loopback Host, so `forward.py` runs twice: in the
sandbox `<ip>:18756 -> 127.0.0.1:8756`, on the PC `127.0.0.1:18756 ->
<ip>:18756`. The sandbox sees only `.sandbox\in` (read-only: MSI, scripts,
sample manuals, copied to its Desktop), `-Manuals <dir>` read-only at
`C:r\manuals`, and writes only `.sandbox\out` (`inside.log`,
`ready.json`/`failed.json`). Closing the window discards everything. One
sandbox at a time (a Windows limit). It has no API key unless one is typed
into its Shop setup -- that spends for real. The forwarder chain was
checked against `dev.py` (admin true, POST 200); **the sandbox itself has
not been run yet** (the feature was off on 2026-10-11).

Mercedes WIS, end to end from this one session (VM -> index). The extractor
is `extractor/` in this repo (the only copy since 2026-10-08); its data --
the EWA install it reads, `autorundown_out/` it writes -- stays in
`../Mercedes Documents/`, found by `extractor/wis_paths.py` (`AR_OUT_ROOT` /
`AR_HOME` override; an output folder inside the repo is refused).

```bash
python extractor/wis_paths.py                     # where it reads + writes
# 1. everything, resumable (~4 h from scratch, mostly images): docs ->
#    applic -> vehicles -> images (VM on, headless, 15k-image batches with
#    the VM off between) -> build (offline: parse.py, apply_applic.py
#    --rebuild-join, parse_vehicles.py, convert_images.py, apply_images.py)
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
# 2. rebuild the WIS part of the main index (~6 min): restores
#    index.before-wis.db, carries the spend log across, re-ingests
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
in this project before `rebuild_wis.py` existed. The script copies the newer
log rows across and checks the count before it re-ingests.

The WIS DB drops its connection under load. `pull.js` now aborts and
checkpoints at the last good document instead of carrying on, so if step 1
stops with `ABORT`, power-cycle the VM headless and re-run the same command.

To work on the UI for the WIS source without the VM (all free):

```bash
python make_sample_corpus.py       # uses the REAL 406-series catalogue
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
prefixes), so `test_ui_picks.py` runs on sample data -- in CI too.
Putting the old in-place variant sort back fails it with 106 failures.
`--catalogue <path>` points it elsewhere; without a catalogue it falls back to
a small invented one.

---

## Spend discipline — read this before running anything

The user pays per API call and has said so directly. Earlier in this project
~87% of all spend was *testing*, not use. Rules:

- **`python eval.py` is free** (no API calls). Run it freely after any change
  to extraction, indexing, chunking, or search.
- **`python eval.py --answers` costs ~$0.08 (Haiku) to ~$0.36 (Sonnet).**
  Only after changing prompts, the answer schema, or models. Not routinely.
  Scope it: `--case <id> --model claude-haiku-4-5` is ~$0.01.
- **Prefer testing UI rendering by injecting a payload into `renderAnswer()`
  via the browser console** rather than asking a real question. Free, and it
  exercises the same code.
- Every paid call is logged to `usage_log` in the index; `ask.py --usage`
  attributes spend to `web` / `cli` / `eval`.

---

## Architecture

```
autorundown/
  importers/     archive-specific code lives ONLY here
    base.py        Importer interface + VehicleRecord / PageRecord
    lemon.py       HTML manual archives (the one in use)
    records.py     normalized corpus: records.jsonl + manifest.json
    folder.py      generic fallback: HTML / text / PDF
  extract.py     HTML -> text; tables -> pipe tables; image + link refs
  index.py       FTS5 schema, chunking, dedup, connectors, usage log
  search.py      BM25 + mandatory vehicle filter, round-robin merge
  query.py       Claude planner + answerer + citation verification
  server.py      FastAPI local server
  web/index.html the whole UI, one file
```

Everything downstream of `importers/` reads only the normalized records. A new
archive format = one new `Importer` subclass, nothing else changes. Keep that
boundary.

---

## Decisions already made — do not silently undo these

**No OCR.** The archive is HTML, not scans. Spec tables are real `<table>`
elements. If someone later points this at scanned PDFs, OCR plugs in at the
importer layer.

**No embeddings.** BM25 + Claude-generated search terms. Manual text is dense
with exact tokens (`3.5L`, `coil-on-plug`, part numbers) where BM25 wins, and
this avoids a multi-GB model download. Add vectors *alongside* only if
retrieval starts missing on vague questions — measure first.

**Vehicle filter is applied in SQL before ranking**, not as post-filtering. A
wrong-vehicle spec must never be a candidate.

**Citations are verified, not trusted.** `_verify_citations()` drops any page
or figure the retrieval did not actually return, and withholds an answer left
with no surviving citation. This is the core safety property — a confident
wrong torque is worse than no answer. Do not weaken it.
Steps carry no citation of their own, so they never count as one: an
answer of steps whose cited pages were all invented is withheld, steps and
all (the user's rule, 2026-10-11: "only answers that have sources behind
it"; before, a steps-only answer survived with no citation). Tests:
`test_citations.py`. Not yet seen on a paid answer -- if the model often
leaves `source_pages` empty on procedures, they will now show as withheld.

**Retrieval merges round-robin, not by global score.** Global ranking let one
half of a two-part question crowd out the other ("how much oil AND drain plug
torque" returned only the oil). Regression test: `multipart-oil-and-torque`.

**A scope is searched in one statement, ranked per bucket** (2026-10-09,
QA finding M13). FTS5 finds and scores every match in the whole index
before the `doc_id` filter applies, so one query per bucket did that
whole-index work 12-14 times for a generation (7-20 s per keyword search on
a full-size synthetic index). `search._run_scope` runs AND once over
`doc_id IN (…)` with `ROW_NUMBER() OVER (PARTITION BY doc_id …)`, then OR
only for the buckets that came up short -- the same filters, still in SQL
before ranking, and only the general bucket type-narrowed. Checked against
the old per-bucket code on that index: 42 searches, identical rows; median
1.37 s -> 0.80 s, worst 9.8 s -> 1.3 s. Not done: a `doc_id` column inside
the FTS table (narrows inside FTS5; ~10x more, but rebuilds the FTS table).

**Answer schema is split by shape**: `specs` (values only), `figures`
(diagrams that *are* the answer, e.g. tightening sequences), `steps` (ordered
procedure). Without `figures`, the model writes a spec whose value is "see
Fig 15", which renders in 30px type as though it were a torque.

**The answer reads top-down the way the job is done** (the user's request,
2026-10-08): Answer, Procedure, Figures, then Specifications, caveats,
sources. It used to lead with specs.

**Only the chosen car's values, checked after the model.** The user typed
"2001 E 320 4MATIC" (W210, M112) and got torque values for every W210
engine: a page listing M112, M113 and OM613 rows passes the SQL engine
filter because it names the M112 too, and the model copied every row.
`ANSWER_SYSTEM` now says to give only this vehicle's rows, and
`query._drop_other_vehicles()` enforces it like the citation check: a spec
whose `applicability` (+ name) names engines (`M113`, `OM 613`, `engines
112, 113`, `113.940`) or model codes of this series (`210.082`) and none of
the car's is removed. Unnamed, "all engines" and anything with "except" stay.
Only when a car or engine is chosen (`vehicle_codes()` is None for the whole
generation). Removed specs come back as `other_vehicle_specs` and render
collapsed ("2 values for other engines or models — not your car, hidden");
if nothing else is left the answer becomes not-found. Tests in
`test_records.py` ("answers keep only the chosen car's values"). **Not yet
tried on a paid answer.**

**Prompt rules that exist for a reason** (in `ANSWER_SYSTEM`): never convert
units; one entry per fastener; a conflict is only *same unit, different
number* — different units are the same spec written twice; answer every part
of a multi-part question. Each of these fixed an observed failure.

**Licence: MIT, "Copyright (c) 2026 cquester"** (the user's choice,
2026-10-08). It covers this repo's own code and docs only. Never commit
Mercedes software (`tbjdbc.jar`, the EWA JRE) or anything extracted from
WIS/EPC (records, images, VIN tables, raw TSVs) — no licence makes that
redistributable. `importers/data/*.csv` is hand-made reference knowledge and
NHTSA-derived years, which is why it is allowed.

---

---

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
`ar_badlink_` and `ar_v12_` in `test_records.py`. If a future pull regresses
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
~6,800 px wide SVG). Cross-refs are WIS docnrs (`SI00.00-D-0026A`) with no
docnr -> dok_id map, so they stay text, not links.

**"Original layout": the document redrawn from WIS's own page layout.** The
user found the reading view "bunched up" next to WIS. WIS pages get a
Reading view / Original layout toggle (`viewToggle()` in `index.html`,
choice remembered per viewer in `localStorage`); the original is an iframe on
`/api/original/<doc_id>/<page_no>`, served by `autorundown/wis_original.py`
(self-contained; `tools/sids_render.py <dok_id>` is a CLI over it). Free, no
API calls. What it knows about SIDS, each measured on a corpus sample:
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
  start with a UTF-8 BOM; `&H&` lines are hotspots (links to other DokIDs),
  not graphics, and are ignored. The 103 XML bodies have no layout (404).
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

---

## Vehicle selection: year / make / model, and VIN

Built: **Year → Make → Model → Variant (optional)**, plus a VIN box.

- **Model is the generation** ("W201 190", "R107/C107 SL/SLC"). Years are
  known per generation (curated table, exact — no padding), so the Model list
  is right for the chosen year.
- **Variant is optional** ("All variants" by default) and lists every variant
  of the generation *regardless of year*, because the data cannot say which
  variants were sold in which year: checked against 21 variants with known
  years, 7 of the 11 R107 variants have no date rows at all and the one real
  end date was wrong. An earlier design listed variants in the Model menu and
  showed a 280 SL under 1987 — the user caught it. What a variant changes is
  the engine documents searched (see below); "All variants" searches every
  engine the generation was built with.
- Engines ("Engine 102") and unnamed components (the 722 gearbox) sit in their
  own sections at the end of Model, under "Any year" only.
- The Model label omits the make (it's the step before).

Three sidecars in the corpus dir feed it, all optional:

- `models.jsonl` (IMPORT_SPEC 3a) — series production spans → the Year list.
- `variants.jsonl` (3b) — one line per (series, sales name) with Baumuster and
  engines, from `ASRA_FahrzeugII` + `AW_Fahrzeuge`. 5,936 models across 234
  series. (Counts of named vs unnamed indexed series were taken under the old
  structured filing and have not been re-measured.)
- `vin_us.json` (3b) — `USTypBM2TypBM`, 38 rows: WMI + VIN position 4 → series.
- `vin2fin.json` — `AW_VIN2FIN`, 7,577 US VIN prefixes (first 9 characters,
  check digit included) → Baumuster. The importer attaches them to each
  variant as `vin9`.

They come from `pull_vehicles.js` (8 tables, 9 s, VM on) and
`parse_vehicles.py` (offline) in the extractor folder. Raw TSVs are kept in
`autorundown_out/wis_vehicles/`.

**Picking a variant selects the series' manual plus that variant's engine.**
Documents are filed per series, so there is no per-variant manual — the scope
note under the toolbar says so ("searches the 190 (201) manual, which covers
all 11 variants … plus the documents for its engine (102.983)"). What a variant
*does* change is the engine: a document whose text says `ENGINE 102` is filed
under the engine (`E102`, 233 pages), not under the W201, so without this the
190E 2.3-16's engine documents are unreachable from the car. Spot check after
the rebuild: "cylinder head bolts tightening" on the 190 E 2.3 16 returns
W201, E102 and general pages interleaved. `scope_ids(vehicle, variant)` = car, its
engine bucket(s), then the general pool (only the general pool is
type-narrowed). Choosing the series itself includes every variant's engines.
The variant index travels as `variant=` on `/api/search` and in the ask body;
`get_page` uses the series-level scope, so a cited engine page always opens.

**The engine is the choice that matters, so the picker is grouped by it.**
The user picked "W212 E-Class, all 87 variants", searched "valve cover", and
got V6, V8, AMG and diesel answers side by side -- 10 engine families, all in
scope. Now the Variant menu is grouped by engine ("M272 · V6 petrol"), each
group with "Any M272 — E 250, E 300, …" plus its cars; with several engines
and none chosen, the scope note says "Pick your engine above for exact specs"
in accent colour. Engine labels come from `importers/data/mercedes_engines.csv`
(reference knowledge, like the year table; an unknown family shows its number),
attached per variant as `engine_families`.

An engine choice (`engine=272` on `/api/search` and in the ask body) narrows
twice, both in SQL before ranking:
- **buckets**: only that engine's bucket is added — a sales name can span
  engines ("E 300" = M272, later M276), so taking the variants' engine_ids
  pulled in E274/E276;
- **pages**: `pages.engines` (",642,") holds the engines a page is limited
  to — the engine role, else ENGINE lines, else an engine code in the title
  (`TITLE_ENGINE_RE`, three digits so bolt sizes never match; 3,178 untied
  pages, e.g. "Cylinder head cover, OM457"). With an engine (or a car) chosen,
  a page passes only if it names no engine or that one. 60,023 car-filed
  records name an engine: "Bolt tightening procedure for cylinder head cover"
  under the W212 is OM642 only and showed for an M272 E 350 before.
`Scope.engines` carries it; a chosen car filters to its own engines. The
answer model is told the choice (`query.narrowed()`: "M272 · V6 petrol;
models: E 250, E 300 …"), not just "W212 E-Class".

**Exact codes: a family-level engine filter is not enough.** The user asked
about a 190E 2.3-16's valve cover and got **15 Nm from 50000338, "Engine 102
(except 102.983/99 …)"** — a page that excludes this car's engine (the
16-valve 102.983). Its own page, 49013390 "Engine 102 KE in model 201",
says cap nuts 10 Nm and plastic screws 2 Nm. Three fixes:
- `code_limits()` stores per page `codes_except` (codes after "except" in
  title, MODEL/ENGINE lines or validity text; 6,805 records) and
  `codes_only` (exact codes left in the title, "Engine 102.942 …"; 1,533).
  Model and engine codes alike. With a car chosen, `Scope.codes` = (its
  Baumuster, its engine codes, narrowed by the year-picked engines) and the
  SQL drops a page that excludes *all* of either group, or whose only-codes
  name none of the car's. All-of, so a choice spanning codes loses nothing.
- `CODE_BOOST` (1.35) multiplies BM25 for pages naming the car's exact
  **engine** code: 49013390 was #14 behind 8-valve M102 pages. Boosting on
  model codes too (or ≥1.5) pushed the right W124 brake-pad / wheel-bolt
  pages out of the top 10 — `eval_wis.py` caught it; tuned on all 19 cases.
- The answer model is told the exact codes ("190 E 2.3 16 (201.034) …
  exact engine code 102.983"), and the Ask path translates the planner's
  queries too ("valve cover" -> "cylinder head cover").
Regression case: `w201-2316-valve-cover` (expect 49013390, forbid 50000338).

**Keyword search translates shop words** (`autorundown/synonyms.py`): WIS
says "cylinder head cover", never "valve cover". `/api/search` runs the
manual's wording first, then the user's, and returns one row per page (two
matching chunks of one page used to show twice). The UI says "Also searched
the manual's term: …". Ask doesn't need it — its planner rewrites queries.
Unambiguous pairs only; bare "rotor" was left out (distributor rotor).

**Engine documents have their own namespace, so engine and vehicle numbers
never collide.** 906 is the OM906 truck engine and the Sprinter; 651 the
OM651 diesel and a truck series; 123 the M123 and the W123. Engine buckets
are `E<nnn>` (`E651`), filed from the engine *role* (schema 1.2) or an
`ENGINE` line, so no vehicle can share one; `engine_ids` prefers the `E`
bucket. Measured on the current index: all 1,230 passenger variants link to
an engine bucket (OM651: 123 of 123), 0 links point at a vehicle bucket.
Engine doc_ids are lowercase (`…-e102`). Passenger mode drops `E` buckets
that no car variant uses. Engine buckets are **not** type-narrowed (see Known
gaps).

**VIN: two formats, both from Mercedes' own tables.** A European FIN carries
the Baumuster in positions 4-9 (`WDB 201034 …`), so it pins the series *and
the exact variant*. A US VIN carries a coded letter at position 4
(`USTypBM2TypBM`: `WDB`+`D` = 201), so it pins the series only — the user
picks the variant — and position 10 gives the model year (letters repeat
every 30 years; the candidate inside the series span is used). Stored per
vehicle as `vin_codes` (",WDB:D,"). `USMotKar2TypBM4` maps another position to
the Baumuster's 4th digit, but which VIN position is not established, so it
is pulled and unused. A non-Mercedes VIN fails with a clear message: the
first three characters must be a Mercedes-Benz maker code (`MB_WMI` in
`index.html`, plus any the index's VIN tables name) -- before 2026-10-09 a
Fiat VIN with digits at 4-6 was read as a FIN and picked a W201. Typed text
is a VIN only when it is one 10-17 character token and matches no car
("mercedes 190" was decoded as one).

**US VINs pin the exact variant via Mercedes' own decoder table.**
`AW_VIN2FIN` (WIS, 7,870 rows, pulled by `pull_vehicles.js`; `AR_TABLES=`
pulls single tables) maps the first 9 VIN characters to the first 9 of the
FIN = WMI + Baumuster: `WDBDA34D4` → 201.034 190 E 2.3 16, `WDDGF8AB3` →
204.080 C 300 4MATIC. `vinLookup()` tries it first (variant + year), then
falls back to the series-only position-4 code. Covers 84 series up to ~2016
(the database is dated 2016-11); newer US VINs fall back to series only.

**Data cards (EPC) are not available — they are access-protected.** The EPC
install has the data card databases (`DAG_DCPC`, `MBNA_DCNA`, `MBOE_DCCAR`,
… 2016-11-23, up to 8 GB each, port 2034), but they refuse both accounts the
rest of the install uses (`tbadmin` for WIS, `tbuser` from `epc_cfg.xml`,
which does open `SPECIALFILES`). That is a deliberate lock on that data; do
not try to get around it. The user was told the legitimate routes: look a VIN
up in the EPC program inside the VM, or use credentials issued with their
licence if they have any.

**No option-code (SA code) descriptions are reachable either — checked
2026-10-08, do not look again.** `SPECIALFILES` (1.2 MB; "42 tables" counted
the system catalogue and views: 12 base tables) has no code text. With
`tbuser`, 11 of its 12 base tables refuse SELECT; their `_V` views are
granted and hold only data-card *routing* metadata: `DC_MODEL_DESC`
(43,447 rows, WMI + Baumuster → short model description, mostly
trucks/buses), `WHC_MASTER(_MODELS)` (WMI → market/plant names),
`VIN_MDL` (74 rows, VIN prefix → model stub — a subset of what
`AW_VIN2FIN` already gives), `DCDBINFO` (which data-card DB serves which
region), `AGG_FILTERS*`, `DB_LANGS`, `VERSIONS` ("DW 01/2017"), `SAREC_INI`
(6 config rows). `PWORD`/`PWORD_V` were deliberately not read. The
catalogue databases where EPC keeps code texts — `ALLTEXT`, `COMM`, and
`SA_IMAGES`/`BM_IMAGES` — refuse `tbuser` at login, like the data cards.
So decoding a pasted data card needs a code list from elsewhere; nothing
was added to the pipeline. Survey output:
`autorundown_out/epc_survey/`; script `extractor/probe_epc_survey.js`.

**Per-model US model years: built once from NHTSA, committed.**
`importers/data/mercedes_us_years.csv` (11 KB: Baumuster, US years, NHTSA
trims) comes from `tools/build_us_years.py`, run by hand: for each of the 887
`AW_VIN2FIN` patterns (first 8 VIN characters; vPIC ignores the check digit)
it decodes a synthetic VIN per plausible year via vPIC's batch endpoint and
keeps the years vPIC recognises — with a trim (a bare "M-Class" is a generic
match) and clipped to the generation span +1. The site never calls NHTSA; if
vPIC changes, nothing breaks (cache in `tools/.us_years_cache.json`).
304 Baumuster got years. Checked against known cars: right for 2000s models
(W212 E550 4MATIC M273 2010-11, M278 2012+; E350 4MATIC M272 2010-12), loose
for 1980s ones (190E 2.3-16 shows 1983-87, sold 1986-87) and one stray year
(212.072 2015) — extra years, never a hidden car. `parse_vehicles.py` now
writes `engine_by_bm` per variant, so a year picks the engine.

**One line picks the car** (`#carInput`, the dropdowns are "Browse"). Type
"2009 Mercedes-Benz E550 4Matic" or "09 e550 4matic": suggestions are built
client-side (`buildCarIndex`, ~2,500 entries) from each variant x its US
years → the Baumuster valid that year → their engines; Mercedes' slash names
("E 500/550") also match "E550", and NHTSA trims are matched too. A 2009
E550 4MATIC resolves to the **W211** (the W212 is 2010+ in the US). Picking
sends `variant` + `engine` (comma list when a year is ambiguous, "M272 or
M276" for a 2012-13 E 350) + `year`; the answer model is told all three. A
VIN in the same box resolves the same way. Non-US variants are listed by
name ("not a US model") under their generation's years.

**Body style: one model per body (2026-10-08).** WIS sells the W210 sedan
(210.082) and wagon (210.282) under one name, "E 320 4MATIC", so the user
could not pick their wagon. `parse_vehicles.py` now writes `body_by_bm` from
`ASRA_Typkennzahl` (already pulled; joined on the type-number key columns
both tables share, language chosen by which names most bodies; it prints
what it used and the W210 example -- **check that line**, the join was
written without the real TSVs at hand). The importer's `split_by_body()`
turns a name spanning bodies into "E 320 4MATIC Sedan" / "... Wagon", each
with only its own Baumuster, so codes, US years, VIN prefixes and the answer
label follow. The car box matches "wagon", "estate", "sedan", "cabriolet".
Variant positions shift, so the remembered-pick keys are now `.v3`. To land
on the real index (no VM needed): `python extractor/parse_vehicles.py`, then
`python rebuild_wis.py`.

**Fixed 2026-10-08: variant indexes were wrong since the variant picker
shipped.** The UI sorted each generation's variants by name *in place* and
sent the sorted position; the server indexes the stored order. Choosing "E
500/550 4MATIC" (28th by name) searched the W211's 28th stored entry, the
E 63 AMG — 29 of 29 W211 positions differed. Now nothing sorts `_variants`;
display order is sorted inside `engineGroups`, and the remembered-pick key
became `autorundown.vehicle.v2` so old positions are dropped.
`test_ui_picks.py` now checks that the sent index names the shown car, for
every pick the UI can make (see Verifying changes).

**There is no per-variant model year in this database — do not look again.**
`ASRA_FahrzeugII.ErstesModeljahr/LetztesModeljahr` are filled on 317 of 92,787
rows (engine series only). `ProduktionVon/Bis` there are **chassis-number
ranges** (`034959`–`999999`), not dates, despite the name. Per-Baumuster dates
from `W_Baumusterzeile` start late and run open: 124.030 (300E, 1985-1993)
comes out 1990-1995, which would *hide* the car from a 1987 filter. So years
stay at series level (below) — and those are unreliable too.

**Production years from `W_Baumusterzeile` are approximate, and sometimes
wrong.** They are a plain min/max over every row of a Baureihe, and those rows
are documentation-validity windows, not market years. Verified against the real
catalogue: the W140 S-Class (1991-1998) reports 1983-2000, the W202 C-Class
(1993-2000) reports 1985-2019, and the C117 CLA (a 2013 car) reports 1985-2016.
The error is *usually* too wide, but not always — see the measured result
below, where it hides 6 of 46 known series. Do not try to tighten it with a
heuristic: taking the minimum over only the specific-Baumuster
rows (ignoring the `*/*/*` wildcard rows) was tested and fixes 140 and 202 but
makes 201 and 164 worse and does nothing for 117. (`ASRA_Fahrzeug` was
expected to be the fix; it is not — see above.)

**The year filter is not trustworthy, and it does hide cars — a measured
fact, not a guess.** Scored against 46 series with known production years
(±1 year): the current min/max spans *hide 6* (the W126 from 1979 and R107 SL
from 1971 both show 1983 — the database's documentation epoch — so a 1980 W126
is missing under 1980; the W123 and W213 have no start at all) and add ~17
spurious years on average, mostly because rows without an end date make spans
run to today (why a 1990s W202 shows under 2010). Every alternative estimator
over `W_Baumusterzeile` was worse: ignoring wildcard rows hides 22, trimmed
percentiles hide 14-25. The dates are documentation validity; no rule recovers
production years from them. (An earlier note here claimed the spans were "too
wide, never hiding a car" — that was wrong.) The evaluation script and the
46-series truth table are in the session history; rebuilding them is cheap.

**So years (and series names) come from a hand-maintained table — the user's
decision, made on that evidence.** `importers/data/mercedes_years.csv`:
`series, year_from, year_to, known_as` for 78 car/van/smart series. It is
reference knowledge, not Mercedes data, and says so in the code. Rules:
- A series with no row gets **no year and is shown under every year** — never
  hidden. Six vans were left out deliberately rather than guessed (448, 611,
  636, 690, 900, 909); trucks, buses and Unimogs are not in the table at all.
  Engines and components (no named models) stay under "Any year" only.
- Years filter **exactly**. An earlier ±1-year padding put the W123 (ended
  1986) under 1987; the user expects the list to match the year.
- `known_as` is the series label ("W126 S-Class", "NCV3 Sprinter"); a name
  shared by several series gets its number ("Vario (667)").
- Page text no longer says "built <years>": those came from the unreliable
  catalogue dates and are what the answer model reads.
- **Add a row when a new model appears.** `test_records.py` checks the file is
  well-formed and that no two series share a label.
Other sources have no table and keep their catalogue spans.

**Makes come from Mercedes' own vehicle type.** WIS covers cars, vans, trucks,
buses and Unimogs in one corpus; 70-odd truck series listed among the cars is
what made the picker unusable. `AW_Fahrzeuge.FzgArtCode` names each Baumuster
CAR / 4xWD / Light or Heavy transporter / TRUCK / BUS / UNIMOG / smart, which
`parse_vehicles.py` writes as `vehicle_type` (exact Baumuster, else the
series' majority), and `series_make()` maps to Mercedes-Benz / Vans / Trucks /
Buses / Unimog / smart. Falls back to `SparteAbk` (00 car, 10 commercial →
"Mercedes-Benz Commercial", 30 old series). One name rule is used: "U <n>" is
Unimog in 75/75 known cases. The matching "O <n>" = bus rule is **not** used —
only 78% agree (van-based minibuses). Note the Vito is a *Light transporter*
though its `SparteAbk` is car; the type table is the better signal.

**Double-encoded text is repaired at import.** The WIS driver decodes the DB's
UTF-8 as Windows-1252, so "Coupé" arrived as "CoupÃ©" and "Ø" as "Ã˜" — ~1,000
vehicle names and 12,751 document bodies (the diameter sign in specs).
`fix_mojibake()` (importer and `parse_vehicles.py`) rewrites a run only when
its bytes are valid UTF-8, so correct text is never touched. It handles both
forms the driver produces: cp1252 ("Ã©") and Latin-1, where "–" arrives as
"â" plus raw C1 control characters — 11,398 WIS pages in the index built
before 2026-10-01 still carry that second form (it reads "Nm â 30"), and the
next re-ingest repairs 11,371 of them. `wisDemojibake()` in the UI repairs
the display meanwhile. The proper fix is upstream (decode as UTF-8 in the pull);
the repair makes the index clean regardless.

**Series without a curated name fall back to the real sales names.** `class_name()` over a
series' variants gives "C-Class"-style labels only when every name shares its
first word (W201 → "190 (201)"). Most series don't ("260 SE", "560 SEL"), so
`sample_names()` shows a spread instead: "MODEL 126: 260 SE, 380 SEL, 560 SEL
…". The label also reaches the answer planner as vehicle context, where a bare
number means nothing. Names are as stored: `Verkaufsbezeichnung` is char(18),
so some arrive clipped ("300 SD, S 350 Turb").

**A search scope is a set of doc_ids, not one.** `search.scope_ids(vehicle,
variant)` returns the selected vehicle, then its engine buckets, then the
general pool (`general_doc_id`). **The core property is intact** — each query
is still filtered to one doc_id before ranking; the scope only decides which
doc_ids are queried, so a wrong-model spec is still never a candidate.

The general pool is 28,829 pages since schema 1.2 and it is narrowed by
vehicle type (see the `records` importer section), because not all of it is
universal.

**Duplicates are removed by `page_no`, not `page_uid`.** A document naming
both a model and an engine is filed in both buckets, so a scope holding both
would show it twice. A scope never mixes sources, so `page_no` (the WIS
DokID) is unique within it.

**Results are interleaved, and the round is ordered by score.** The general
bucket is far larger, so ranking the two together would bury the model's own
pages — hence round-robin, as with `multi_search`. But unlike `multi_search`
these lists answer the *same* query, so BM25 scores are comparable and the
better one wins within each round. Without that, a loose OR-match on the model
took rank 1 over a tight match from the general bucket. That is what
`_interleave(..., by_score=True)` is for; `multi_search` keeps `by_score=False`
because scores from different queries are not comparable.

**Nullable columns were added after the first release** — `vehicles`:
`general_doc_id`, `model_code`, `vehicle_types`, `variants` (JSON),
`vin_codes`; `pages`: `vehicle_types` —
with an `ALTER TABLE` migration in `index._migrate` (`ADDED_COLUMNS`), because
`CREATE TABLE IF NOT EXISTS` never touches an existing index and an index
should not need rebuilding for a nullable column. Other importers leave them
NULL and `scope_ids` degrades to a single, unnarrowed id.

**Makes are grouped on a folded key in the picker.** The HTML archive says
"Mercedes Benz", WIS says "Mercedes-Benz"; `makeKey()` in `index.html` folds
case and punctuation and shows the commonest spelling. Presentation only —
stored data is left as each importer wrote it.

**"C-Class" is a heuristic, not data.** The database has no marketing class
name, only per-variant sales designations. `class_name()` takes the shared
first token and expands a single letter, and **returns "" when the designations
disagree** rather than guessing — `sample_names()` covers that case.

---

## Shop network: who may do what (2026-10-09, the user's decisions)

**The PC running the server is the admin, and the only one.** `auth.py`:
a request is admin when its socket peer is loopback *and* its Host header is
loopback (`localhost`, `127.0.0.1`, `[::1]`) -- the second check stops DNS
rebinding, where a web page's own name resolves to 127.0.0.1. Admin needs no
sign-in. `server.ADMIN_PREFIXES` (`/api/settings`, `/api/browse`,
`/api/vms`, `/api/import`, `/api/admin/`) answer 403 to anything else; the
middleware `who_is_asking` enforces it in one place. Before this,
`POST /api/settings` was open to the LAN: a phone could replace the API key.
uvicorn runs with `proxy_headers=False` so `X-Forwarded-For` can never make a
request look local -- **do not put a reverse proxy on the same PC** in front
of it; every request would then come from loopback.

**Mechanic accounts are optional** (`Config.require_signin`, default off =
open LAN as before). No passwords: the admin issues an 8-character code
(15 min, single use, no 0/O/1/I/L), a phone swaps it for a 32-byte token in
an HttpOnly cookie (365 days). Only SHA-256 hashes of codes and tokens are
stored. Disabling a user deletes their tokens at once. 10 wrong codes from
one address = 429 for 15 min. Cross-site POSTs are refused by `Origin` check
(`_origin_ok`), for the admin too. Accounts are in `~/.autorundown/auth.db`,
**not in the index**, so a rebuild cannot lose them.

**Spend per mechanic.** `usage_log` gained `user_id`, `user_name` (nullable,
`ADDED_COLUMNS`); the name is a snapshot so reports survive account changes.
A monthly cap (`users.monthly_cap_usd`, UTC calendar month) is checked
before the API is called (402); keyword search stays free. `rebuild_wis.py`
compares log rows on the backup's columns and migrates the restored index
before carrying rows over -- without that, the wider rows never matched and
the insert would have failed.

Phones keep their model choice in `localStorage` and send it per question;
only the PC changes the default. `/api/stats` hides this PC's paths from
others. The UI asks `/api/me` first and shows the gear (admin), an account
button (mechanic) or the sign-in box. `#code=XXXX` in the URL pre-fills it.

**HTTPS (`serve.py --https`, `tls.py`).** Own CA (EC P-256, 10 years,
`~/.autorundown/tls/`), Name Constraints (critical): DNS `localhost`,
`.local`; private IPv4/IPv6 ranges only -- a leaked CA key cannot sign for a
real site. Server cert 397 days, SANs = localhost, `<host>.local`, loopback
and every private address of the PC; re-issued at start when an address is
missing or <30 days remain. `/ca.crt` is public. Verified with Python's ssl
(OpenSSL) for `localhost` and `127.0.0.1` in `test_access.py`. **Not yet
tried on a real iPhone or Android phone** -- in particular whether iOS
accepts the critical Name Constraints. A CA made since 2026-10-09 also
carries EKU serverAuth (a leaked key can't sign code or mail); keys are
created 0600, never written then chmod'ed.

**Hardening from the QA review (2026-10-09; `docs/qa/REPORT.md`).** All in
`server.py` unless named; `test_access.py` covers each.
- **Every request must name this PC** (`auth.host_allowed`): an IP address,
  localhost, the PC's name, `<name>.local`, `autorundown.local`, else 421.
  The admin check alone let a DNS-rebinding page search and *ask on this
  PC's key* as an ordinary LAN user.
- **Admin routes refuse cross-site browser requests** (`Sec-Fetch-Site`
  other than same-origin/none) -- an `<img src=…/api/vms>` on any site ran
  the extractor. Clients without the header (curl, `launcher --stop`) pass.
- **No handler is built from manual text.** Page/connector/view links are
  buttons with `data-page`/`data-doc` and one delegated listener in
  `index.html`; `esc()` escapes `'`. A LEMON page file named `9');…('.html`
  ran script as the admin, and through `extractor_dir` + `/api/vms` ran code
  on the PC. The `lemon` importer skips pages not `[A-Za-z0-9._-]+`;
  `extractor_dir` must be a local absolute folder holding `vm_pipeline.py`.
- `/api/image` serves picture types only, with a sandboxing CSP + nosniff
  (an SVG in `images/` ran as the admin). Every response: X-Frame-Options
  SAMEORIGIN, frame-ancestors 'self', nosniff, no-referrer.
- Bodies over 256 KB are refused unread (`BodyLimit`); search `limit` 1-50,
  `q` 500 chars; a question 2,000 chars (paid input); monthly caps 0-100,000
  and finite (-5 blocked a mechanic for good, Infinity meant no limit).
- Phones get no `file_path` (vehicles, page, image/original errors); Settings
  refuses an empty or missing index, a non-index file, an unknown model and
  a network-share extractor; `config.json` is written atomically (a
  half-written file made other requests 500).

## Importing from the site

**Shop setup is a page, not pop-ups** (2026-10-10, the user's choice after a
designer review, `docs/qa/designer-review.md`): `#/setup/<section>` takes
over the window -- Manuals (the import tabs, with the job and its log in a
card beside them), AI answers, Phones & mechanics, This PC, Advanced. Left
nav on a PC (max 1280 px wide), a list page with drill-down at ≤900 px. The
old Settings / Import / Mechanics dialogs (520-720 px, stacked three deep)
are gone: only this PC is admin, so a phone-sized box made no sense there.
Toggles and the default model save as they change ("Saved ✓"); the API key
and the Advanced folders have their own Save, and an unsaved edit survives
moving between sections (sections are only hidden; the nav says "unsaved").
The header link is "⚙ Shop setup" (icon only on a phone).

**First start and starting up** (same date): the page shows a "Starting
AutoRundown…" card until `/api/me` and `/api/vehicles` answer -- a 5xx or no
answer retries every 2 s, never a raw "Internal Server Error" (the user hit
one after an upgrade, see `index.connect`'s `setup`). With no manuals the PC
lands on a "Set up AutoRundown" checklist (add manuals, required; API key
and phones, optional) and can't enter an empty search screen; a phone gets
"No manuals yet … check back later". While an import runs, a banner says
results are partial (`/api/status`, any signed-in device).

Shop setup → **Manuals**, and automatically on first start
when the index is empty. A folder browser plus format detection
(`importer_jobs.describe()`: a folder with `manifest.json` + `records.jsonl`
is a corpus straight away — letting the other detectors walk its `images/`
took 15 s), then a background job that runs **the same CLI**: `ingest.py
--importer auto` (add / update, resumable) or, for a WIS corpus,
`rebuild_wis.py` (replace the WIS part, keep other manuals and the spend log).
One job at a time; the child runs with `PYTHONIOENCODING=utf-8` (a piped
child on Windows otherwise dies printing "Coupé"); the log streams into the
dialog. `/api/browse` and `/api/import` answer **only to localhost** (403 to
a phone on the LAN, measured) since they list folders and run long jobs;
search stays open to the LAN. Verified end to end: a WIS rebuild started
from the dialog finished in 3 min 13 s, rc 0, evals unchanged.

**The import sources are folder tabs** (LEMON · Mercedes VM · Folder,
opening on LEMON; tabs were the user's choice over radio buttons,
2026-10-09). **LEMON is one tab** -- the user asked to keep one LEMON source,
not two -- made of **three cards, one open at a time** (2026-10-10: the user
found the folder section at the top, open, "too much"): "LEMON catalogue ·
Preview" first and open (2026-10-10, the user: "catalogue should be on the
top"; one muted "picking only" line, no warning box), then "From a folder
on this PC" (once a LEMON folder is recognised the folder browser and
the detected box step aside behind "Change folder…"), then "Imported LEMON
manuals (n)". `impSource()` is `lemon` when the folder card is open,
else `catalogue` (no Import button, so the tab opens without one). The
folder browser (`#impFolder`) is moved between the Folder tab and that
section; `impSource()` still answers `lemon` (folder import) / `catalogue`
(tree) / `folder` / `vm`, so the import code paths are unchanged.

**Removing manuals: in the tab that imported them** (2026-10-09, the
user's design over a separate cleanup section). The LEMON tab lists
"Imported LEMON manuals" (every `lemon` source car, including ones named the
old way that the tree can't show) with tick boxes and "Remove selected"; the
Folder tab lists folder imports the same way; the Mercedes VM tab has one
"Remove all Mercedes WIS data". **WIS is never removed per model**: its
models share engine buckets and the general pool, so a single entry would
leave the rest half-working -- the server and `remove_manuals.py` refuse a
WIS doc_id. Removal runs `remove_manuals.py` as an import job (log, one job
at a time): deletes the car's chunks (the FTS trigger follows), pages,
connectors and vehicle row, one commit per car. Safe per car because
duplicate pages are only matched within a doc_id (`index.add_page`). It
checks the spend log row count is unchanged and never touches files on disk.
"Shrink the index file" = VACUUM + `wal_checkpoint(TRUNCATE)` (in WAL mode
VACUUM lands in the -wal file; without the checkpoint the file doesn't
shrink -- the test caught it); off by default for single cars, on for WIS.
`/api/import/manuals` lists, `POST /api/import` modes `remove` /
`remove_wis`, both admin-only. Test: `test_remove_manuals.py`.

**LEMON folder import** (inside the LEMON tab): open the archive folder, get one row per vehicle folder with
"imported" / "partly imported" read from the index (opened `mode=ro`), tick
which to import. `/api/import/lemon?path=` lists them
(`importer_jobs.lemon_vehicles()`, admin-only under `/api/import`); the job is
`ingest.py --importer lemon --only <folder> …` (`--only` matches a vehicle by
its folder; an unknown one fails the run). The server only accepts folders
its own listing returned. The Folder tab still auto-detects LEMON and points
at the LEMON tab. A torrent source exists on the unmerged branch
`claude/confident-wozniak-t9iphf`; the user chose to leave it out until it is
tested on its own. Test: `test_lemon_import.py`.

**LEMON catalogue tree** (the LEMON tab, 2026-10-09): every vehicle in the
LEMON torrent as folders, make > year (newest first) > model > variant, each
with a tick box -- the user wants to tick "Mercedes Benz" for all of it, or
open it and tick a year, a model or one variant (drop-downs were tried first
and replaced). Folders load as they are opened and show how many vehicles
and how many are imported; ticks are all / some (indeterminate) / none.
A pick is stored as a folder path in `APP_DIR/lemon_picks.json`, never as a
list of cars: picking a folder drops picks inside it, unpicking inside a
picked folder splits it into its other children (`set_pick`), so picks
never overlap and counts are sums. `picked_vehicles()` expands them for the
download step. Nothing downloads yet. `autorundown/lemon_catalogue.py`;
`GET /api/import/lemon-catalogue?path=<JSON list>`, `POST …/picks {path,
picked}`, admin-only. Test: `test_lemon_catalogue.py`.

What the torrent actually is (read 2026-10-09 from `lemon-manuals.torrent`
and the `lemon-website-source-code.tar.gz` inside it; **not** the vehicle
folders the torrent branch assumed, so that branch can't work on it):
`lemon/index.json` (143 MB), `lemon/pages.mtbl` (31.5 GB), `lemon/images.mtbl`
(481 GB), the same three under `charm/`, and LEMON's own server binaries.
`index.json` = 279,988 vehicles `{make, years, model, engine, uriPath,
rootUriTable, rootLinkTable}`, 65 makes, 1960-2025. The `.mtbl` files are
sorted key-value tables (zstd blocks of 128 KB, index block at the end);
page keys are `<content type>_<sha256>` (`html_labor_detail_…`,
`uri_table_act_…`), so **one car's pages are spread across the whole 31.5 GB**
and a 64 MB-piece torrent can't hand over one vehicle. Images are
`image_<id>`; whether one car's ids cluster in `images.mtbl` is untested.

- The catalogue comes from the torrent's `index.json` when
  `config.lemon_torrent_dir` holds one, else from the committed snapshot
  `importers/data/lemon_catalogue.json.gz` (0.6 MB, `{make: {year: {model:
  [engine]}}}`, built by `tools/build_lemon_catalogue.py`, which refuses to
  write if any vehicle's name isn't "model engine").
- "imported" is exact: a LEMON car's name (last part of `uriPath`, and the
  breadcrumb our `lemon` importer reads) is always "model engine", so its
  doc_id is `_slug(year-make-model engine)`; `load()` maps every doc_id to
  its tree path once (~0.7 s) so imported counts reach every folder. Manuals downloaded under older
  names ("190E 2.3-16 (201.034) L4-2.3L (102.983)") match no row; the 1987
  catalogue has no 2.3-16 at all, only "190E 2.3, 102.983/102.985".
- The "engine" field also carries trim and body for many makes ("Lariat, 4D
  Pickup Extra Cab, 3.5L Eng VIN G, 4WD"), so the tree calls that level the
  variant; a filter box narrows the open folders' variants (and the makes).
- Per-car image download measured on the real index (2026-10-09): the
  F-150 Lariat's 8,689 images (593 MB in its LEMON zip) sit in 154 of the
  64 MB pieces of `images.mtbl` = 9.6 GB, in 101 runs; image ids
  (`G000161176`, `GFD41139`, `VA467679`) are the file names in a LEMON zip.
  A car's image list is only known from its pages, so text comes first.
- **The download (not built) must follow what the user agreed, 2026-10-09**,
  and the LEMON tab's "How LEMON downloads will work" already tells users so
  -- keep the two in step: one download for all picks; size, free space
  needed and what stays afterwards shown before starting, refused without
  the space; afterwards the torrent files (pages.mtbl, partial images.mtbl,
  `.parts`, resume data, index.json) are deleted and only the index plus
  each picked car's own images (`APP_DIR/manuals/<doc_id>/images`) stay --
  removing a car deletes its folder; setting "keep the LEMON text database"
  off by default; never seeds, upload capped while running, session and port
  closed when done. Ideas not yet measured: a two-pass fetch (the
  `uri_table_*`/`link_table_*` keys sort together near the end of
  pages.mtbl, so pass 1 could fetch only the tables and the index, pass 2
  only the pieces holding the picks' pages) and batch-and-delete to cap peak
  disk. Sizing it needs piece 16308 (the first 12 MB of the 60 MB pages
  index); the user has not yet OK'd that fetch.
- **Never fetch from lemon-manuals.la over HTTP** -- the user's rule (the
  site asks not to be scraped). The `.torrent` itself is its download link;
  anything else comes from the swarm.

**From a Mercedes virtual machine** (third source in the same dialog):
`/api/vms` lists VirtualBox VMs; the job runs the extractor's
`vm_pipeline.py --vm <name> --out-root <autorundown_out> --steps …` and then
`rebuild_wis.py` on `<out_root>/wis_extract`. The pipeline is the manual
procedure in one resumable script: docs (`pull.js`, power-cycle + resume on a
drop, 6 tries) → applic (`pull_applic.js`, `pull_applic2.js`) → vehicles
(`pull_vehicles.js`) → images (list from `figures.tsv`, 15k per batch, VM off
60 s between) → build (`parse.py` → `apply_applic.py --rebuild-join` →
`parse_vehicles.py` → `convert_images.py --jobs 3` → `apply_images.py`). It
only starts/stops the VM headless and reads the DB; it never snapshots,
changes the NIC or the guest firewall (the old `extract.ps1` did) — if the DB
isn't reachable it says what to set up by hand. A saved VM state is
discarded for a cold boot (a 7.1 save state may not resume under 7.2). Finds
VBoxManage, the EWA `sun_jre` Java and `tbjdbc.jar` itself; the extractor
folder is `config.extractor_dir` or this repo's `extractor/`, and the output
root comes from that extractor's `wis_paths.py` (`importer_jobs.
extractor_out_root()`), so the site and the scripts agree. Verified from the dialog with steps
vehicles,build: 25 min, rc 0, every corpus count identical to the hand-run
build (223,968 vehicle-tied, 380,428 figures placed, 7,577 VIN prefixes).
**From-scratch run, 2026-10-07** (old outputs moved to
`autorundown_out/_previous_2026-10-07`, not deleted): ~4 h from the dialog,
every manifest count identical to the previous corpus (272,607 records,
223,968 vehicle-tied, 76,667 engine, 272,181 images, 380,428 placed). It
found three gaps, now fixed in `vm_pipeline.py`: every pull (not just
`pull.js`) power-cycles and resumes on a DB drop — `pull_applic2.js` lost
the connection on its last table; a failed run always shuts the VM down
(`STARTED` + `finally`) — it had been left running; and `vm_up` tries a
second cold boot before giving up — one boot after 12 clean batches never
brought the DB up in 10 min. Conversion sorts largest-first, so its ETA
reads ~3 h while on the DXF/SVG diagrams and then drops; ~40 min in total.

## Windows installer

`packaging/windows/build.ps1` + `AutoRundown.wxs` (WiX 5) build a **per-user
MSI** to `%LOCALAPPDATA%\Programs\AutoRundown`: no UAC prompt, and Python
can write there. `.github/workflows/windows-msi.yml` builds it on
`windows-latest`, installs it silently, indexes the sample corpus with the
*installed* Python, serves and searches it, launches a second time, then
uninstalls and checks nothing is left. Before that it does a real first
start: opens the installed Start-menu shortcut (no index) and runs
`packaging/windows/ui_check.py` in Edge -- first-run import dialog, import
job to "Finished" (ingest.py as a child of the bundled Python), car box,
keyword search, page view, phone width; free, `/api/ask` is blocked. Works
locally too (`--executable` for a Chromium binary). Uploads the MSI and
`ui-screenshots` (a cloud session cannot download artifacts: the blob host
is blocked; read the step's "ok" lines in the job log instead). Not covered by CI
(needs a person): SmartScreen, the firewall prompt, Windows 11 desktop.
macOS/Linux run from source (README), by the user's decision.

- **Embeddable Python, not PyInstaller/Briefcase.** Import jobs re-run
  `ingest.py` / `rebuild_wis.py` / `vm_pipeline.py` with `sys.executable`,
  and a frozen exe would launch itself. The bundle is the official
  `python-<ver>-embed-amd64.zip` (same version as the build's Python, so pip
  wheels match) + `pip install --target Lib/site-packages` + the source.
- **`python3XY._pth` replaces sys.path** and stops Python adding a script's
  own folder. Its `..\app` line is what lets `python ingest.py` import
  `autorundown`. Anything the installed app imports must live in `app\`.
- **User data is outside the install** (`~/.autorundown`, `config.py`), so
  upgrade and uninstall never touch the index or the spend log. The WIS
  backup `index.before-wis.db` sits *next to the index it belongs to*:
  `rebuild_wis.py --index` (default: the configured index) and
  `importer_jobs.wis_backup()`. Before 2026-10-08 both used the repo folder,
  so a dialog rebuild ignored the served index.
- `autorundown/launcher.py` is the shortcut target. The MSI shortcuts run
  `pythonw -m autorundown.launcher --background`: no console, output to
  `~/.autorundown/autorundown.log`, a pystray tray icon (Open / phone
  address / Start when I sign in / Open log / Stop); without a tray it serves
  headless. Console mode (default from source) is the old behaviour. If one
  is already running it only opens the browser; `--stop` POSTs
  `/api/shutdown` (localhost only). A failed start in the background shows a
  message box, since there is no console.
- **The wizard asks** (`AutoRundownUI` in the .wxs, WiX UI extension):
  Welcome -> Options ("Start AutoRundown when I sign in", property
  `AUTOSTART`) -> Ready -> Finish with "Launch AutoRundown" ticked. No
  licence page (WixUI_Minimal's was dropped). The Options box installs the
  `Autostart` component: the same Startup-folder shortcut `autostart.py`
  writes, so Settings can still turn it off. Default: on for a first install
  and for upgrades from versions that never asked (0.1.x <= 0.1.2); after
  that an upgrade keeps the current state (`autostart_asked` registry value +
  a search for the shortcut). Silent installs: on, `AUTOSTART=0` for off. The
  user expected the installer to leave it running -- 0.1.2 only added
  shortcuts, so nothing ran until clicked. CI reads the dialog wiring from
  the MSI tables (no one clicks the wizard there).
- **Not a Windows service, deliberately** (the user asked for one): a service
  needs admin and runs as SYSTEM, which can't see the user's VirtualBox VMs
  or `~/.autorundown`. `autorundown/autostart.py` writes a Startup-folder
  shortcut instead (Settings checkbox and tray item; Windows only, localhost
  only); the MSI removes it on uninstall, and its `StopRunning` custom action
  runs `--stop` before upgrade/uninstall so files aren't in use.
- **`autorundown.local`**: `autorundown/mdns.py` (zeroconf) registers an
  `_http._tcp` service whose host is `autorundown.local.`, so the PC answers
  that name itself -- the user wanted a phone bookmark that survives IP
  changes without router setup. Best effort: any failure is logged and the
  server runs on; Settings shows `phone_urls` (name + numeric). Two PCs
  running it would contend for the name.
- Version: `autorundown/__init__.py` `__version__`; bump it per release (MSI
  MajorUpgrade needs a higher version to replace an install).
- **CI is `.github/workflows/ci.yml`** (2026-10-11, the user's design):
  every PR runs lint (`ruff --select F`), `tools/check_js.py`, the free
  suites, a sample index served and searched, and `ui_check.py` in
  Playwright's Chromium -- on Linux; Windows and macOS legs join by
  themselves once the repo is public (the user plans to make it public;
  while private, Windows minutes count 2x and macOS 10x of 2,000/month,
  and required checks are not available). On `main` and tags the same
  tests run first, then `windows-msi.yml` (now `workflow_call` only, or
  "Run workflow" by hand), then the release. PRs build the MSI only when
  they touch packaging, the launcher/autostart/mdns/config, requirements
  or workflows (`changes` job). `claude/**` pushes no longer build
  anything: open a PR. No secret is used; `ANTHROPIC_API_KEY` is forced
  empty and `ANTHROPIC_BASE_URL` points at a closed port.
  **Add a new free test to the "Test suites" loop in `ci.yml`.**
- **Releases: a push to `main` publishes one** (`ci.yml`'s `release`
  job, 2026-10-09, the user's request). When the MSI job passes on `main`,
  the MSI and its `.sha256` go to a GitHub release `v<__version__>` with
  generated notes. A version that already has a release is skipped with a
  notice, never overwritten -- so **bump `__version__` in the PR** to ship
  it. A PR that builds the MSI uploads it as a workflow artifact (90 days).
  Only that job gets `contents: write`; the build itself stays read-only.
- The WIS extractor is **not bundled**, deliberately: it reads the EWA
  Java and `tbjdbc.jar` from the "Mercedes Documents" folder
  (`extractor/wis_paths.py`; Mercedes software, never shipped).
  `importer_jobs.extractor_candidates()`: config `extractor_dir` (Settings
  -> "Mercedes extractor folder"), then the repo's `extractor/` -- from
  source that is all, no fallback to a second copy (they drift). Only the
  installed app, which has no `extractor/`, also tries
  `~/Desktop/Mercedes Documents/autorundown_extractor` and the OneDrive
  Desktop one (the user's copy). The bundle includes `requirements-extractor.txt`
  (Pillow, ezdxf) because the extractor runs under the bundled Python and its
  build step converts images. CI copies `extractor/` to the runner's Desktop
  and checks `/api/vms` finds it.
- Unsigned: SmartScreen warns (README says what to click). Signing needs a
  certificate; nothing in the build does it yet.

## Archive-specific traps

These are properties of the archive, discovered the hard way. A flat or
simple archive hides most of them.

| Trap | Handling |
|---|---|
| Each article filed under ~3.2 folders | dedup by content hash; citations name the canonical page |
| Manual contains other engines' pages (2,747) | flagged `other_variant` at import, excluded from search by default |
| Images flat *or* nested (`images/A/B/x.png`) | refs kept relative to `images/`, never basename |
| UI icons are `<img>` too | anything outside `images/` rejected |
| "Refer to: X" was a real link | preserved as `[[ref:N::label]]`, stripped before the model sees it |
| Connector IDs (`C906`) are bare text | indexed from page titles; 529 for the Ford, 0 for the Mercedes |

The Mercedes yielding zero connectors is correct, not a bug — a 1987 manual
predates the end-view format. Features here degrade quietly by design.

---

## Verifying changes

1. `python eval.py` — free, catches indexing/ranking/dedup/variant regressions
   on the F-150. `python eval_wis.py` — free, 18 WIS cases (~15 s): expected
   DokIDs plus wrong-vehicle guards that read page *text*. Guards that fail
   today are a ratchet (`KNOWN_BAD` ceilings): more offenders fails the run,
   fewer prints IMPROVED — lower the ceiling when it does.
2. For UI work: start the server, drive it with the browser tools, and inject
   payloads into `renderAnswer()` rather than paying for answers.
   After any change to the vehicle picker (`#carInput`, Browse, VIN, the
   variant menu, `loadVehicles`): `python test_ui_picks.py` — free, ~45 s,
   needs `pip install -r requirements-dev.txt` (Playwright; it drives the
   installed Edge/Chrome, no browser download). It loads the real page against
   the index (opened `mode=ro`; `--index` to point elsewhere), makes picks —
   typed queries with arrow/Enter, a US VIN and a FIN in the box and the VIN
   dialog, Browse + reload, then every one-line suggestion (~2,500), every
   Variant option (~1,600) and one VIN prefix per variant (~430) — captures
   each `/api/search` and checks `variant=` against the stored
   `vehicles.variants` read in Python: same name (and Baumuster for Browse),
   engine families the car has, its engine buckets in `scope_ids`.
   `/api/search` is answered by the test; any non-GET or `/api/ask` request is
   aborted and fails the run. Re-adding the old in-place sort fails it with
   6,242 failures, the first being the reported one (E 500/550 4MATIC → E 63
   AMG). Known quirk it works around: typing "190 e 2.3 16" finds nothing —
   `carMatches` reads a bare "16" as the year 2016.
3. For extraction changes: re-index (~40s) and re-run `eval.py`. Watch
   `unique_pages` — if it moves, dedup behaviour changed and that should be
   deliberate.
4. Only then, if prompts or schema changed: `eval.py --answers`.

Current baseline: 45,507 pages → 16,034 unique → 21,401 chunks, 189 MB, ~40 s.
Eval 7/7 on both Sonnet 5 and Haiku 4.5.

With Mercedes WIS (English, passenger mode, schema 1.3 with images) in the
same `index.db`: 272,607 records = 272,603 indexed + 4 figure-only, 531,847
page rows (x1.95), index 3.5 GB, ~6 min via `rebuild_wis.py`. Images live
outside the index: 272,181 graphics, 11 GB, in `wis_extract/images/`.
W201: 1,181 pages, W124: 2,522 (under schema 1.2). `index.before-wis.db` is
the pre-WIS backup the script restores; `index.v11.db` is the last
schema-1.1 index, kept for rollback (both gitignored). `eval_wis.py`: 18/18
pass, known-bad guards 33 -> 12.

---

## Known gaps

- `eval.py` cases are archive-specific (2015 F-150 page numbers). They will
  fail against any other archive; that is documented in the file.
- Sibling-engine pages exist in the main tree identified only by their title
  (e.g. "Generator - 2.7L EcoBoost"). The prompt requires flagging
  applicability, but this is prompt-level, not structural.
- The spend log lives inside the index. `rebuild_wis.py` carries it across a
  WIS rebuild; any *other* way of rebuilding (deleting `index.db`, copying a
  backup over it) still loses it.
- **WIS retrieval is checked by `eval_wis.py`, not by answers.** 18 cases,
  13 known-bad guards left (other engines' documents in the general pool,
  engine buckets not type-narrowed, title-only applicability). The right page
  is found in every case but often at rank 4-7; one page can fill several of
  the top 10 as separate chunks (dedup is per chunk), visibly so in the UI's
  keyword list.
- **Answers over WIS have not been tried.** Everything above is keyword
  search; no paid question has been asked against the WIS corpus. The answer
  prompt was tuned on LEMON HTML.
- WIS pages have no system taxonomy (`system` is left empty), so the
  connector index does nothing for that source (`/api/connectors` returns
  `[]`), as with the Mercedes HTML set.
- **The vehicle-type narrowing is a derived heuristic.** The thresholds
  (7+ bits = universal, ≥1% share, single-series documents) are fitted to the
  English corpus and verified on a handful of models. A general document with
  a type bit its model's own documents never carry is hidden — that is the
  point, but if WIS mis-flagged a genuinely relevant bulletin, it's hidden
  too. The "include other trims/engines" checkbox does *not* bypass this.
- **The W124 E-Class has no year range at all**, so it only appears under "Any
  year". One row in `W_Baumusterzeile` stamps it `1194-03-01` (a transposed
  1984) — the only implausible date in all 33,085 — and the importer refuses
  it rather than guessing. This is the one case where the year filter is
  *under*-inclusive, which is the unsafe direction. Fixed by the extractor
  ignoring out-of-range dates in the rollup; reported in `HANDBACK.md`.
- **The real corpus lives outside the repo** at
  `Mercedes Documents/autorundown_out/wis_extract/` (records.jsonl 861 MB,
  models.jsonl, manifest.json). Real numbers: `records_with_models` is
  197,658 — 72%, not the ~98% the IMPORT_SPEC example claims — and
  `model_series_count` is 406, not 190. Treat that example as illustrative.
- **Leftover font fragments** ("MATH A 10", "CLASSIC 12") show as their own
  lines on WIS pages; the "Tightening torques: Passenger cars: …" summary
  documents (e.g. 10010804) carry component names but no values — an
  extraction gap in the extractor, not a rendering problem.
- **4 wiring diagrams don't convert** (malformed DXF, e.g. 6/190775
  "Invalid group code VERTEX"); their records stay figure-only and dropped.
- **Scanned manual pages (classes 10/30, ~2,700 records for old cars) are
  findable by title only** — no text layer, and no OCR by decision.
- **Wiring diagrams rank behind text pages.** A component-code search
  (`X63/4 N3/10`) finds the W203 engine-CAN diagram at #7 with "All
  variants": every engine bucket gets a slot per round. Picking a variant
  narrows it.
- **Transmission-only documents stay general.** Most gearbox documents name
  their cars and are filed under them (schema 1.2); the ~700 that name only a
  transmission go to the general pool, since no car→gearbox map exists. The
  `MODEL 722` bucket is down to 1 page.
- **Engine-titled documents in the general pool.** Untied documents like
  "Repair notes for the cylinder heads … Engines 601, 602" still reach a
  petrol 190E. The type filter can't catch them (same vehicle-type bit);
  their applicability is visible and the prompt must flag it.
- **More buckets, slower search.** A whole-series scope for the W140 queries 7
  buckets: ~0.7 s per keyword search, more for an answer (several planner
  queries). Picking a specific variant narrows it.
- Series with no sales names keep "MODEL xxx" labels; engine buckets are
  labelled "Engine xxx" and sit at the end of the Model list.

---

## Where we left off (2026-10-08) — start here in a new session

1. **"Original layout" is built** (see its section above). Not yet seen by
   the user on their phone; ask how it reads. Open ends: the undecoded
   MATH/SYMBOLS glyphs and most LOGO icons, RTF hotspots as links.
2. **Extractor: one copy now** (2026-10-08, the user's decision). The site
   runs `extractor/` from this repo; the data stays in `../Mercedes
   Documents/` (`extractor/wis_paths.py`). The old standalone copy,
   `../Mercedes Documents/autorundown_extractor`, was left in place (we
   never delete) -- nothing uses it; the user may remove it. Not yet re-run
   end to end from the repo copy: the paths resolve and the evals pass, but
   the next VM run is the real check.
3. ~~A UI-level test for variant indexes~~ — done: `test_ui_picks.py`.
   Open: a bare two-digit word in the car box is always a year ("190 e 2.3
   16" → 2016, no match).
4. Old data moved aside in `autorundown_out/_previous_2026-10-07` (~15 GB)
   and `index.v11.db` — the user may delete them; we never delete data.
5. **Older figure numbers ("P01-5220-13"): fixed in `parse_sids.py`, not yet
   in the corpus.** `strip_fig_captions()` strips both styles but keeps a
   number the text refers to ("see Fig. D27-0007-13", "wiring diagrams
   W54-2328-71, … and …"): of 34,294 in the corpus ~50 are kept. To land:
   `python extractor/reparse_bodies.py --dry-run`, then without
   `--dry-run`, then `apply_images.py`, then `rebuild_wis.py`. The
   dry-run used to report every illustrated body (139,273) as losing text:
   its check ignored `[figure:]` but not `[diagram:]` markers -- the old
   parser failed it too (973 of 3,000 sampled). Fixed; on that sample both
   parsers lose 0 words. The full 18-minute dry-run has not been re-run
   since the fix.

6. **Sedan/wagon split and the other-engine filter are built but not run on
   the real data** (see their sections). Next: `parse_vehicles.py` (read
   its "body styles" lines), `rebuild_wis.py`, `test_ui_picks.py`,
   `eval_wis.py`; then one scoped paid answer for a W210 wagon.

7. **Shop access + HTTPS are built (2026-10-09), not yet tried on the
   user's phones.** Next: `serve.py --https`, install `/ca.crt` on an iPhone
   and an Android phone, sign one in with a code. Run `test_ui_picks.py`
   against the real index too (the header/layout CSS changed; not run here,
   no real index in that session).

8. **The QA review's findings are fixed (2026-10-09), on branch
   `claude/qa-agents`, not merged.** `docs/qa/REPORT.md` has every finding
   and its status; the four agents are `.claude/agents/qa-*.md`. Checked in
   a cloud container on `dev.py` sample data (invented text, no variants)
   with the free suites and browser checks. **Not run there, run them on
   the real index before merging:** `test_ui_picks.py` (car box, VIN and
   Browse changed), `eval_wis.py`, `eval.py`; and look at Settings, the
   Import dialog and the light theme (new; follows the device) on the PC
   and a phone. Since then (2026-10-10) Shop setup replaced the Settings
   dialog -- see "Importing from the site". The MSI build now checks python.org's Sigstore signature on
   the embeddable Python against python.org's release signers (`$signers`
   in `build.ps1`; 3.13 is thomas@python.org via Google -- the first CI
   run assumed the Windows signer and failed). A new signer fails the build
   with sigstore naming who signed: add that line. `-NoVerify` skips it.

## Working style the user prefers

- Verify claims by running things, not by asserting them. Several bugs here
  were found only because a "done" result was actually tested.
- Say plainly when something is broken, unverified, or self-inflicted.
- Don't spend their API credit to prove a UI change works.
