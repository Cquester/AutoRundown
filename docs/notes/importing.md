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
  names ("190E 2.3-16 (201.034) L4-2.3L (102.983)") match no row by name.
  Correction (agent review 06, 2026-10-28): the 1987 catalogue's "190E 2.3,
  102.983" rows *are* the 2.3-16 (102.983 is only in Baumuster 201.034).
  `importers/data/lemon_labels.csv` names the 46 variants LEMON gives only
  by codes where one Baumuster fits ("190 E 2.3-16 (201.034, engine M102.983
  I4 petrol), automatic"), shown in the car box and the LEMON tab and
  searched; `on_pc` counts a manual whose doc_id names that Baumuster and
  engine as that row, so the 2.3-16 is not offered again as Not downloaded
  (both the automatic and the manual row: the manual covers the car). The
  other 253 code-only variants (engine family only, ambiguous) keep their
  codes: `reports/agent-review/06-mercedes-label-map.csv`.
- The "engine" field also carries trim and body for many makes ("Lariat, 4D
  Pickup Extra Cab, 3.5L Eng VIN G, 4WD"), so the tree calls that level the
  variant; a filter box narrows the open folders' variants (and the makes).
- Per-car image download measured on the real index (2026-10-09): the
  F-150 Lariat's 8,689 images (593 MB in its LEMON zip) sit in 154 of the
  64 MB pieces of `images.mtbl` = 9.6 GB, in 101 runs; image ids
  (`G000161176`, `GFD41139`, `VA467679`) are the file names in a LEMON zip.
  A car's image list is only known from its pages, so text comes first.
- **The download (not built) must follow what the user agreed, 2026-10-09**,
  and the LEMON tab's "How LEMON downloads will work" (hidden since 2026-10-27) tells users so
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
- **The app never downloads manuals from lemon-manuals.la by itself** --
  the user's rule (the site asks not to be scraped); clarified 2026-10-29:
  nothing is fetched automatically, but people may pull data from the site
  themselves, and the browser's own favicon check (below) is fine. The
  server makes no request to it. The `.torrent` itself is its download link;
  anything else comes from the swarm. Since 2026-10-27 the app only opens
  a car's page in the user's own browser ("Add a car", below); the user
  downloads. Plain HTTP clients (curl, Python) get a bot trap from the
  site: every path answers `Content-Length: 21474836480` (20 GB of
  nothing) -- a real browser gets the real page.
- LEMON's server went down on 2026-10-26 (the user: hit in an attack on
  Ukraine); its download is on hold and **Operation CHARM is the backup**,
  below. The LEMON `.torrent` is at `C:\Users\cques\lemon-manuals.torrent`
  (328 KB, 2026-08-19); a torrent does not need LEMON's website, but its
  layout (above) makes one car a large part of 31.5 GB, where CHARM's is
  ~200 MB -- so one car comes from CHARM. The LEMON torrent also carries
  a `charm/` copy (`.mtbl`, not CHARM's own LMDB layout). LEMON came back
  on a temporary server on 2026-09-30 (its banner; seen working 2026-10-27).
- **The LEMON torrent, read 2026-10-27** (pieces 16308-16309 and index.json
  only, with the user's OK): 16,310 pieces of 64 MB, 1,094.5 GB.
  `lemon/pages.mtbl` is 31.52 GB (pieces 15840-16309), an mtbl file: the
  last 512 bytes are nine little-endian u64 fields and `LBTM`; 128 KB
  zstd blocks, 52.6 M entries in 1.56 M blocks, a 60 MB block index. Its
  keys sort by kind, so each kind is one run of pieces: bulletins
  15840-15903, `html_act` 15903-16043, `html_aht`..root 16043-16067,
  `html_s` 16067-16243 (11 GB), `link_table` 16243-16261, `uri_table`
  16261-16308. Pages are stored once by content and shared between cars,
  so one car's text is spread over most of the file: **per-car text from
  this torrent is not practical** (a whole-file download is ~4.5 days on
  its swarm). This is why "Add a car" uses LEMON's own zip.

**One car from Operation CHARM** -- **built, tested, hidden** (2026-10-27,
the user: hours per car on its swarm, "waiting an entire day isn't worth
it"; `#impCharmBox` has `hidden`, the code and `tests/test_charm.py` stay).
The real image run, a 2013 Toyota Corolla, stopped at the user's 1-hour
limit: 41 of its 70 image pieces had arrived, enough for 84% of its
images. (The LEMON tab's "One car from Operation
CHARM" card, 2026-10-27; the user chose one car at a time, text and images
together, with a "Text only" choice when the images are large). CHARM
(charm.li, 1982-2013) is where LEMON's data came from: the LEMON zip of the
1987 190E 2.3-16 is a static export of CHARM's pages for that car (the same
image ids, breadcrumbs and page HTML). The rules agreed for LEMON hold:
only the swarm (the `.torrent` from charm.li's About page, "Download our
data!", is the one file from the site -- fetched with the user's OK); never
seeds (upload capped at 4 KB/s, no UPnP/NAT-PMP, any free port, session
closed when done); size, free space and what stays shown first, refused
without the space; afterwards the torrent data is deleted and only
`APP_DIR/manuals/<doc_id>/` stays; removing the car recycles that folder.

What the torrent is (read 2026-10-26, `operation-charm.torrent`, 884 KB):
757.9 GB, 45,175 pieces of 16 MB, v1, six public trackers plus the peer
`charm.li:17471` the site names. **Not** the mtbl format of the `charm/`
folder inside the LEMON torrent:
- `operation-charm/lmdb-images/data.mdb`, 698 GB, pieces 0-41600: a plain
  LMDB (4 KB pages), 20.25 M images keyed `/<group>/<sub>/<id>/`
  (`/IMP49Q313/euro210/151068321/`), B-tree depth 5.
- `operation-charm/lmdb-pages.sqsh`, 60 GB, starting exactly at piece
  41600: SquashFS 4.0, gzip, 128 KB blocks, holding one sparse `data.mdb`
  (2 TB mapsize, 477 GB used, 8:1 compressed): 443 M entries, depth 9,
  written in 79,200 transactions and never compacted. Its inode table (the
  block list) is in the last 9.4 MB, piece 45174.
- `server.js` (the site) shows the keys: a page path
  `/Make/Year/Car/<section>/.../` (URL-encoded, `%20`, `%28`, `%2F`) maps
  to an article key `N,M` whose value is the page's HTML; `N` is one number
  per car (the 190E: 46795). A listing page's article key is
  `SPECIAL_<its path>` (a year page lists its cars). Images are
  `<img src='/images/<key>'>` in the HTML.

Measured on the 1987 190E 2.3-16 (201.034) (416 MB fetched, 2026-10-26/27):
12,954 paths (the LEMON zip: 12,904 pages), 5,724 articles, 8.7 MB HTML,
5,161 images (zip: 5,091). Its path leaves are all in **one** piece
(42906), its articles in 6 (42906, 42907, 42946, 42947, 44369-44371), the
tree above them in 3-4 more: **the text of one car is ~12 pieces, ~200 MB**.
CHARM wrote a car in one go, so a car's pages sit together; neighbouring
cars are in the neighbouring pieces. Images are not: an image's key and
its data sit in the same piece, but a car's image ids are spread through
its whole group (`euro210`, sorted as strings), estimated **~570 pieces,
~9 GB** for the 190E (fitted from 4 lookups over 5 pieces -- an estimate,
like LEMON's 9.6 GB for the F-150). Cars in the same group share those
pieces. The swarm gave ~100 KB/s per piece (9-14 seeds), 3-11 min a piece.

How it is built (`autorundown/charm.py`, `charm_fetch.py`, `charm_download.py`):
- `charm.py` reads both databases a piece at a time, pure Python (zlib,
  struct): `Squash` (one file inside the image, block by block), `Lmdb`
  (meta, `get`, `scan`, `children_pieces` = what a range costs from the
  branch pages alone). A read of a piece not on disk raises `Missing` with
  every piece one pass needs, so a swarm fetches them together. Every page
  number, offset, length and depth is checked (`BadData`); image keys
  become file names only if every part is `[A-Za-z0-9_.-]`.
- `charm_fetch.TorrentSwarm` (libtorrent; resume data in the work folder,
  so a plan's pieces are not fetched again) and `LocalSwarm` (a complete
  copy + `layout.json`, for tests and `dev.py`; `AUTORUNDOWN_CHARM_SOURCE`).
- `charm_download.py --plan` finds the car (exact key; else the year's
  `SPECIAL_` listing and `match_car`: an exact "model engine" is taken, an
  inexact name is only offered -- `NOT_FOUND {candidates}`, exit 2), fetches
  its text and the image index and prints `PLAN {json}` (saved as
  `charm_download/plan-<doc_id>.json`). Without `--plan` it downloads the
  rest (images in batches of 32 pieces), writes the LEMON layout
  (`index.html`, `pages/<n>.html` with LEMON's breadcrumbs, `images/<group>/
  <sub>/<id>.<ext>`, links and images rewritten to the folder's own files,
  an unknown one to `../404.html`) and a `.autorundown-download` mark (from
  the start: a later try only replaces a marked folder), then deletes the
  torrent data. Exit 3 cancelled, 4 no space, 5 already in the index.
- Job modes (`importer_jobs`): **`charm_plan`** and **`charm_car`**,
  `POST /api/import {mode, car, text_only}`; `car` is `[make, year, model,
  engine]` (a catalogue path) or `[make, year, car]` (CHARM's name, what
  `charm_car` takes, from the plan). `charm_car` is refused without a plan
  and without the space, then runs `ingest.py --importer lemon` on the
  folder. `GET /api/import/charm`, `POST /api/import/charm/torrent {path}`
  (must be a .torrent holding CHARM's two files). The UI's
  `window.charmFill(make, year, car)` opens Shop setup on the card, fills it and sizes the car --
  for the "Add this car" button on `feature/parts-ux-and-lemon-autofill`.
- `recycle.downloaded_car`: a marked folder directly in
  `APP_DIR/manuals` is the one other thing Remove may recycle.

**The real text-only run** (2026-10-27, the 190E into a throwaway home):
26 real pieces in all (416 MB on disk; the last one 16 MB in ~2.5 min at
~200 KB/s from 5-15 peers, upload held at the 4 KB/s cap), 12,954 pages
written in 7 s, `ingest.py --importer lemon` 6 s. Against the LEMON zip
of the same car, each in its own throwaway index: the same doc_id, make,
model and engine; 12,886 vs 12,833 pages, 5,483 vs 5,624 unique; every
LEMON title is in CHARM (CHARM has 15 more, e.g. "Replacing Fuel Filter");
and with link numbers, image references and dead-link markers set aside,
4,533 of LEMON's 4,534 page texts are in CHARM (the one left is LEMON's
own index page). LEMON shows a link to a page missing from its own zip
as `[[name]]`; CHARM's export sends it to 404 and the name is plain text.
The real 1987 year listing gave 14 cars through `listing_names`;
"190E 2.3-16" + "L4-2.3L" offers the right car first, never takes it.
Two things that broke on the real swarm and are fixed: a torrent added
unpaused wanted every piece and a peer added during the file check was
dropped (0 peers for 38 min), and one added paused never checked its
files at all -- now the zero priorities go in the add itself, unpaused,
peers after the check. libtorrent's first check of the 758 GB sparse
layout takes **~8 min** (logged as "checking the pieces on disk: N%");
the resume data saved at the end skips it next time. 385 of the
torrent's pieces are all zeros and "on disk" with nothing fetched; the
plan's `fetched_pieces` leaves them out.

Not verified yet: the image download on the real swarm (only one real
image was read); libtorrent inside the MSI's embeddable Python (a cp312
win_amd64 wheel exists); a peak-disk cap (pieces stay on disk until the
end: sparse files, ~9 GB for a car).
CHARM names cars the old LEMON-zip way ("190E 2.3-16 (201.034) L4-2.3L
(102.983)"), so a LEMON catalogue path usually needs the plan's candidates.
Test: `tests/test_charm.py` (sample data, `tools/make_sample_charm.py`).

**LEMON cars in the car box** (2026-10-26, the user: typing "2016 Ford
F-150" should offer it, "Not downloaded", with "Add this car" right there).
- The list is the LEMON tab's own (`lemon_catalogue.load`: the torrent's
  `index.json` when on this PC, else the committed snapshot) -- no scraper,
  never the LEMON site. Cached in the server and now looked at again at most
  every `RECHECK_S` (5 min) or when `lemon_torrent_dir` changes.
- `lemon_catalogue.search` (`GET /api/catalogue/suggest?q=`, any device that
  can search): the car box's own matching (every word, a year narrows, one
  letter = a whole word), cars whose doc_id is in the index left out, words
  in make/model before engine-only matches, newest first; 6 rows and the
  total. A year alone asks nothing. ~0.2 s with a year, ~0.7 s without on
  the 279,988-car snapshot. The page asks once typing pauses (280 ms).
- **Add this car** (`POST /api/import/lemon-add {path}`, admin): if the car's
  folder is on this PC -- looked for only beside the LEMON manuals already
  imported (each one's `file_path` parent and grandparent, `importer_jobs.
  lemon_on_disk`, depth 3 as the importer scans) -- the same `lemon` import
  job as the folder import, its progress in the card (`/api/import`), and
  the car is selected when done. Otherwise its zip if it is in Downloads,
  else LEMON's page (next section).
- The sample Camry (`tools/make_sample_lemon.py`) is named as the catalogue
  names it ("Camry LE, 2.4 E, Automatic") so a box exercises the import.
Tests: `tests/test_catalogue_suggest.py`.

**Add a car: LEMON's zip, saved by the user** (2026-10-27, the user: "it'll
just redirect them to the site", with a check of which site is up). Both
places offer it: the car box's "Add this car" and Shop setup → Manuals →
LEMON → **Add a car** (a search over the same catalogue, 12 rows, the same
card under it, `offerCatalogueCar(r, '#lemAddBox')`).
- `POST /api/import/lemon-add` (admin): the car's folder on this PC → the
  `lemon` job; else its zip already in Downloads → the `lemon_zip` job;
  else `{"action": "download", links, zip_name, downloads}`.
  `lemon_catalogue.site_links`: LEMON's page
  `https://lemon-manuals.la/<make>/<year>/<model engine>/` (each part
  URL-encoded; the catalogue's names are the site's, checked on the 2023
  Corolla Cross's nine variants) and, for 1982-2013, CHARM's year page
  `https://charm.li/<make>/<year>/`.
- **Which site is up is asked by the browser, not the server**
  (`siteUp`): a no-cors fetch of each site's `/favicon.ico`, 6 s at most,
  dropped once headers arrive -- it says only that the server answers. The
  server stays off both sites. LEMON up → "Open on LEMON"; LEMON down and
  CHARM up → "Open on CHARM" (to read online; adding still needs LEMON's
  zip) plus "Try LEMON anyway"; both down → "Check again".
- The link opens in a new tab; the card then asks `POST
  /api/import/lemon-zip` every 4 s ("waiting") for 45 min at most, and
  says so. LEMON's *Download .zip for offline use* is an HTML page, not the
  file: "To prevent automated downloads, please type "human"", a text box
  and *Download manual* (seen 2026-10-28 on the 2003 Civic). The user does
  that step; the card names it (agent review 04: it didn't, so a user
  following it waited for a download that never started). The app never
  fills that form.
  `lemon_zip.find`: LEMON's name `LEMON <year> <make> <model engine>.zip`
  (`zip_name`), also "name (1).zip" and with a character Windows can't
  hold replaced; never a `.crdownload`/`.part` or a zip whose central
  directory isn't written yet. `AUTORUNDOWN_DOWNLOADS` overrides
  `~/Downloads` (tests set it, so they never read the PC's own).
- The zip is untrusted (`lemon_zip.check`): one folder at the top
  (`<year> <make> <car>/`, as LEMON makes it) holding `index.html`; plain
  relative paths only (no `..`, absolute, drive, backslash or control /
  reserved characters), no links, at most 1 M entries and 40 GB, no entry
  over 1 MB inflating more than 1000x, writes capped at the size the zip
  states. `unzip_lemon.py` (job part 1, "== Unpacking", "unzipped d/t
  files") unpacks into `APP_DIR/manuals/<doc_id>/` with `DOWNLOAD_MARK`
  (written incomplete first), never over a folder it didn't mark, and
  only with the space; part 2 is `ingest.py --importer lemon` on it.
  Removing the car recycles that folder; the zip stays in Downloads.
- Checked in a test box (2026-10-27): the search, the LEMON link, waiting,
  and a stand-in zip dropped in Downloads imported and selected.
- **A real LEMON zip through the API** (2026-10-27, the user's own
  `LEMON 2015 Ford F-150 Lariat, 4D Pickup Extra Cab, 3.5L Eng VIN G,
  4WD.zip` from Downloads, 634 MB; a throwaway home and index, TestClient,
  no server): `lemon-add` found it and started `lemon_zip`; 41,837 files
  (864 MB) unpacked in ~37 s, then 191 s of `ingest`: 32,674 pages ->
  10,410 unique, 15,319 chunks, 529 connectors, 2,747 other-engine pages,
  155 MB; doc_id the catalogue's, in `/api/vehicles`, no longer offered;
  all 8,689 images its pages name are on disk. The 190E 2.3-16 zip
  (18,013 files, 226 MB) passes `check` too, but its old-style name is
  not in the catalogue, so "Add a car" never names it.
- **Windows' 260-character path limit**: that first run, in a deep temp
  folder, failed halfway with a traceback (280 characters). `unpack` now
  refuses up front ("its files need paths of N characters ... Windows
  allows 259"), removes a folder it couldn't mark, and `unzip_lemon.py`
  turns any other disk error into a STOP line. In the installed app
  (`C:\Users\<name>\.autorundown\manuals\<doc_id>\...`) the F-150's
  longest path is ~140 characters.
Tests: `tests/test_lemon_zip.py`.

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
discarded for a cold boot (a 7.1 save state may not resume under 7.2).
(Since superseded: snapshots, resuming a saved state and the network
check's Fix it are under "Choose the VM, check it" below.) Finds
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
  `--subnet` -- with **Fix it** beside that ✗ (2026-10-29, the user's
  new-user run on the reverted `AutoRundown-base`: Adapter 1 was NAT):
  `POST /api/vms/fix-network` runs `vm_pipeline.py --fix-network`, which
  puts Adapter 1 on the PC's host-only network (`modifyvm` when off,
  `controlvm` when running; a saved VM is left alone and told to shut down,
  since discarding its state is a power-off), then the page checks again.
  No admin rights needed. A PC with no host-only network at all is its own
  ✗ (`hostonly`, no button): making one installs a driver, so a person does
  it in VirtualBox's Network Manager. **The guest firewall** is opened by
  the run itself (`open_guest_firewall`, same day): when Windows is up
  (Guest Additions run level 3), someone is signed in, and the database
  hasn't answered 90 s after the start, it types into the VM's keyboard
  (`controlvm keyboardputscancode` / `keyboardputstring`, which need no
  Windows logon, unlike guestcontrol and the blank password): Win key,
  `cmd`, Ctrl+Shift+Enter (Win+R can't elevate on Windows 7), Alt+Y at
  UAC (EMO is an administrator), then `netsh advfirewall firewall add rule
  name=AutoRundownDB dir=in action=allow protocol=TCP localport=any
  remoteip=<subnet>.0/24` (deleted first, so re-runs don't pile up) and
  `exit`. A rule, not the firewall off: TCP from the host-only network,
  which reaches only this PC. **Any port, not 2054/2034**: Transbase's
  multiplexer on 2054 / 2034 hands each login on to a kernel on another
  port. A rule for 2054,2034 (then 2020-2060) let Test the VM pass --
  it only checks the port answers -- and every real login fail with
  "Transbase Client Error <30209> IO exception on socket", one VM
  power-cycle after another. Once per run; the rule stays in the VM.
  Checked from `AutoRundown-base` through the installed app: typed at
  +98 s, the database answered 22 s later and the real pulls logged in
  (model links: 33,085 Baumuster rows). The databases start with Windows
  on that VM -- opening "Mercedes WSM EPC" is not needed (tried both). Found on the way: a Windows blue
  screen (IRQL_NOT_LESS_OR_EQUAL) during an earlier VM test, a few
  minutes after a port probe of the VM from the PC (ping, 135, 445, 3389);
  the cause is not known (also seen 2026-10-23), kept as the snapshot
  `AutoRundown-crashed-20261029`. A crashed Windows ignores the power
  button, which is what "did not shut down when asked" meant then; Guest Additions (without them "Windows didn't start" can't be
  told from "the database isn't answering"); Java; `tbjdbc.jar`; every
  script the chosen steps run (an older extractor copy lacks the EPC ones);
  free disk (images ~30 GB). An extractor too old to know `--check` prints
  no CHECK lines: that is a failure saying to update the copy. `POST
  /api/import` runs the same check and refuses with the failing messages.
  **The page runs it only on a new pick** (2026-10-25, the user: "not …
  every time i hit that tab", only on a new VM or the test button), or when
  *What to extract* / the steps change what the run needs. Opening the tab
  with the remembered VM shows the last result of this page visit for the
  same VM, what and steps, else "Not checked since this page was opened"
  with *Check now*; Import and *Test the VM* stay on, since both check the
  VM on the server first.
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
