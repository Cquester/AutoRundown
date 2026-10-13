# AutoRundown — working notes for Claude

Local repair-manual search over the user's own service-manual archive.
Retrieval is local (SQLite FTS5); the only network call is to the Anthropic
API. Read `README.md` for the product description — this file is the working
context: what is already decided, what is already tried, and what will break.

**Status: working end to end.** Importers (LEMON HTML, Mercedes WIS via
`records`, plain folders), index, retrieval, Claude query layer, web UI with
Shop setup, shop-network access, Windows MSI with CI and releases. Not built:
macOS/Linux packages (they run from source, by the user's decision), code
signing, the LEMON torrent download.

**This file is the short version.** The history and measurements behind each
area live in `docs/notes/` (moved there verbatim on 2026-10-12). **Before
changing an area, read its note** — most rules there exist because something
broke:

| Before you touch… | read |
|---|---|
| the WIS extractor, `importers/records.py`, WIS page rendering, images, "Original layout", `wis_links.py` (cross-refs, diagrams, option codes, oil, labour) | [`docs/notes/wis-source.md`](docs/notes/wis-source.md) |
| the car box, Browse, Variant menu, VIN, years, engines, `search.scope_ids`, `Scope` filters | [`docs/notes/vehicle-selection.md`](docs/notes/vehicle-selection.md) |
| `auth.py`, sign-in, spend caps, HTTPS, request hardening | [`docs/notes/shop-network.md`](docs/notes/shop-network.md) |
| Shop setup, import jobs, removing manuals, the LEMON tab / catalogue, the VM import | [`docs/notes/importing.md`](docs/notes/importing.md) |
| `packaging/`, the launcher / tray / autostart / mDNS, CI workflows, releases | [`docs/notes/windows-installer.md`](docs/notes/windows-installer.md) |
| `tools/container/` (test boxes), `tools/sandbox/` (Windows Sandbox) | [`docs/notes/test-environments.md`](docs/notes/test-environments.md) and `tools/container/README.md` |

---

## Repo layout

```
autorundown/        the app; everything the MSI ships (see Architecture)
serve.py ingest.py ask.py rebuild_wis.py remove_manuals.py
                    command-line entry points; the app runs ingest / rebuild_wis /
                    remove_manuals as child processes by path, so they stay at the root
dev.py              throwaway instance with sample data (.dev/, port 8757)
eval.py eval_wis.py retrieval checks against the real index (free)
tests/              free test suites, `python tests/test_<name>.py`
                    (tests/_paths.py puts the root and tools/ on sys.path)
tools/              make_sample_corpus.py, make_sample_lemon.py, readme_shots.py,
                    check_js.py, build_lemon_catalogue.py, build_us_years.py,
                    sids_render.py; container/ (test boxes); sandbox/
extractor/          Mercedes WIS extractor (its data stays in ../Mercedes Documents/)
packaging/windows/  MSI (WiX 5), build.ps1, ui_check.py (first-start UI check)
docs/               notes/ (deep notes), qa/ (QA reports), images/ (README),
                    SETUP-ON-NEW-PC.md, MERCEDES_WIS_EXTRACTION.md
.claude/            QA agents + runbook, launch.json
```

**A new free test goes in `tests/` and in the "Test suites" loop in
`.github/workflows/ci.yml`** (the test box's `check.sh` runs every
`tests/test_*.py` by itself). A new script the installed app runs must also be
added to the file list in `packaging/windows/build.ps1` -- `remove_manuals.py`
was missing from the MSI until 2026-10-12, so removing manuals failed in the
installed app; `windows-msi.yml` now checks it is installed.

---

## If you are in a OneDrive copy

If you are running inside `OneDrive\AutoRundown`, **set up a local copy before
doing anything else.** Do not build the index or run the server from there:
SQLite writes on every question, and a sync daemon touching an open database
corrupts it. `docs/SETUP-ON-NEW-PC.md` has the steps; the one that matters is
`python ingest.py --archive <local manuals> --index index.db --repath` — page
*text* is in the index, but *images* are served from the archive on disk by
the absolute path of whichever machine built it.

---

## Testing a change: start here

Three places to test, each for one job. **All free**; none can spend API
credit.

1. **Quick checks on the PC**: the free suites (`python tests/test_records.py`
   etc.) and `eval.py` / `eval_wis.py` against the real index (read-only).
   No server needed.
2. **A test box for anything "like a user", and for every agent**:
   `wsl -d Ubuntu -- bash tools/container/box.sh up` starts your own copy of
   the app in Docker, with sample data, no network, nothing of the user's.
   Keep the `BOX name=… admin=… phone=…` line it prints: **admin** is "this
   PC", **phone** is a mechanic's phone. `box.sh check <name>` runs every
   CI check inside it; `down <name>` when finished -- your box only,
   several chats share this PC. Hand agents the `BOX` line; QA agents read
   `.claude/qa/RUNBOOK.md`. **How-to and rules: `tools/container/README.md`.**
   WSL stops (and the box with it) a few seconds after the last Windows
   command: while using a box, keep a background
   `wsl.exe -d Ubuntu --exec sleep infinity` running. A box is a snapshot of
   the working tree: after changing code, `down` and `up` again.
3. **Windows Sandbox for the installer** (`tools/sandbox/start.ps1`): the
   real MSI on a clean Windows, when packaging, the wizard or the tray
   changed.

Never test against the real server (port 8756), the real index or
`~/.autorundown` by starting servers on the PC: use a box. Show the user a
change by giving them the box's admin (or phone) address.
**The user prefers local sessions to cloud sessions (2026-10-09): never start
a cloud session or remote agent without asking.**

---

## Commands

```bash
python serve.py                                   # web UI, localhost + LAN
python serve.py --https                           # TLS from a local CA (tls.py)
python -m autorundown.launcher                    # what the Windows shortcut runs
python ingest.py --archive <dir> --index index.db # build index (~40s)
python ingest.py --archive <dir> --repath         # fix archive path only
python ask.py --vehicle f-150 "<question>"        # CLI, costs money
python ask.py --vehicle f-150 --search "<terms>"  # keyword only, FREE
python ask.py --usage                             # spend, by source and user
python remove_manuals.py --doc-id <id> [--compact]  # remove a LEMON / folder manual
python remove_manuals.py --source mercedes-wis    # remove all WIS (never one model)
python rebuild_wis.py                             # replace the WIS part (~6 min; keeps spend log)
python dev.py                                     # FREE, throwaway instance, :8757
python eval.py                                    # FREE retrieval check (F-150)
python eval.py --answers                          # PAID, prints estimate
python eval_wis.py                                # FREE, WIS retrieval + wrong-vehicle guards

# free test suites (CI runs all of them)
python tests/test_citations.py                    # _verify_citations (<1 s)
python tests/test_records.py                      # `records` importer
python tests/test_access.py                       # admin/sign-in/limits/HTTPS (~2 s)
python tests/test_lemon_import.py                 # LEMON import source (~3 s)
python tests/test_lemon_catalogue.py              # LEMON tab's catalogue tree (~3 s)
python tests/test_remove_manuals.py               # removing manuals (~20 s)
python tests/test_wis_links.py                    # WIS cross-refs/diagrams/codes/oil (~5 s)
python tests/test_ui_picks.py [--index <db>]      # UI sends the car it shows (~45 s, Playwright)
python tools/check_js.py                          # index.html's JS parses (needs node)

# sample data and tools
python tools/make_sample_corpus.py                # stand-in WIS corpus -> sample_corpus/
python tools/make_sample_lemon.py                 # stand-in LEMON archive -> sample_lemon/
python tools/readme_shots.py --url <box admin url> # README screenshots -> docs/images/
python tools/build_lemon_catalogue.py <lemon/index.json>  # rebuild the catalogue snapshot
python extractor/parse_links.py                   # rebuild wis_links.sqlite (offline, ~1 min)

# test environments
wsl -d Ubuntu -- bash tools/container/box.sh up     # test box (Docker in WSL): admin + phone ports
wsl -d Ubuntu -- bash tools/container/box.sh check <name>  # every CI check inside it (~35 s)
powershell -File tools/sandbox/start.ps1 [-Source] # fresh install in Windows Sandbox, :18756
```

**`python dev.py` is what a test box runs** (on the PC only for a quick look
when Docker/WSL is unavailable). It builds sample data (invented text) into
`.dev/`, runs with its own `AUTORUNDOWN_HOME` there and serves on
localhost:8757, so the real index, spend log and the real server on 8756 are
never touched. It drops `ANTHROPIC_API_KEY`, so Ask costs nothing unless
someone types a key into its Shop setup. One of the three sample LEMON
vehicles is imported, two are left for the import page; `--empty` gives the
first-start checklist, `--reset` rebuilds. The sample WIS corpus uses the
real model catalogue when `../Mercedes Documents/autorundown_out/wis_applic/models.jsonl`
exists, so the picker and VIN lookup behave as on the real corpus; the
document text is always invented.

The Mercedes WIS pipeline (VM -> corpus -> index) and its commands are in
`docs/notes/wis-source.md`. One rule from it belongs here:

**Never rebuild by copying `index.before-wis.db` over `index.db` by hand.**
The spend log (`usage_log`) lives inside the index, so that silently deletes
every paid question asked since the backup — it happened on several rebuilds
in this project before `rebuild_wis.py` existed. The script copies the newer
log rows across and checks the count before it re-ingests.

---

## Spend discipline — read this before running anything

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
  importers/       archive-specific code lives ONLY here
    base.py          Importer interface + VehicleRecord / PageRecord
    lemon.py         LEMON HTML manual archives
    records.py       normalized corpus: records.jsonl + manifest.json (WIS)
    folder.py        generic fallback: HTML / text / PDF
    data/            hand-made reference tables (years, engines, US years, LEMON catalogue)
  extract.py       HTML -> text; tables -> pipe tables; image + link refs
  index.py         FTS5 schema, chunking, dedup, connectors, usage log, migrations
  search.py        BM25 + mandatory vehicle filter, scopes, round-robin merge
  synonyms.py      shop words -> manual words for keyword search
  query.py         Claude planner + answerer + citation / other-vehicle checks
  server.py        FastAPI local server; admin-only routes, hardening
  auth.py          the PC is admin; mechanic codes, tokens, caps
  tls.py           local CA for --https
  config.py        ~/.autorundown/config.json (AUTORUNDOWN_HOME)
  importer_jobs.py import / remove / VM jobs started from Shop setup
  lemon_catalogue.py  LEMON torrent catalogue tree and picks
  wis_links.py     WIS cross-refs, diagram hotspots, option codes, oil, labour
  wis_original.py  WIS "Original layout" renderer
  launcher.py autostart.py mdns.py   Windows tray, start at sign-in, autorundown.local
  web/index.html   the whole UI, one file
```

Everything downstream of `importers/` reads only the normalized records. A new
archive format = one new `Importer` subclass, nothing else changes. Keep that
boundary.

---

## Decisions already made — do not silently undo these

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
`tests/test_citations.py`. Not yet seen on a paid answer -- if the model often
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
`tests/test_records.py` ("answers keep only the chosen car's values"). **Not yet
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

More decisions of the same weight, each with the failure that caused it, are
in `docs/notes/`. The ones most likely to be undone by accident:

- **WIS model filing uses schema 1.2's WS/WC join, checked against page
  text** (98.4% wrong-vehicle under 1.1); `eval_wis.py` checks page *text*,
  never buckets. Slash lists like `MODEL 639.601/603` are variants, not
  series. (`wis-source.md`)
- **Do not look again** for per-variant model years in WIS, for EPC data
  cards (access-protected: a deliberate lock, do not get around it), or for
  option-code texts in EPC (they come from WIS). (`vehicle-selection.md`)
- **Years come from the hand-made `importers/data/mercedes_years.csv`**,
  filtered exactly; WIS catalogue dates are documentation validity and hide
  cars. Add a row when a new model appears. (`vehicle-selection.md`)
- **`_variants` order is the server's order — never sort it in place** (the
  E 500 → E 63 AMG bug); `tests/test_ui_picks.py` guards it.
  (`vehicle-selection.md`)
- **Admin = loopback peer AND loopback Host**; no reverse proxy on the same
  PC; no handler built from manual text. (`shop-network.md`)
- **WIS is never removed per model.** (`importing.md`)
- **Never fetch from lemon-manuals.la over HTTP**; the LEMON download, when
  built, must follow what the user agreed (2026-10-09). (`importing.md`)
- **Embeddable Python, not a frozen exe; not a Windows service.**
  (`windows-installer.md`)

---

## Archive-specific traps

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
   on the F-150. `python eval_wis.py` — free, 19 WIS cases (~15 s): expected
   DokIDs plus wrong-vehicle guards that read page *text*. Guards that fail
   today are a ratchet (`KNOWN_BAD` ceilings): more offenders fails the run,
   fewer prints IMPROVED — lower the ceiling when it does.
2. The free suites in `tests/`, or all of CI at once in a test box
   (`box.sh check <name>`).
3. For UI work: drive a test box with the browser tools, and inject
   payloads into `renderAnswer()` rather than paying for answers.
   After any change to the vehicle picker (`#carInput`, Browse, VIN, the
   variant menu, `loadVehicles`): `python tests/test_ui_picks.py` — free,
   ~45 s, needs `pip install -r requirements-dev.txt` (Playwright; it drives
   the installed Edge/Chrome). It loads the real page against the index
   (opened `mode=ro`; `--index` to point elsewhere), makes every pick the UI
   can make — typed queries, VIN and FIN, Browse + reload, every one-line
   suggestion (~2,500), every Variant option (~1,600), one VIN prefix per
   variant (~430) — and checks the `variant=` each sends against the stored
   `vehicles.variants`. Any non-GET or `/api/ask` request fails the run.
   Known quirk it works around: typing "190 e 2.3 16" finds nothing —
   `carMatches` reads a bare "16" as the year 2016.
4. For extraction changes: re-index (~40s) and re-run `eval.py`. Watch
   `unique_pages` — if it moves, dedup behaviour changed and that should be
   deliberate.
5. Only then, if prompts or schema changed: `eval.py --answers`.

Current baseline: 45,507 pages → 16,034 unique → 21,401 chunks, 189 MB, ~40 s.
Eval 7/7 on both Sonnet 5 and Haiku 4.5.

With Mercedes WIS (English, passenger mode, schema 1.3 with images) in the
same `index.db`: 272,607 records = 272,603 indexed + 4 figure-only, 531,847
page rows (x1.95), index 3.5 GB, ~6 min via `rebuild_wis.py`. Images live
outside the index: 272,181 graphics, 11 GB, in `wis_extract/images/`.
`index.before-wis.db` is the pre-WIS backup the script restores;
`index.v11.db` is the last schema-1.1 index, kept for rollback (both
gitignored). `eval_wis.py`: 19/19 pass, 13 known-bad guards within their ceilings (checked 2026-10-12).

---

## Known gaps

## Known gaps

- `eval.py` cases are archive-specific (2015 F-150 page numbers). They will
  fail against any other archive; that is documented in the file.
- Sibling-engine pages exist in the main tree identified only by their title
  (e.g. "Generator - 2.7L EcoBoost"). The prompt requires flagging
  applicability, but this is prompt-level, not structural.
- The spend log lives inside the index. `rebuild_wis.py` carries it across a
  WIS rebuild; any *other* way of rebuilding (deleting `index.db`, copying a
  backup over it) still loses it.
- **WIS retrieval is checked by `eval_wis.py`, not by answers.** 19 cases,
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

## Where we left off (updated 2026-10-12) — start here in a new session

Merged to `main` since the last update: the QA fixes (PR #6), the LEMON
catalogue (#5), sandbox + CI (#7), WIS reference tables (#8), labour times
(#10), test boxes (#9), and this tidy-up (tests/, tools/, docs/notes/).
Still open, in rough order of value:

1. **Sedan/wagon split and the other-engine filter are built but not on the
   real data.** Checked 2026-10-12: the real `index.db` has no "Wagon"
   variant yet. Next: `python extractor/parse_vehicles.py` (read its "body
   styles" lines), `python rebuild_wis.py`, `python tests/test_ui_picks.py`,
   `python eval_wis.py`; then one scoped paid answer for a W210 wagon (ask
   first).
2. **Answers over WIS have never been tried** (see Known gaps). One cheap,
   scoped paid question — with the user's OK — is the next real test of the
   answer prompt on that source.
3. **Shop access + HTTPS not yet tried on the user's phones.**
   `serve.py --https`, install `/ca.crt` on an iPhone and an Android phone,
   sign one in with a code.
4. **Older figure numbers ("P01-5220-13"): fixed in `parse_sids.py`, not yet
   in the corpus.** To land: `python extractor/reparse_bodies.py --dry-run`
   (18 min; not re-run since its check was fixed), then without
   `--dry-run`, then `apply_images.py`, then `rebuild_wis.py`.
5. **"Original layout"**: not yet seen by the user on their phone. Open: the
   undecoded MATH/SYMBOLS glyphs and most LOGO icons, RTF hotspots as links,
   links inside the iframe, tap targets in the full-size diagram view, and
   the hotspot junk in `body_text` (`parse_sids.py`).
6. **The extractor has not been re-run end to end from the repo copy** (the
   paths resolve and the evals pass; the next VM run is the real check).
7. The car box reads a bare two-digit word as a year ("190 e 2.3 16" → 2016).
8. The LEMON download is not built (catalogue picking only); sizing it needs
   piece 16308, which the user has not yet OK'd fetching.

Leftovers the user may delete (we never delete data):
`autorundown_out/_previous_2026-10-07` (~15 GB), `index.v11.db`, the old
`../Mercedes Documents/autorundown_extractor` copy, and the merged local
`claude/*` branches.

---

## Working style the user prefers

## Working style the user prefers

- Verify claims by running things, not by asserting them. Several bugs here
  were found only because a "done" result was actually tested.
- Say plainly when something is broken, unverified, or self-inflicted.
- Don't spend their API credit to prove a UI change works.
- Keep docs in step with the code: a change that moves a file, renames a UI
  label or adds a command updates README.md, this file and the matching
  `docs/notes/` file in the same PR.
