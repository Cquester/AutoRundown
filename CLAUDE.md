# AutoRundown — working notes for Claude

Local repair-manual search over the user's own service-manual archive.
Retrieval is local (SQLite FTS5); the only network call is to the AI
provider's API (Anthropic by default; Gemini, xAI or DeepSeek by setting). Read `README.md` for the product description — this file is the working
context: what is already decided, what is already tried, and what will break.

**Status: working end to end.** Importers (LEMON HTML, Mercedes WIS via
`records`, plain folders), index, retrieval, AI query layer (four providers), web UI with
Shop setup, shop-network access, Windows MSI (built on the PC), CI, "Add a
car" from LEMON's zip (the app opens LEMON's page, the user saves the zip,
the app imports it), one car from Operation CHARM's torrent (built, hidden:
hours per car). Not built: macOS/Linux packages (they run from source, by the
user's decision), code signing, the LEMON torrent download (not practical per
car; `importing.md`).

**This file is the short version.** The history and measurements behind each
area live in `docs/notes/` (moved there verbatim on 2026-10-12). **Before
changing an area, read its note** — most rules there exist because something
broke:

| Before you touch… | read |
|---|---|
| the WIS extractor, `importers/records.py`, WIS page rendering, images, "Original layout", `wis_links.py` (cross-refs, diagrams, oil, labour) | [`docs/notes/wis-source.md`](docs/notes/wis-source.md) |
| the car box, Browse, Variant menu, VIN (check digit, Scan VIN, `web/vendor/`), years, engines, `search.scope_ids`, `Scope` filters | [`docs/notes/vehicle-selection.md`](docs/notes/vehicle-selection.md) |
| `auth.py`, sign-in, spend caps, HTTPS, request hardening, `handoff.py` (send to desk) | [`docs/notes/shop-network.md`](docs/notes/shop-network.md) |
| Shop setup, import jobs, removing manuals, the LEMON tab / catalogue, the VM import, the CHARM download (`charm.py`, `charm_fetch.py`, `charm_download.py`) | [`docs/notes/importing.md`](docs/notes/importing.md) |
| `packaging/`, the launcher / tray / autostart / mDNS, CI workflows, releases | [`docs/notes/windows-installer.md`](docs/notes/windows-installer.md) |
| `tools/container/` (test boxes), `tools/sandbox/` (Windows Sandbox) | [`docs/notes/test-environments.md`](docs/notes/test-environments.md) and `tools/container/README.md` |
| `providers.py`, the AI provider selector, prices, `tools/fake_anthropic.py`'s provider fakes | [`docs/notes/ai-providers.md`](docs/notes/ai-providers.md) |
| the EPC parts source — `extractor/pull_epc.js`, `epc_fv4.js`, the EPC↔WIS join (`index.link_sources`) (not in the live index yet) | [`docs/notes/epc-source.md`](docs/notes/epc-source.md) |

---

## Repo layout

```
autorundown/        the app; everything the MSI ships (see Architecture)
serve.py ingest.py ask.py rebuild_wis.py remove_manuals.py charm_download.py unzip_lemon.py
                    command-line entry points; the app runs ingest / rebuild_wis /
                    remove_manuals / charm_download / unzip_lemon as child processes
                    by path, so they stay at the root
dev.py              throwaway instance with sample data (.dev/, port 8757)
eval.py eval_wis.py retrieval checks against the real index (free)
tests/              free test suites, `python tests/test_<name>.py`
                    (tests/_paths.py puts the root and tools/ on sys.path)
tools/              make_sample_corpus.py, make_sample_lemon.py, readme_gifs.py,
                    check_js.py, build_lemon_catalogue.py, build_us_years.py,
                    sids_render.py; container/ (test boxes); sandbox/
extractor/          Mercedes WIS extractor (its data stays in ../Mercedes Documents/)
packaging/windows/  MSI (WiX 5), build.ps1, ui_check.py (first-start UI check)
docs/               notes/ (deep notes), qa/ (QA reports), images/ (README),
                    SETUP-ON-NEW-PC.md, MERCEDES_WIS_EXTRACTION.md
.claude/            QA agents + runbook, launch.json
```

**GitHub Actions is turned off until the repo is public** (the user,
2026-10-23: the private repo hit its Actions billing limit). Both workflows
are disabled on GitHub (`gh workflow disable`), so a PR shows no checks.
**Before asking to merge, run every CI check locally** in a test box
(`box.sh check <name>`) and say so in the PR. Keep `ci.yml` up to date anyway
(new tests still go in its loop); once public: `gh workflow enable CI`.

**After a PR is merged to `main`, build and install the MSI on this PC**
(the user, 2026-10-23: CI no longer builds it). From an up-to-date `main`:
`packaging\windows\build.ps1`, then `msiexec /i dist\AutoRundown-<ver>.msi
AUTOSTART=1 /passive`; steps in `docs/notes/windows-installer.md`. Do not add
the MSI build back to a workflow.

**A new free test goes in `tests/` and in the "Test suites" loop in
`.github/workflows/ci.yml`** -- or, if it drives a browser (Playwright), as
a step in the `browser` job there, with `--index "$RUNNER_TEMP/smoke.db"`
(the test box's `check.sh` runs every
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
   **Ask works there for free**: a fake Anthropic API in the box
   (`tools/fake_anthropic.py`) answers from the passages the app sends.
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
python remove_manuals.py --doc-id <id> [--compact]  # remove a LEMON / folder manual (--compact: shrink the index)
python remove_manuals.py --source mercedes-wis    # remove all WIS (never one model)
python rebuild_wis.py                             # replace the WIS part in place (keeps other manuals + spend log)
python charm_download.py --make "Mercedes Benz" --year 1987 --car "<CHARM name>" --torrent <operation-charm.torrent> --plan
                                                  # one car from CHARM's swarm: --plan sizes it; without it downloads (never seeds)
python dev.py                                     # FREE, throwaway instance, :8757
python dev.py --fake-api                          # FREE, Ask answered by tools/fake_anthropic.py (all 4 providers)
python dev.py --fake-vm                           # FREE, Mercedes VM tab runs tools/fake_extractor
python eval.py                                    # FREE retrieval check (F-150)
python eval.py --answers                          # PAID, prints estimate
python eval_wis.py                                # FREE, WIS retrieval + wrong-vehicle guards

# free test suites (CI runs all of them)
python tests/test_citations.py                    # _verify_citations (<1 s)
python tests/test_records.py                      # `records` importer
python tests/test_access.py                       # admin/sign-in QRs/limits/keys/HTTPS (~4 s)
python tests/test_lemon_import.py                 # LEMON import source (~3 s)
python tests/test_lemon_catalogue.py              # LEMON tab's catalogue tree (~3 s)
python tests/test_catalogue_suggest.py            # car box: LEMON cars not downloaded, Add this car (~5 s)
python tests/test_remove_manuals.py               # removing manuals (~20 s)
python tests/test_wis_links.py                    # WIS cross-refs/diagrams/codes/oil (~5 s)
python tests/test_wis_tables.py                   # Torque/Capacities tabs: job values, car's column, fluids (~5 s)
python tests/test_parts_diagrams.py               # Parts tab: EPC drawings, browse by group (~10 s)
python tests/test_synonyms.py                     # shop words -> WIS and EPC words (<1 s)
python tests/test_job_numbers.py                  # old job numbers (67-200), title matches in search (<2 s)
python tests/test_fake_api.py                     # Ask end to end vs the fake API (~5 s)
python tests/test_providers.py                    # Ask on all 4 AI providers vs the fakes (~20 s)
python tests/test_epc_join.py                     # EPC parts joined into WIS cars (~5 s)
python tests/test_vm_jobs.py                      # VM import WIS/EPC/Both + progress, fake extractor (~12 s)
python tests/test_handoff.py                      # phone -> desk cars: who sees what (~2 s)
python tests/test_history.py                      # question history: answers kept whole, who sees what (~2 s)
python tests/test_chat.py                         # AI chat follow-ups vs the fake API, who may follow up (~5 s)
python tests/test_numbers.py                      # typed part / document numbers stay whole; words stay words (<1 s)
python tests/test_part_procedures.py              # a part's repair procedures: own group first, jobs first (<1 s)
python tests/test_vin.py                          # VIN check digit, I/O/Q, typos (~5 s)
python tests/test_vin_scan.py [--n 8]             # Scan VIN on generated barcodes/labels (~1 min, Playwright)
python tests/test_ui_picks.py [--index <db>]      # UI sends the car it shows (~3 min, Playwright)
python tests/test_car_history.py [--index <db>]   # recent cars: re-select sends the same car (~10 s, Playwright)
python tests/test_answer_tabs.py [--index <db>]   # one question fills the right box's tabs (~15 s, Playwright)
python tests/test_drawing_ui.py                   # Manual contents, drawing picks (red, several), divider (~30 s, Playwright)
python tests/test_rebuild_wis.py                  # WIS rebuild keeps EPC, other manuals, spend log (~5 s)
python tests/test_charm.py                        # one CHARM car: reader, plan, download, import, refusals (~10 s)
python tests/test_lemon_zip.py                    # "Add a car": LEMON zip found, unsafe zips refused, unpack + import (~10 s)
python tests/test_parts_fit.py                    # parts pages/lines that fit the car: cut code lists, other models' lines (<1 s)
python tools/check_js.py                          # index.html's JS parses (needs node)

# sample data and tools
python tools/make_sample_corpus.py                # stand-in WIS corpus -> sample_corpus/
python tools/make_sample_lemon.py                 # stand-in LEMON archive -> sample_lemon/
python tools/make_sample_epc.py                   # stand-in EPC parts + drawings -> sample_epc/ (dev.py ingests it)
python tools/make_sample_charm.py                 # stand-in CHARM torrent data -> sample_charm/ (dev.py's CHARM card uses it)
python tools/readme_gifs.py --url <box admin url>  # README GIFs -> docs/images/ (free; --answer is PAID)
python tools/build_lemon_catalogue.py <lemon/index.json>  # rebuild the catalogue snapshot
python extractor/parse_links.py                   # rebuild wis_links.sqlite (offline, ~1 min)
python tools/wis_job_numbers.py --index index.db [--apply]  # add "Job no.: 67-200" to an existing index's WIS pages

# test environments
wsl -d Ubuntu -- bash tools/container/box.sh up     # test box (Docker in WSL): admin + phone ports
wsl -d Ubuntu -- bash tools/container/box.sh check <name>  # every CI check inside it (~35 s)
powershell -File tools/sandbox/start.ps1 [-Source] # fresh install in Windows Sandbox, :18756
```

**`python dev.py` is what a test box runs** (on the PC only for a quick look
when Docker/WSL is unavailable). It builds sample data (invented text) into
`.dev/`, runs with its own `AUTORUNDOWN_HOME` there and serves on
localhost:8757, so the real index, spend log and the real server on 8756 are
never touched. It drops every provider's `*_API_KEY` / `*_BASE_URL`, so Ask
costs nothing unless someone types a key into its Shop setup. `--fake-api`
(what a box runs) points all four providers at `tools/fake_anthropic.py` on
port+1 (`ANTHROPIC_BASE_URL`, `GEMINI_BASE_URL`, `XAI_BASE_URL`,
`DEEPSEEK_BASE_URL`), so Ask runs its whole real path -- planner, search,
`_verify_citations`, spend log, UI -- with made-up replies on any provider,
and even a real key typed into Settings goes only to the fake (only the
Anthropic key is preset; type any dummy key for the others). Words in a
question pick the rare paths on every provider (`fake:badcite`,
`fake:steps`, `fake:notfound`, `fake:refusal`, `fake:error`,
`fake:nocredit`, `fake:slow`, `fake:malformed`, `fake:otherengine`). It proves the app's plumbing,
not the real model's judgement: prompt changes still need one scoped paid
answer. One of the three sample LEMON
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
in this project before `rebuild_wis.py` existed. It also deletes the EPC
parts catalogue and every manual imported after the backup -- which is what
`rebuild_wis.py` itself did until 2026-10-16. **The script now removes WIS
in place** (`remove_manuals.py --source mercedes-wis`) and re-ingests; it
never reads the backup, refuses a corpus that is not WIS, and checks every
other manual's page/chunk counts and the spend log before and after
(`tests/test_rebuild_wis.py`).

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
- **Test Ask in a test box, where the fake API answers it** (or inject a
  payload into `renderAnswer()` from the console) rather than asking a real
  question. Free, and it exercises the same code.
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
  numbers.py       typed part numbers (one term, every printed form) and document numbers
  part_procedures.py  a parts subgroup's repair procedures: its group's manual pages by name
  query.py         planner + answerer + citation / other-vehicle checks
  providers.py     the AI providers (Anthropic SDK; Gemini, xAI, DeepSeek over HTTPS),
                   their models, prices, schemas and errors
  server.py        FastAPI local server; admin-only routes, hardening
  auth.py          the PC is admin; sign-in QRs (grants), tokens, caps
  handoff.py       cars a phone sends to the desk (auth.db `handoffs`)
  qr.py            QR codes as inline SVG (segno, pure Python)
  tls.py           local CA for --https
  config.py        ~/.autorundown/config.json (AUTORUNDOWN_HOME)
  importer_jobs.py import / remove / VM jobs started from Shop setup
  lemon_catalogue.py  LEMON torrent catalogue tree and picks; LEMON / CHARM page links, zip names
  lemon_zip.py     a LEMON car's zip from Downloads: found, checked (untrusted), unpacked
  charm.py         Operation CHARM's data (SquashFS + LMDB) read a piece at a time; LEMON-style export
  charm_fetch.py   CHARM torrent pieces from the swarm (libtorrent, never seeds) or a local copy
  wis_links.py     WIS cross-refs, diagram hotspots, oil, labour
  wis_original.py  WIS "Original layout" renderer
  html_original.py LEMON / folder pages' "Original layout": the page's own HTML, allow-listed
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
`tests/test_citations.py`. A part number in the answer's text or a spec must
be printed on a page it cites (`_check_part_numbers`, 2026-10-28: Haiku named
the rear pad kit, from an uncited page, as a superseded front pad); one that
isn't is taken out and said so. Not yet seen on a paid answer -- if the model often
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

**The answer points at the manual page; it does not retell it** (the user,
2026-10-13: "it gets really messy having AI respond and have a bunch of images
on the side ... it's more to provide the correct document"). An answer is one
to three sentences, a table of the values the job uses (`specs`), and
`document_page` -- the procedure page for "how do I", the spec page for a
value -- which the UI opens on the right (on a phone, 700 px and less: loaded
into the "Document" view with a dot on that segment, opened by the answer's
"Open manual page" button, so it does not cover the answer; Back or a swipe
returns). An answer with no page clears the panel to "No manual page for this
answer". A withheld answer (`caveats` ends "Answer withheld: …"; there is no
separate field) is headed "No checked answer — the AI's sources couldn't be
verified", with "Search the manual instead", never "Not found".
The phone layout (car line + full-screen car sheet, Answer | Document, docked
question, bottom tab bar) is CSS under `@media (max-width:700px)` plus
`openRight`/`openSheet` in `index.html`; 701-900 px keeps the slide-over panel. `steps` is left empty by the prompt and not rendered; the
side panel of "diagrams from cited pages" is gone; `figures` survive only as
links to a diagram on another page. `document_page` is checked like any
citation in `_verify_citations` (invented -> dropped, falls back to the first
real cited page; never counts on its own). A value is never "see Fig 15" --
that rendered in big type as though it were a torque. **Not yet tried on a
paid answer.**

**The extractor never powers the VM off; the user picks and checks the VM;
every run can be cancelled** (2026-10-16). A forced power-off after the
database didn't answer left the real VM's Windows 7 in Startup Repair. Now
`vm_down` saves the VM's state when ACPI shutdown doesn't finish, a saved
state is resumed (not discarded), and Windows not starting (Guest Additions
run level) stops the run with a screenshot of the VM's screen. Do not bring
back `controlvm poweroff` or `discardstate`. Before a run boots the VM it
takes its own snapshot ("AutoRundown-auto-…", one per 6 h, never deleted
automatically) and Shop setup can roll back to any snapshot, snapshotting
the current state first. The VM is chosen as its `.vbox` file in a
browser like the Folder tab's (the user, 2026-10-23: no list filled in for
them), remembered as `Config.vm_file`; nothing is preselected, and a VM
VirtualBox doesn't know is refused (never registered for them). Every
program a job starts is started with no terminal window (`NO_WINDOW`;
the installed app opened one per VBoxManage / Java call);
`vm_pipeline.py --check` (and `--check --boot`, "Test the VM") gates a run;
Cancel stops the pull and shuts the VM down cleanly, and the extractor stops
by itself if the server goes away. Details: `docs/notes/importing.md`.

**One question fills the right box's tabs, without another AI call**
(2026-10-16, the user's request). The right box has tabs -- Manual page,
Parts, Labour times, Capacities, Torque specs; on a phone they are the bottom
bar and open the Document view. Every Ask searches Parts and Labour with the
AI's own search terms (`queries`), every Search with the words typed
(`/api/search` `also=`, merged round-robin). The user chose this over the AI
writing each tab: 3-5x the spend, and it would retell the manual. Do not add
per-tab AI calls. Details: `docs/notes/epc-source.md` ("Result tabs").

**A paid answer is kept and shown again for free** (2026-10-23, the user:
"if we are having people pay for ai requests then it should be
retrievable"). Every Ask is stored whole in `auth.db` `history`
(`history.py`); opening it from History redraws the stored answer and
never calls the AI again. Who sees which entries follows sign-in (the PC
all, a mechanic their own). Do not make "open from history" re-ask.
Tests: `tests/test_history.py`. Details: `epc-source.md` ("Wider, Clear
question, History").

**AI chat: follow-ups answer from the manual only, checked like the first
answer** (2026-10-25, the user's request). The left side has two tabs, AI
chat and Keyword search. A chat's first answer is the usual brief + page;
a follow-up ("tests 1-4 OK, 0 V at A5 -- the culprit?") is answered from
the passages with `FOLLOWUP_SYSTEM`, the pages the chat cited sent again
from the index, through the same `_verify_citations` and checks. A chat is
one `history` entry; a follow-up's car and earlier turns come from that
entry, never from the request. Earlier answers are never a source. Do not
let a follow-up skip a check or answer from general knowledge. Tests:
`tests/test_chat.py`. First paid chat (Haiku, $0.032): an F-150 J2 reading
of 50 ohms was answered "go to J3", the page's own branch. Details:
`epc-source.md` ("AI chat and Keyword
search").

**An answer also sees the procedures its pages link to; still one AI call**
(2026-10-17, the user's request). Search finds only pages sharing the
question's words, so "replace oil level sensor" never reached "Remove/install
bottom part of oil pan", which its page links. `query.with_linked` adds the
in-scope AR/BA/BF documents linked from the top 3 pages *about the question*
(a title word in common; `_fits_car` = search's engine/code filters),
at most 4, after the first 6 passages, each leading with its tables' values
for this car (`wis_tables`), marked "linked from page N". Haiku still left
the pan screws out (twice, even with a prompt line asking), so
`_add_linked_torques` adds a cited page's linked torques after the model,
like `_add_omitted_parts` (`added_linked`, said in caveats). Checked with
Haiku: sensor 10 Nm + pan screws 14/10 Nm + drain 30 Nm, ~$0.018 per answer.
Tests: `tests/test_wis_tables.py`.

**Torque specs and Capacities show the job's values, in the car's column**
(2026-10-16, found on the live W210 index: "Oil level sensor … 20 Nm" was
the screw plug's 20 Nm -- a flattened table row put a value next to the
next row's name -- every engine's column side by side, and transmission
values for an oil level sensor from the old "+ torque" keyword search).
`/api/values` reads the answer's pages (or the top search hits) and, for
WIS, the AR/BA/BF/BE documents they link to (in scope only); WIS tables are
read from their SIDS layout (`wis_tables.py`: row and column kept, a column
kept only if its label fits the car -- `label_fits`, English and German
labels, "except", conditions shown as "Only for: …"; a dash is never a
value; a row it can't pair is left out, never guessed). `/api/fluids` is the
car's fluids card from WIS's capacity sheets (BF..-P-1000) with the oil
sheets (MB 229.x); none for ATF (per gearbox) or for cars WIS has no sheet
for (the W201). Other manuals use the text lines (`value_lines`), which no
longer give "Stage 1 Nm 20" as 1 Nm or a quarts column as litres. Do not go
back to keyword-searching these tabs. Tests: `tests/test_wis_tables.py`,
`test_answer_tabs.py`.

**A VIN is checked before it is decoded; a scanned one is never searched
by itself** (2026-10-16). `vinCheck()` (index.html, `vin-check` block):
I/O/Q refused with the fix offered; every 17-character VIN that is not a
FIN must pass its ISO 3779 check digit, else only the series is used (no
variant, engine or year) -- one-character typos named other cars with
other engines. Do not narrow it to WMIs starting 1/4/5: Mercedes' US VINs
start W. Scan VIN reads the photo in the browser (vendored zxing-wasm +
tesseract.js under `web/vendor/`, served by `GET /vendor/`, no CDN, no
upload route, no AI) and only fills the box; Find picks the car. Tests:
`tests/test_vin.py`, `tests/test_vin_scan.py`, `test_ui_picks.py` part 7.
Details: `vehicle-selection.md`, `shop-network.md`.

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

**A parts catalogue is joined into the manual's car at query time, and
its page numbers are prefixed** (2026-10-14). EPC builds its own vehicles;
`index.link_sources` gives a WIS car the EPC vehicles of the same
`model_code` (`joined_ids`) and the EPC engine buckets of its variants'
engines (`engine_joins`), and `scope_ids` adds them after the car's own
engine buckets, searched as a separate group appended after the manual's
results (the manual's top N never changes) and held in their own FTS
table (`parts_fts`; BM25 statistics are index-wide, and in `chunks_fts`
EPC shifted WIS rankings even with no join); the joined EPC vehicle is
left out of `/api/vehicles`.
Only `doc_type "parts"` vehicles are ever joined in. **EPC page numbers
are `P<dok_id>`** (`SOURCE_PAGE_STYLE` / manifest `page_no_prefix`):
page_no is the citation key, and an unprefixed EPC number could equal a
WIS DokID and verify a citation against the wrong page. Do not drop the
prefix. Tests: `tests/test_epc_join.py`. Details: `epc-source.md`.

**Parts the answer left out are added, checked after the model**
(2026-10-15). Two paid answers gave only the pencil-type glow plug for a
C 220 CDI and left out the standard one (A 001 159 66 01, "all models")
that the passage showed -- the second despite a prompt line to list every
current number. `query._add_omitted_parts()`, like the two checks above:
on parts pages the answer cites, every current line (no "replaced by")
that fits the chosen car ("all models", or its exact model/engine code)
and is not named in the answer is added as a spec citing that page, said
in `caveats`, and listed in `added_parts`. Only when a car or engine is
chosen. Tests: `tests/test_citations.py`, and end to end through the fake
API (`fake:onepart`) in `tests/test_epc_join.py`.

**The right box's tabs are plain, and labour shows the job** (2026-10-16,
the user's list). Capacities and Torque specs are one table each (Fluid /
Capacity, Part / Specification: `search.value_row`, the value as printed,
no paragraph). Labour times are empty until a question and show the job
itself (`wis_links._rank_labour`), both gearboxes' versions where they
differ -- the car box does not know the gearbox; other matches fold away.
The Labour times and Option codes buttons beside VIN are gone (Option codes
entirely: nobody knows them; a data-card read may bring them back). Tests:
`tests/test_wis_links.py`, `tests/test_epc_join.py`, `test_answer_tabs.py`.
Details: `wis-source.md`, `epc-source.md`.

**Four AI providers, one set of checks** (2026-10-16). Anthropic (default),
Google Gemini, xAI and DeepSeek answer through `providers.call`; switching is
the Shop setup setting `Config.provider`. The citations rule is unchanged and
provider-blind: whatever answered, the reply is validated against the same
schema (malformed → "no parsable answer", never a crash) and then goes
through `_verify_citations`, `_drop_other_vehicles` and `_add_omitted_parts`
exactly as before -- do not move any of them into a provider, and do not
skip one for a provider. The Anthropic request is byte-for-byte what it was
(recorded and compared; again for SDK 2.x, 2026-10-21: same body, the key
now in `Authorization: Bearer`, API version 2026-10-21 -- `anthropic<3`
until the next major is recorded the same way). The others use the stdlib (no `openai` /
`google-genai` in the MSI). Prices carry the date checked and their source;
an unknown price is logged as NULL ("cost not tracked"), never guessed.
Keys are per provider, entered in Shop setup, never fetched or sent to a
phone. The question box's model menu lists every model of every provider
with a key (less `Config.hidden_models`, never the default); the model
picked decides the provider, and `pick_key` is asked for that provider.
The shipped default is Sonnet 5 (the user: Opus is overkill). Newer models
come from the provider's own model list (Shop setup, a free GET) and are
added by hand; an added model has no price (NULL, "cost not tracked") and a
Claude one gets the plain request -- never guess a price for it. Tests: `tests/test_providers.py`; details and what is unverified
(no real call to Gemini/xAI/DeepSeek yet): `ai-providers.md`.

**Prompt rules that exist for a reason** (in `ANSWER_SYSTEM`): never convert
units; one entry per fastener; a conflict is only *same unit, different
number* — different units are the same spec written twice; answer every part
of a multi-part question; shop words are not manual words ("cam magnet" =
camshaft solenoid: a passage covering the part under the manual's name is
the answer -- 2026-10-17, a 2014 E 350 "replaced cam magnets" was "not in the
archive" with "Remove/install solenoid camshaft" as passage #1; with the
rule Haiku answered from it, 4 Nm + 60°, and the W210 oil level sensor
answer was unchanged); everyday, American and British names count too
("front window" = windshield/windscreen, molding = moulding), judged by what
the part is, and the planner makes one query with the manual's likely name;
a question that only names a part wants its part numbers (and the fitting
page), the likeliest reading if ambiguous, never "not found" for wording
(2026-10-24: a W201 "Front window molding clip" was "not found" by Haiku
three times with CLIP, GARNISH MOULDING BELOW WINDSHIELD in its passages;
with both rules: A 201 988 05 78 up to chassis A 179755, A 124 988 21 78
from A 179756, ~$0.02). Each of these fixed an observed failure.

**Licence: MIT, "Copyright (c) 2026 cquester"** (the user's choice,
2026-10-08). It covers this repo's own code and docs only. Never commit
Mercedes software (`tbjdbc.jar`, the EWA JRE) or anything extracted from
WIS/EPC (records, images, VIN tables, raw TSVs) — no licence makes that
redistributable. `importers/data/*.csv` is hand-made reference knowledge and
NHTSA-derived years, which is why it is allowed.

More decisions of the same weight, each with the failure that caused it, are
in `docs/notes/`. The ones most likely to be undone by accident:

- **A title match only breaks ties in keyword search** (x1.05, 3+ words,
  `search.title_fit`); bigger boosts or two-word searches lost `eval_wis`
  cases. Old job numbers ("67-200") come from WIS figure names, only where
  the title's job agrees. (`wis-source.md`)
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
- **Recent cars store the structured pick, re-checked on use** (2026-10-16):
  `autorundown.carHistory.v1` keeps doc, variant index *and name*, engine,
  year, label, VIN; a re-select whose variant name no longer sits at that
  index falls back to the whole series and says so, and always goes
  through `applyCar`. Never re-pick from the stored label text.
  `tests/test_car_history.py`. (`vehicle-selection.md`)
- **A VIN sends the engine of the Baumuster it names** (`vin9_bm`), never
  the variant's first (320 prefixes sent a wrong engine until 2026-10-16);
  a Baumuster sold under several names gets all their engines. The live
  index gets `vin9_bm` at runtime from `vin2fin.json`. (`vehicle-selection.md`)
- **Admin = loopback peer AND loopback Host**; no reverse proxy on the same
  PC; no handler built from manual text. (`shop-network.md`)
- **Which API key pays is decided only in `config.pick_key`** (company key,
  or the signed-in mechanic's own in "A key per mechanic" mode, for the
  active AI provider). A mechanic with no key is refused -- never a fallback
  to another key, nor to their key for another provider. Keys never
  reach a phone (booleans, estimates only; the admin sees the last 4), and
  `usage_log.key_ref` tags spend with "company" / "user:<id>", never the
  key. Sign-in is by single-use 15-minute QR grants (hashes only), voided
  when the dialog closes. (`shop-network.md`)
- **WIS is never removed per model.** (`importing.md`)
- **Never fetch from lemon-manuals.la over HTTP**; the LEMON download, when
  built, must follow what the user agreed (2026-10-09). (`importing.md`)
- **"Add a car" opens LEMON's page; the user downloads** (2026-10-27).
  Which site is up is asked by the browser (`siteUp`, a favicon), never
  the server; CHARM's page is the backup when LEMON doesn't answer. The
  saved zip is untrusted (`lemon_zip.check`) and unpacked into
  `APP_DIR/manuals/<doc_id>/` with the download mark; the zip stays.
  Tests read `AUTORUNDOWN_DOWNLOADS`, never the PC's Downloads.
  (`importing.md`)
- **CHARM: one car from the swarm only, plan first, never seeds** (its
  card is hidden since 2026-10-27; keep the code); the
  `.torrent` is the one file from charm.li. A car's text is ~200 MB, its
  images ~9 GB (spread through their group). Data from the swarm is
  untrusted: every offset is checked (`charm.BadData`). (`importing.md`)
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
   variant menu, `loadVehicles`, the recent-cars list -- that one also
   `python tests/test_car_history.py`): `python tests/test_ui_picks.py` — free,
   ~3 min, needs `pip install -r requirements-dev.txt` (Playwright; it drives
   the installed Edge/Chrome). It loads the real page against the index
   (opened `mode=ro`; `--index` to point elsewhere), makes every pick the UI
   can make — typed queries, VIN and FIN, Browse + reload, every one-line
   suggestion (~2,500), every Variant option (~1,300), every US VIN prefix
   (~5,800, checked against `vin2fin.json`: Baumuster and engine sent), the
   VIN dialog sending what the box sends — and checks the `variant=` each
   sends against the stored `vehicles.variants`.
   Any non-GET or `/api/ask` request fails the run.
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
`index.before-wis.db` is the old pre-WIS backup (no longer used by the
script; it predates EPC and later manuals, so never restore it);
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
- The spend log lives inside the index. `rebuild_wis.py` and
  `remove_manuals.py` leave it in place; any *other* way of rebuilding
  (deleting `index.db`, copying a backup over it) still loses it.
- **The in-place WIS rebuild has not run on the real 3.5 GB index yet**
  (tested on sample data only). Removing ~530k WIS pages fires the FTS
  delete trigger per chunk, so expect it to take longer than the old
  backup restore; freed pages are reused by the re-ingest.
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
8. **"Add a car" has not run in the installed app yet.** A real LEMON zip
   (the F-150 Lariat) went through the API into a throwaway index on
   2026-10-27; the browser's site check and Open on LEMON were seen in a
   test box only.
9. **Gemini, xAI and DeepSeek have never answered a real question**
   (`ai-providers.md`, "Not verified"): one cheap scoped paid question on
   each, with the user's OK and their key, is the real test of the request
   shapes and of the prompt on those models.

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
