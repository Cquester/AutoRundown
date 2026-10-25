# Importing from the site (Shop setup → Manuals)

<!-- Moved out of CLAUDE.md on 2026-10-12 to keep that file short; the text
is unchanged apart from file paths (tests/, tools/). "Above" / "below" and
section names may refer to CLAUDE.md or another file in docs/notes/. -->

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

**A late folder reply no longer overwrites a typed path** (2026-10-11,
found by `ui_check.py` in a test box): opening Manuals asks
`/api/browse` for the home folder; a path typed before that reply landed
was replaced by it, and Enter then browsed the home folder. `impBrowse()`
drops a reply when `#impPath` was edited (`impTyped`, an `input` counter)
after it was requested. Probably also the "two paths run together" glitch
seen once on Windows.

Shop setup → **Manuals**, and automatically on first start
when the index is empty. A folder browser plus format detection
(`importer_jobs.describe()`: a folder with `manifest.json` + `records.jsonl`
is a corpus straight away — letting the other detectors walk its `images/`
took 15 s), then a background job that runs **the same CLI**: `ingest.py
--importer auto` (add / update, resumable) or, for a WIS corpus,
`rebuild_wis.py` (replace the WIS part in place, keep other manuals -- EPC,
LEMON, folder -- and the spend log; offered when the index already has WIS
and the folder is a WIS corpus, see `wis-source.md`).
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
checks the spend log row count is unchanged.
**A Remove button removes completely** (2026-10-23, the user: "if i click a
delete button on a local site, it should delete the data completly"; the
"shrink the index file afterwards" box confused). No box any more: every
Remove shrinks the index (`--compact`: FTS5 `optimize` on `chunks_fts` and
`parts_fts`, then VACUUM + `wal_checkpoint(TRUNCATE)` -- FTS5 only marks
deleted rows, so VACUUM alone left 84,598 `parts_fts` blocks for 0 parts on
the live index; in WAL mode VACUUM lands in the -wal file and needs the
checkpoint). "Remove all Mercedes WIS / EPC data" also sends the extracted
files to the **Recycle Bin** (the user's choice over a permanent delete or
leaving them): the corpus folder each manual was imported from
(`vehicles.file_path`) and, beside a `wis_extract`, its sister outputs
(`importer_jobs.extract_dirs`, listed on the tab and in the confirm).
Folders come from the index, never from the extractor's settings, so a
test's corpus can't point at the real `autorundown_out`; `recycle.py` takes
only extract-folder names and never one holding the index; Windows asks
first if a folder is too big for the Recycle Bin. A LEMON or folder
manual's files are the user's own and stay. `rebuild_wis.py` and the VM
EPC import call `remove_manuals.py` too but never pass `--recycle` -- they
re-ingest from those folders. `/api/import/manuals` lists, `POST
/api/import` modes `remove` / `remove_wis` / `remove_epc`, admin-only.
Test: `tests/test_remove_manuals.py` (Recycle Bin = a temp folder via
`AUTORUNDOWN_RECYCLE_TO`).

**LEMON folder import** (inside the LEMON tab): open the archive folder, get one row per vehicle folder with
"imported" / "partly imported" read from the index (opened `mode=ro`), tick
which to import. `/api/import/lemon?path=` lists them
(`importer_jobs.lemon_vehicles()`, admin-only under `/api/import`); the job is
`ingest.py --importer lemon --only <folder> …` (`--only` matches a vehicle by
its folder; an unknown one fails the run). The server only accepts folders
its own listing returned. The Folder tab still auto-detects LEMON and points
at the LEMON tab. A torrent source exists on the unmerged branch
`claude/confident-wozniak-t9iphf`; the user chose to leave it out until it is
tested on its own. Test: `tests/test_lemon_import.py`.

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
picked}`, admin-only. Test: `tests/test_lemon_catalogue.py`.

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
**The VM is chosen as a file** (2026-10-23, the user: "it should be a file
explorer option, where i choose what vm to use", after the tab had listed
VirtualBox's VMs for them): "Choose VM file…" browses folders and `.vbox`
files (`/api/vms/browse`, starting where the last pick was, else
`~/VirtualBox VMs`; `AUTORUNDOWN_VMS_DIR` for `dev.py --fake-vm`'s pretend
files), and `POST /api/vms/pick` reads the file's `<Machine name>`
(`importer_jobs.vbox_machine`), checks VirtualBox has that VM, and keeps
it as `Config.vm_file`. Runs still address the VM by name. A VM that
isn't added to VirtualBox is refused with how to add it (Machine > Add…):
this never registers one, as it never changes VirtualBox's settings.
**No terminal windows**: under the installed app (pythonw, no console)
every console program a job started -- Python, VBoxManage, Java -- opened
a window of its own (the user saw "a bunch of different terminal
windows", 2026-10-23). `importer_jobs` and `vm_pipeline.py` start every
process with `CREATE_NO_WINDOW`; the hidden console is inherited by
everything under it. `tests/test_vm_jobs.py` checks every `subprocess`
call carries `creationflags`.
`/api/vms` lists VirtualBox's VMs (to check a pick and show their state)
and the chosen one; the job runs the extractor's
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
brought the DB up in 10 min. (Since 2026-10-23, only if the clean shutdown
worked: when Windows ignores the power button the state is saved, and the
"second boot" would just resume it -- a blue-screened guest was resumed
twice that way. It stops instead and says to Reset / roll back.)
**The installed app runs the extractor with its own embeddable Python**,
whose `._pth` does not put a script's folder on `sys.path`: every extractor
script that imports a sibling (`wis_paths`, `parse_sids`, …) inserts its
own folder first. Five did not, so the VM tab of the installed 0.1.6
showed "No module named 'wis_paths'" once the Desktop copy was updated
(2026-10-23; from source it worked). `tests/test_vm_jobs.py` checks it. Conversion sorts largest-first, so its ETA
reads ~3 h while on the DXF/SVG diagrams and then drops; ~40 min in total.

**The EPC parts catalogue from the same tab** (2026-10-15). The Mercedes VM
tab asks "What to extract": Workshop manuals (WIS, with its steps) or Parts
catalogue (EPC). EPC is job mode `vm_epc`: `vm_pipeline.py --steps epc`
(`pull_epc.js` on the EPC database, port 2034, `AR_CATNUM=all,engines`,
resumable per catalog, the same power-cycle-and-resume as the WIS pulls,
32-bit JRE heap 1 GB) into `<out_root>/epc_extract`; then, if EPC is already
in the index, `remove_manuals.py --source mercedes-epc`; then `ingest.py
--importer records` on it -- so the index matches the corpus exactly. The
`epc` step is not in the default (WIS) run. EPC is a group of its own in
`importer_jobs.manuals()` (an "EPC in the index" card with "Remove all
Mercedes EPC data", mode `remove_epc`), never listed car by car in the
Folder tab, and `remove_manuals.py` removes it only whole (`GROUPED`). An
extractor without `pull_epc.js` -- the installed app's Desktop copy predates
EPC -- is refused before the job starts (`importer_jobs.epc_problem`).
**First run from the site (dev.py, 2026-10-15) was cut off** at 23 of 161 engine
catalogs when the Claude desktop app restarted (09:20:37, its VM service
stopping) and took down the terminal `dev.py` was started in, and with it the
job: the VM was left running, the corpus intact (per-catalog appends; 46,269
records, none partial). Since then the job log is also written to
`<app dir>/import_job.log` as it arrives. Start long jobs from a server that
does not live inside another app's terminal. Tests: `tests/test_epc_join.py`
(groups, job commands, whole-source removal), nothing touching a VM.

**WIS / EPC / Both, and the progress bar** (2026-10-16). "What to extract"
is a segmented toggle: WIS, EPC, Both. **Both** is job mode `vm_both`: one
POST, one job, made of two *parts* -- the WIS job's commands (`vm_pipeline.py
--steps <ticked>` then `rebuild_wis.py`), a 60 s pause (the PC
crashes under sustained I/O; the WIS part ends with an index rebuild), then
the `vm_epc` commands. Nothing new runs: `ImportJobs.start` builds the parts
from the same code as the two single jobs. The EPC part's commands are built
when it starts, since the WIS part may change what the index holds, so
whether an older EPC needs removing first is decided then. (`rebuild_wis.py`
now replaces WIS in place and keeps EPC; it used to restore
`index.before-wis.db`, which had none.) A failing command stops
the job: that part is `failed`, later ones `not started`, `failed_part` says
which, and the log says "not starting EPC parts". Both is refused up front if
the extractor predates EPC (`epc_problem`), so it can't fail hours in.
The status (`GET /api/import`) carries `parts`, `part` and `progress`
(`importer_jobs.Progress`): phase, done, total, unit, detail, activity, the
pipeline's `STOP:` reason -- parsed from the log lines the scripts already
print (`== images: N graphics`, `batch: +x -> a / b`, `catalog X (i/N`,
`total docs ... = N` on a fresh pull only, `found N vehicle(s)`, `parsed i`,
`n/N failed` from conversion, `[i/N]` from removal). **A total is shown only
when a script printed one**: a resumed document pull, model links and the
vehicle tables get an indeterminate bar with their counts. The bar sits in
the sticky bar at the bottom of the Manuals page while the Mercedes VM tab is
open ("Step 1 of 2: WIS — Images", "2,000 / 3,000 graphics (66%)", the two
parts' states; the parts list is hidden at phone width). It reads the job on
the server, so a reload shows it again; a reload during a VM job also reopens
the VM tab with the toggle on that job's choice. The job state lives in the
server process, as before: a server restart still loses it (the log file
stays).

**Choose the VM, check it, cancel it, never pull the plug** (2026-10-16,
the user, after a real run: the database never answered, the pipeline
forced the VM off after a 2-min shutdown wait, and Windows 7 came back in
Startup Repair -- seen in a `screenshotpng` of the VM; the server was then
closed and the extractor kept running on its own).
- **The VM is chosen, not defaulted**: the list starts on "Choose the
  virtual machine…"; Extract and *Test the VM* stay off until one is chosen
  and its check passes.
- **The check, without booting** (`vm_pipeline.py --check`, `GET
  /api/vms/check?vm=&what=&steps=`): one `CHECK <key> <ok|warn|fail> <msg>`
  line each -- the VM exists; its state (running / saved / aborted are
  warnings, said in words); a host-only adapter (fail without) on the
  `--subnet`; Guest Additions (without them "Windows didn't start" can't be
  told from "the database isn't answering"); Java; `tbjdbc.jar`; every
  script the chosen steps run (an older extractor copy lacks the EPC ones);
  free disk (images ~30 GB). An extractor too old to know `--check` prints
  no CHECK lines: that is a failure saying to update the copy. `POST
  /api/import` runs the same check and refuses with the failing messages.
- **Test the VM** (job mode `vm_check`, `--check --boot`): boots it, checks
  Windows starts and the WIS / EPC database (as the steps need) answers,
  shuts it down cleanly if it started it; imports nothing. The results join
  the checklist (`status().checks`).
- **No forced power-off.** `vm_down` asks Windows to shut down (ACPI) and,
  after 2 min, **saves the VM's state** instead of powering off; a saved
  state is **resumed**, no longer discarded (discarding is a power-off for
  the guest). When the database doesn't answer, `windows_up()` reads the
  Guest Additions run level: 2/3 = Windows is up (the DB is the problem: one
  more boot after a clean shutdown, as before); below that = Windows never
  started: a screenshot (`VM_SCREEN <out>/vm_screen.png`), the state saved,
  STOP. The page shows that picture (`GET /api/import/screen`).
- **Cancel** (`POST /api/import/cancel`, a button in the progress bar while
  any job runs). The server writes `<app dir>/import_cancel`; the extractor
  (`AR_CANCEL_FILE`) stops the pull it is in -- each one resumes -- shuts the
  VM down cleanly and exits 3; the job ends `cancelled` (not failed), later
  parts `not started`. An index step (ingest, rebuild, remove) is left to
  finish, then nothing after it starts: ending one half-way could leave the
  index without the manuals it was replacing. An extractor that ignores the
  cancel is ended after `CANCEL_GRACE` (15 min). The extractor also stops
  the same way when the server process (`AR_PARENT_PID`) goes away.
- **Our own snapshot, and rolling back** (the user, the same day: "we
  should create a snapshot just for us"). Before a run or a VM test boots
  the VM, `auto_snapshot` takes "AutoRundown-auto-<date>-<time>" (`SNAPSHOT
  <name>`, kept as the job's `snapshot`), unless ours from the last 6 hours
  exists -- retries don't pile them up. Never deleted automatically:
  deleting merges disk images, heavy I/O on this PC; the check warns past
  5. Shop setup → *Snapshots and rolling back* lists them (`GET
  /api/vms/snapshots`, `--list-snapshots`) and rolls back (job mode
  `vm_rollback`, `--restore NAME`), refused while the VM runs; it first
  takes "AutoRundown-before-rollback-<...>", so a rollback can be undone.
  After a run fails with a screenshot the section opens with the run's own
  snapshot chosen. A snapshot taken just before a run is the VM as it was
  then: if Windows was already broken, pick an older one.
Real VM, 2026-10-16: `--check` (read-only); **Test the VM** resumed the
saved Startup-Repair state, waited 10 min, took the screenshot and saved the
VM again (never powered off); **Cancel** during a test: STOP, ACPI
shutdown ignored by Startup Repair, screenshot, state saved after 2 min,
job `cancelled` -- that run found a crash in the shutdown loop (a set
changing while iterated; fixed, tested). Then, through Shop setup: **rolled
back** to AutoRundown-20260929-172504 (it kept
AutoRundown-before-rollback-20261016-133808 first) -- and found that the
page's check and snapshot list, asked for together, collided on the VM's
lock ("already has a lock request pending"): the server now runs VirtualBox
queries one at a time (`VBOX_LOCK`) and `vbox()` retries a pending lock.
**Test the VM** on the rolled-back VM: our snapshot taken, Windows up and
WIS answering in ~70 s, clean ACPI shutdown in 10 s; again with EPC: the
snapshot reused (6 h), WIS and EPC (2034) both answering, clean shutdown.
Not run on the real VM: a full extraction with Cancel mid-pull (the pull
itself is the same resumable code as before). The rest on the fake
extractor ("Windows won't start (fake)", "No network (fake)").
Tests: `tests/test_vm_jobs.py`.

**Testing it without a VM**: `tools/fake_extractor/` is a stand-in
`vm_pipeline.py` (plus empty `pull_epc.js`/`epc_fv4.js` and a `wis_paths.py`
writing to a temp folder, never `AR_OUT_ROOT`). It lists three pretend VMs --
"Mercedes EWA (fake)", "Fails in documents (fake)", "Fails in EPC (fake)" --
prints the real pipeline's kind of lines over a few seconds and writes a tiny
corpus, so the real `ingest.py`/`remove_manuals.py` steps run after it.
`python dev.py --fake-vm` points the extractor folder at it (pause between
parts 5 s, `AUTORUNDOWN_PART_PAUSE`); in a test box, restart `dev.py` with
`--fake-vm` (the box's entrypoint doesn't pass it). tools/ is not in the MSI.
Test: `tests/test_vm_jobs.py` (parser, Both order, failure of either part,
one job at a time, reload, the old safeguards). **Not yet run against a real
VM**: the parser's patterns are taken from the scripts' source, not from a
real run's log.
