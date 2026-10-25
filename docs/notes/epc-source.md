# EPC parts catalogue — third source (in progress)

Mercedes **EPC** (electronic parts catalogue) extracted from the user's own
licensed EWA/EPC install in the VM, into AutoRundown as a third source
(`source_type: mercedes-epc`) alongside WIS and LEMON. Goal: a shop picks their
car and the AI can answer "what's the part number for X", from local pinned
data — same model as the rest of AutoRundown, no live external source.

**Status (2026-10-14, evening):** `extractor/pull_epc.js` v2 pulls a catalog
completely -- parts, option (SA) parts, exploded drawings as PNG, formatted part
numbers, per-baumuster fits and quantities, supersessions -- and resumes across
catalogs. Catalog 001 (W115): 45 + 254 records, 7,732 + 7,796 part rows, 441
drawings (11 MB), ~200 s; ingested into a throwaway index it files 299/299
under MODEL 115 (100% structured agreement, no stray vehicles) and finds parts
by spaced, compact and bare-digit number, name and option text. Not in the live
index; all-catalog run and the site VM adapter are next.

**All 282 chassis catalogs pulled (2026-10-14):** 45,639 records (44,480
catalog + 1,159 SA), ~2.65 M part lines, 44,963 drawings, 1.6 GB, in
`Mercedes Documents/autorundown_out/epc_extract/`. Throwaway ingest: 72 series,
100% structured agreement, no stray vehicles, 1.3 GB index. The run crashed
at the manifest step on a cp1252 records file (fixed: UTF-8 writer; the file
was re-encoded losslessly, original kept as `records.cp1252.jsonl.bak`).
Engine catalogs (`AR_CATNUM=engines`) work in a 3-catalog trial but are not
in the corpus yet -- see "Joining WIS" for why they would be dropped today.

## How EPC is reached (it is NOT the WIS path)

EPC had been written off as "access-protected". It isn't — it was just a
different shape:

- EPC's UI is a Java Web Start **fat client** (`epc.jar`, Snap-on), not a web
  page; its server classes live in the VM webapp `EPC-net/WEB-INF/classes`
  (`com.snapon.sbs.epc.server.*`). We pulled `epc.jar` and the server DAO
  classes and decompiled them (CFR) to learn the data access.
- The catalogue lives in Transbase DBs **ALLTEXT** and **COMM** on **port
  2034** (a separate instance from WIS's 2054). The DB login: open
  `SPECIALFILES` as **tbuser** (empty password — already granted), read `PWORD`
  from `PWORD_V`, **ROT13** it (`EPCConfigManager.decyptPW` is a plain
  Caesar-13 — the DES/`DesEncrypter` in the jar only obfuscates part *names* in
  transit, not the DB password), then open ALLTEXT/COMM as tbuser with that
  password. `pull_epc.js` does exactly this; the password is never printed.
- The earlier survey's "ALLTEXT/COMM refuse tbuser" was a blank-password
  attempt; the real password is the ROT13 of PWORD.

## The schema / query chain (from the decompiled `QueryStrings`)

Parts are in **`BM_PARTS2_V`** (not `BM_PARTS_V`). `PARTNUM` values of all
`XXXXX`/`YYYYY` are **known placeholders the server itself skips**
(`PartBL`: `if (partNum.indexOf("XXXXX")!=-1 || …YYYYY…) continue;`) — not
masking; the real rows alongside them are genuine part numbers.

- catalogs: `select CATNUM, MARKETS, MODEL_YEAR, DATE_FROM, DATE_TO … from APPL_CAT_BASE_V`
- catalog → vehicle: `select type, subbm1, subbm2, class, salesdes from models_v where catnum=?`
  — `type`(+`subbm1`) is the **series / baumuster** (e.g. catnum 001 → type 115 =
  W115), `salesdes` is the sales name (220/230/240 D …), `class 1` = passenger car.
- groups: `select GROUPNUM, TEXT from bm_group_v join BM_DICTIONARY_V using(DESCIDX) where LANG=? and CATNUM=?`
- subgroups: `select GROUPNUM, SUBGRP, TEXT from BM_SUBGRP_V where CATNUM=? and GROUPNUM=? and LANG=?`
- parts: `select …CALLOUT,PARTTYPE,PARTNUM,QUANTBM,NOUNIDX,DESCIDX… from BM_PARTS2_V where CATNUM=? and GROUPNUM=? and SUBGRP=? order by SEQNUM`
  (`selBMPartsAndDescs` also coalesces `part_descs_v` for the description)
- part name: `select NOUNIDX, NOUN from part_nouns_v where LANG=?` (NOUNIDX → name)
- images: `select IMGTYPE,SEQNO,RESTIMG,CALLOUT,SUBGRP,GROUPNUM,SEQNUM from BM_NPG_V where CATNUM=? and GROUPNUM=?`
  then the blob `select IMAGE from IMAGE_V where IMAGE_NAME=?`
- reverse (have a part number, want vehicles): `BM_PARCAT_SEARCH_V where PARTTYPE=? and PARTNUM=?`
- SA / option parts mirror via `SA_PARTS_V` / `SA_NPG_V`, keyed on `SANUM`.
- `DB_LANGS_V` lives in SPECIALFILES, not ALLTEXT (single-char lang codes; `E`=English).

## Filing into AutoRundown (the records importer)

One record per `(catalog, group, subgroup)`; `body_text` is the parts list
(callout | part number | name). **Applicability must be the real series +
baumuster as `MODEL 115` / `MODEL 115.xxx`** — NOT the sales designations.
Emitting `"MODEL 220"` (a /8 engine-size sales name) made the importer build
four fake vehicles; with the series/baumuster the structured links agree 100%
and it files under MODEL 115, the same vehicle WIS uses. Sales names go on a
non-`MODEL` line. No importer change needed.

## VM setup for site-driven extraction (shops just point at their VM)

The site runs the extraction; end users should do as little as possible.

- **Clock → 2008** (keeps the EWA/EPC licence valid): fully host-side — set
  `VBoxManage modifyvm <vm> --biossystemtimeoffset <(2008 − hostNow) ms>` before
  boot. Guest Additions time-sync is already off. No guest interaction.
- **IP** (the 169.254 APIPA seen after a host reboot): enable the host-only
  **DHCP server** host-side (`VBoxManage dhcpserver`); guests then auto-get a
  192.168.56.x address. No guest static-IP setup.
- **Firewall**: the one guest-side thing, and it cannot be scripted host-side
  here — `guestcontrol` needs a guest Windows logon, and this VM's account
  (`EMO`) has a **blank password**, which Windows refuses for the logon type
  `guestcontrol` uses (tested: "user was not able to logon"). So the old
  `extract.ps1 -GuestUser/-GuestPassword` firewall step can't run unattended.
  Plan: these appliance VMs usually ship firewall-off; the pipeline connects
  and, if blocked, shows a one-time "turn Windows Firewall off in the VM"
  message (the same bar WIS is at today — `vm_pipeline.py` doesn't touch the
  firewall). **True zero-touch** would need the offline route: read the
  Transbase DB files off the VDI and run `tbserv32.exe` on the host (no guest
  firewall in the path) — a separate, heavier project, deferred.

## What the probes established (2026-10-14, `extractor/probe_epc_parts.js`)

- **Subgroups:** `BM_SUBGRP_V` is complete for 001 (45 = distinct pairs in
  `BM_PARTS2_V` = `BM_NPG_V`); old catalogs simply have one subgroup per group.
  The pull still unions in any subgroup seen only in `BM_PARTS2_V`.
- **Fits and quantity:** `SUBMODS` is the row's list of `SUBBM1` suffixes
  (3 chars each, blanks allowed) -- the baumuster the part fits; `QUANTBM` is
  the quantity in the same 3-char slots (`001` = 1, `-` = not stated, the usual
  form of a superseded number). `REPTYPE`+`REPPNO` is the replacement number.
  Rendered as `qty 1: 115.000, 115.002; qty 2: 115.114` or `qty 2, all models`.
- **Part numbers:** `A` + 10 digits written 3-3-2-2 (`A 115 223 14 12`, extra
  digits = colour code), `N` + 12 digits 6-6. The line carries the spaced form,
  the compact form and the bare digits, because FTS reads `A1152231412` as one
  token and shops type all three.
- **Drawings:** not in ALLTEXT. `BM_NPG_V` rows name them `B` + group(2) +
  subgroup(3) + `SEQNO`(2) + `RESTIMG` (`B24001000003.940127`); `ARCHIVED=1`
  ones are in database **`BM_IMAGES_ARC`** (126,567), the others in
  `BM_IMAGES` (5,002, named catnum+group+subgroup+seqno). SA drawings: `S` +
  SANUM(6) + SEQNO + RESTIMG in `SA_IMAGES`/`SA_IMAGES_ARC`, rows in `SA_NPG_V`
  keyed on `SANO`. `PNG_IMAGES*` (in `epc_cfg.xml`) do not exist in this
  install. `…99999.…` names are the client's "no drawing" placeholder.
- **Drawing format is FV4**, Snap-on's own run-length bit-plane format (1024 px
  wide, <=1280 rows, 1/2/4 bpp, ~16 KB each). `extractor/epc_fv4.js` is a line
  for line port of the client's `ImageExtractorFV4`; checked offline on a saved
  blob (all rows decode, correct drawing), ~0.5 s per drawing in Nashorn.
  The annotation overlay holds callout x/y positions; `fv4Callouts` reads
  them (format under "Tappable callouts" below).
- **SA parts:** `BM_SAIDX_V(CATNUM, GROUPNUM) -> SANUM` + option codes
  (`CODEONE`, 3 chars each). `SANUM` is CHAR(6) and the server pads 5-digit
  numbers with a leading space -- unpadded queries return nothing. Part rows
  give `STROKE` and `QUANTSA` as comma lists; a stroke maps to baumuster per
  model series through `sa_strokes_v` (the same stroke number recurs for
  different series). An SA is shared by many catalogs, so the pull writes each
  (SA, subgroup) once, limited to the series pulled in that run.
- **Scale:** 9.33 M `BM_PARTS2_V` rows, 2.21 M `SA_PARTS_V` rows, 2,740
  catalogs. Passenger cars = `models_v` class 1: 282 chassis catalogs (`FG`,
  what `AR_CATNUM=all` pulls), plus 161 engine (`M`), 61 gearbox (`GA`), 24
  `GM`, 5 `LG` aggregate catalogs (not pulled yet; would file under engine
  buckets).

## Footnotes (built 2026-10-15)

`extractor/epc_footnotes.js` decodes them (probe step 10 and the decompiled
`FootnoteRev9Serial`): old-style text ("UP TO CHASSIS: 100,102 126027"),
and revision-9 serial footnotes -- `ABBR` E + A(from)/B(up to) + unit (A cab,
F chassis, M engine, G transmission, L steering, V/H front/rear axle, Z
transfer case, P platform), `TEXT` = ident[0:8] date[8:16] model-year[16:21]
P[21] M[22] extra idents -- as "up to engine 32946334 (change date
2015-06-11)", "from engines built 2008-02-28 (plant 3)", "also / not
engines ...". In old-style text, codes after "MODEL" are often the
catalog's own variants ("FOR MODEL 970-975" = engines 119.970-975).

**Corpus format 3: the footnote text is on the part line** ("fn 003: up to
engine ..."; a long one keeps its condition there, its engine lists or
text table go to a "Footnotes:" list at the end). Format 2 had all texts at
the page end, and a page is indexed in ~2,400-character chunks: the OM651
glow plugs sat in chunk 1, their footnotes in chunk 21. A resumed run on an
older format keeps it as a `.bak` and rebuilds the records (drawings reused).

**A parts result is the page's matching lines** (`search._parts_snippet`):
BM25 picks the chunk saying the words most often, which on a parts page is
not the part asked for ("glow plug" on the OM651 page ranked the
wiring-harness chunk over the four glow plug lines). The answer now gets the
page header plus every line matching the question's words (minus "part",
"number" ...), with their footnotes; same page_no, so citations are
unchanged. The parts tail is ranked best-score-first across the parts
buckets (one FTS table, comparable scores): round-robin gave a whole-series
W203 scope's second slot to an engine bucket's weak OR match.

The format-3 run (Shop setup, 34 min for 443 catalogs) finished and then
ran the 32-bit JRE out of memory reading the 531 MB records file whole for
the manifest -- and again on every resume, which the pipeline power-cycled
the VM for six times. Both reads now stream; `vm_pipeline.py` stops at once
on OutOfMemoryError / SyntaxError instead of rebooting the VM. The manifest
for that run was written afterwards from the complete records file.

**Paid answers 2 and 3 (2026-10-15, the user's OK; Haiku, $0.0198 and
$0.0161, throwaway index).** Same question (W204 C 220 CDI glow plug
number). With footnotes inline and the parts snippet, the model saw the
four glow-plug lines with fn 003 on the pencil-type one. Answer 2 used the
footnote correctly (73 01 up to engine 32946334, replaced by 80 01; check
the engine number). Both answers **left out the standard glow plug
A 001 159 66 01 ("qty 4, all models")**, which the passage showed -- answer
3 even after a prompt line "list every current number that fits; do not
pick one". Prompt wording is not enough here, so **`query._add_omitted_parts()`**
(built the same day, the user's choice) adds them after the model: on the
parts pages the answer cites, each current line fitting the chosen car and
not named in the answer becomes a spec citing that page, plus a caveat;
`added_parts` in the result says what code added. Tested free: unit tests
on the real OM651 lines, and Ask end to end through the fake API
(`fake:onepart` gives one plug, the other is added). Not yet seen on a paid
answer. Duplicate parts
passages (one page under three chunk numbers) are fixed.

## Result tabs (2026-10-15, the user's request)

Parts pages in the manual's result list made it hard to read, and a parts
page in the reader offered "Original layout", which it cannot have (its
vehicle is joined into the WIS car, so the UI took it for a WIS page). Now
`/api/search?tab=` and a tab bar: **Workshop** (manual pages only, as
before EPC), **Parts** (the joined catalogue, each row with its matching
part lines, `search.part_lines`), **Labour times** (the dialog moved into a
tab), **Capacities** (WIS's oil figure + capacity lines), **Torque specs**
(torque lines). Since 2026-10-16 Capacities and Torque specs are one table
each -- Fluid / Capacity, Part / Specification, no paragraph (the user:
"just give a table") -- from `search.value_row`, which splits a value line
into what it is and the value as printed (pipe cells joined; "59 lb.ft /
80 Nm" kept whole, never converted; WIS's "Liter 1,8 1,8" all value; a turn
"+ 90 degrees" with its torque; a line with nothing left to name it named by
its page title). `/api/search` returns `rows` beside `lines`; each row opens
its page; WIS's oil figure is the Capacities table's first rows. Value lines come from `search.value_lines`: WIS prints
"Nm 50" and "Liter 6,5" unit-first, and capacity rows often unit-less under
a header ("Oil capactiy in liters ... changing oil and filter 5.0"); a line
must name one of the question's words (or its page title must), and a line
naming only another engine/model of the series is dropped
(`query.mentioned_codes`, as for answers). The tab bar has its own class:
`.srctabs` buttons are all bound by the Shop setup import code. A parts
page opens without the Reading/Original toggle; WIS pages keep it.
Checked on WIS + EPC in a browser (desktop and 375 px), `test_ui_picks.py`
(4,610 picks), and `test_epc_join.py` (each tab's kind; a bus page's
capacity never reaching a car).

**The tabs moved to the right box (2026-10-16, the user: "have the different
tabs be for the right box. So a user asks a question and ai can populate
everything in those fields").** The left holds the answer or the keyword
list; the right box's tabs are Manual page (the page view) and the four
above. Every Ask fills them all with the AI's own search terms (`queries`
in the answer) and every Search with the words typed -- through
`/api/search?tab=…&q=…&also=…`, `also` being up to 4 more queries merged
round-robin with `q`, as the answer's retrieval is. No extra AI call: the
user picked this over the AI writing each tab (3-5x the cost, and against
"the answer points at the page, it does not retell it"). Labour times need
every word of a query, so each term is tried alone, value words ("torque",
"capacity") dropped, until one finds operations. Found while building it: a
value tab dropped a page when the first query to reach it had no value
line naming its words, even if a later query had -- a page now counts as
seen only once a row for it is kept. On a phone the tabs stay the bottom
bar; a tap opens the Document view on that tab. Test:
`tests/test_answer_tabs.py` (the Ask is answered by the test, never sent).

**Torque specs and Capacities are the job's values, not a keyword search
(2026-10-16, checked with Haiku on the live index).** For "replace oil level
sensor" on a 2001 E 320 4MATIC wagon the two tabs, searching
"<question> torque" / "... capacity", listed the transmission's torque
converter bolts, ATF, rear axle and level-control fluids, every engine's
column of a table as one value ("Liters 8.0 8.0 7.5"), and "Oil level sensor
on oil pan top part -- 20 Nm", where the page's 20 Nm is the crankcase screw
plug: the index text puts each table row's value next to the *next* row's
name. Now:

- `/api/values/{doc}?kind=torque|capacity&pages=…` (`tab_values.job_values`)
  reads the job's pages -- after an Ask the answer's `document_page` +
  `source_pages` (with no answer, the first pages it was looked for on);
  after a Search the top 3 hits -- and, for WIS, the documents they link to
  (`wis_links.resolve`, AR/BA/BF/BE only, in scope only: another model's
  document is never a value here). The oil level sensor gets the oil pan
  bottom's self-tapping screws (14 Nm / 10 Nm) from "Remove/install bottom
  part of oil pan"; the answer itself never saw that page.
- WIS tables come from the SIDS layout (`wis_tables.py`, on
  `wis_original.layout`): a header row ("Number | Designation | | ENGINE 112
  (except …) | ENGINE 112.916/…") and rows of number, name, unit, a value per
  column. `label_fits` checks each column label against the car's exact
  engine and model codes: English and German ("MOTOR 104.945 im TYP 210",
  "außer CODE 450"), WIS's shorthand (112.910/911/ 912; 210.006/…/28 =
  210.28x), "except" lists, alternatives after a comma; a label that also
  needs something the car box can't know (a code, a chassis number, "up to
  31.01.98") is "maybe" and shown with "Only for: …". Two labels in one
  header cell ("Engine 112… Engine 113…" over "14 14") are split; a row
  whose values can't be paired with columns is left out. Stage rows keep
  their turn ("Stage 2: 60°").
- `/api/fluids/{doc}` (`tab_values.fluids`): the car's capacity sheets
  (`wis_links.capacity_sheets`: BF..-P-1000, not the BF..-Z-9999 index
  documents), each in the car's column, engine oil with the MB 229.x sheets
  its table links to; grease (grams) left out, refrigerant kept. General-pool
  sheets count only when a column names the car (`strict`), so ATF -- listed
  per gearbox, which the car box doesn't know -- is not on the card (the UI
  says so). No sheet at all for the W201 (checked): the card says WIS has
  none and the oil figure falls back to the ASSYST table (`/api/wis/oil`).
- Other manuals and WIS pages with no value table use the text lines
  (`value_lines` / `value_row`), fixed for "Stage 1 Nm 20" (was "1 Nm"), "5.5
  Liters 5.8 Quarts" (was "Liters 5.8"), turns right after a torque ("55 Nm
  90° 90°"), and a unit-first line's value going to the line before.
  Not fixed: LEMON tables that print no unit ("| 55 | 90 degrees |") stay
  out -- the unit would be a guess.

Tests: `tests/test_wis_tables.py` (invented SIDS bodies in WIS's format),
`tests/test_answer_tabs.py` (the tabs read the answer's pages and the top
hits, and run no keyword search of their own).

## Joining WIS (found and built 2026-10-14)

"Files under MODEL 115, the same vehicle WIS uses" was true of the *series
code* only. `search.scope_ids` searches the chosen vehicle's own doc_id, its
source's general pool and the engine buckets its variants link to -- all from
one source. The records importer builds vehicles per corpus, so:

- EPC's `MODEL 210` becomes its own vehicle (`mercedes-epc-en-210`), a second
  W210 in the picker; the WIS W210 a shop picks never searches EPC parts.
- Engine buckets are kept only if a passenger variant lists that engine, and
  variants (with engines) come from the corpus's own `models.jsonl`. EPC has
  none, so every EPC engine bucket is dropped as "commercial-only" (seen in
  the trial: "leaving out 3 ... engine buckets").
- Chassis catalogs hold no engine internals (W210 "oil filter" finds springs),
  so engine catalogs matter for the most common parts questions.

**Built (the user approved the design 2026-10-14), at query time so each
source stays independently rebuildable and removable:**
- `index.link_sources()` (run by `index.vehicles()`): WIS car ->
  `joined_ids` (EPC vehicles of the same maker + `model_code`) and
  `engine_joins` (engine family -> EPC `E<nnn>` buckets); the EPC vehicle
  gets `joined_into` and `/api/vehicles` leaves it out. Only `doc_type
  "parts"` vehicles are joined in.
- `search.scope_ids` adds them after the car's engine buckets; variant and
  engine choices narrow the EPC engine buckets like the WIS ones.
- **Page numbers `P<dok_id>`** (`records.SOURCE_PAGE_STYLE`, manifest
  `page_no_prefix`; `pull_epc.js` writes it): page_no is the citation key
  and EPC dok_ids overlap WIS DokIDs. `tests/test_epc_join.py` fails 17
  checks with the prefix removed.
- The records importer keeps engine buckets for a corpus with no variants.
- `/api/image` falls back to the joined corpora's `images/`.
- Answer side: passages from parts pages are headed `(parts catalogue)`,
  and `ANSWER_SYSTEM` says part numbers come only from those pages, for
  this car's fits, superseded numbers replaced. **Not yet tried on a
  paid answer.**
**Combined check (2026-10-14; WIS + the 282 chassis catalogs in one
throwaway index, engines not pulled yet).** `eval_wis.py` ranks, live WIS
index / WIS+EPC with the join off / joined:
- Interleaved, the join cost manual pages a rank or two (W124 brake pads
  5 -> 7, wheel bolts 4 -> 5, W140 pads 4 -> 5, W211 manual trans 6 -> 7). So
  parts are now **searched as their own group and appended** after the
  manual's results (`Scope.parts`, `search.parts_slots`: max(2, limit//4) rows
  in keyword search; `PARTS_CAP` 4 passages in Ask, ~2.5k tokens): the
  manual's top N is identical with and without the join.
- One title per parts result: a series has one catalog per market/period (W124:
  4,178 parts pages, 582 distinct titles), so the tail is de-duplicated on the
  title without "(EPC catalog …)", over-fetching x12.
- **A chosen car gets only the catalogues listing its model code**
  (2026-10-25). Each catalogue page names its models in `section_path`
  ("MODEL 126 > 126.044, 126.045, 126.046"; 47,400 of 52,520 parts pages),
  but `codes_only` is empty for EPC, so the codes filter passed every one.
  For a 560 SEC (126.045), "radiator shroud part no" got four of the W126's
  13 RADIATOR pages, all for other 126 models, and the answer rightly said
  none covered 126.045 -- A 126 505 01 55 sat on three pages it never saw.
  `search._run_scope` now keeps a parts page whose section line lists codes
  only if it lists one of the car's (model or engine); engine pages ("ENGINE
  117") list none and pass. Query time, so no re-ingest. Only with a variant
  chosen. Test: `tests/test_epc_join.py`; `eval_wis.py` unchanged.
- **Parts have their own FTS table (`parts_fts`), the user's choice.** In the
  shared `chunks_fts`, EPC shifted WIS BM25 scores even with the join off
  (statistics are index-wide): `w204-c220cdi-glow-plugs` gained a W207 page
  at #3 and failed. Now `chunks.parts` routes each chunk to one table (the
  triggers in `index.FTS_TRIGGERS`), search uses `parts_fts` for the parts
  group and for an EPC-only car, and **all 19 WIS cases on WIS+EPC match the
  live WIS-only index exactly** (W204 back at rank 1). An older index migrates
  once at start-up (`index._migrate_fts`: new triggers; parts chunks already
  in `chunks_fts` are moved -- 213,761 in 30 s on the combined index, both
  tables pass FTS5 `integrity-check` after).
- Parts cases `epc-w124-part-number` (exact number, spaced) and
  `epc-w203-brake-pads` (FRONT WHEEL BRAKE) pass; skipped without EPC. Their
  `foreign` ceilings (1 and 3) are WIS engine-bucket pages, the known
  [engine-bucket] pattern, not EPC results.
  Engine parts (oil filter, glow plugs, thermostat, water pump) are in the
  engine catalogs.

**Engine catalogs in (2026-10-15).** The 161 passenger engine catalogs were
pulled from Shop setup (`vm_epc` in a dev.py instance, 42 min including
the ingest, after a first attempt was cut off by a Claude-app update): the
corpus is 443 catalogs, 50,759 records, 46,000-odd drawings. On WIS + all of
EPC: 146 EPC vehicles (72 series, 74 engines), 120 joined into WIS cars or
engine buckets, 26 old series WIS lacks listed on their own; 50 WIS cars
reach engine parts (W210: 12 engine families, W204: 8). Read by hand and now
eval cases: W210 "E 320" (M104 + M112) oil filter -> both engines' filter
pages, M112 element A 000 180 26 09 with its supersession; W201 190 E 2.3-16
thermostat -> M102 cooling system; W204 C 220 CDI glow plugs -> OM651 GLOW
PLUG qty 4, A 001 159 66 01; W211 E 320 CDI fuel filter -> OM642 and OM648.
No wrong engine in any parts tail with a variant chosen. eval_wis on that
index: 25/25, 18 known-bad guards within ceilings (the new EPC cases' guard
offenders are WIS general-pool or engine-bucket pages, not EPC rows).
An engine page's "Fits" lists engine codes (102.990 vs the 2.3-16's 102.983),
so the answer rule now says model code *or exact engine code*.

**First paid answer over EPC (2026-10-15, the user's OK; Haiku, $0.0217, 2
calls, 18.9k in / 0.6k out; logged in the throwaway index only).** "What is
the glow plug part number?" for a W204 C 220 CDI (651.911) on WIS + EPC:
A 001 159 66 01, qty 4, replacing 58 01, plus the pencil-type A 001 159 80 01
(replacing 73 01) -- every number, quantity, supersession and fits list
matches page P49767 exactly; cited only P49767, `document_page` P49767, no
dropped citation; years from the curated table, not recalled. Two notes:
it listed the superseded numbers as their own (labelled) entries, though the
rule says to give the replacement; and it could not say whether the car
takes the standard or the pencil-type plug -- both fit 651.911 on the page,
and EPC's footnotes (not extracted yet) are what usually decide it. It said
so in `caveats` rather than guessing.

## Remaining build items

1. **Combined check:** WIS + EPC (with engines) in one throwaway index;
   eval cases such as "W210 oil filter part number" (engine parts reached,
   other engines excluded) and the crowding of repair searches; then one
   scoped paid answer (ask first).
2. ~~Engine catalogs into the corpus~~ -- done 2026-10-15 (above).
3. **Footnotes** (`FOOTNOTES` codes on part rows, `SA_FOOTNOTES_V` for SA) --
   not extracted; they carry "up to chassis …" conditions. **Next in value:**
   the first paid answer could not choose between two glow plugs that both fit
   651.911 on the page -- the footnote is what tells them apart.
   **Built 2026-10-15** (see "Footnotes" below).
4. **Gearbox catalogs** -- need a car -> gearbox map first.
5. **Site integration: built 2026-10-15, not yet run from the site.** The
   Mercedes VM tab's "EPC" (or "Both") choice (`docs/notes/importing.md`).
   **No clock or DHCP change after all:** the EPC database answered with the
   VM's `biossystemtimeoffset` at 0 (2026-10-14) -- the 2008 clock matters to
   the EPC *program's* licence, not to reading its database -- and the
   pipeline's standing rule is to start/stop the VM and read, never change its
   settings or the host network. Unreachable database -> the existing
   one-time message (start "Mercedes WSM EPC", host-only network, firewall
   off), the same bar as WIS.
6. Callout hotspots from the FV4 annotation overlay: **built 2026-10-23**,
   see "Tappable callouts" below. An extract pulled before then has no
   positions until the next pull (the PNGs are kept; the pull re-fetches
   only the blob of a drawing with no sidecar). **That first version never
   reached them on a finished extract**: a resume skips every done catalog,
   so the user's 50,570 drawings from 2026-10-15 had 0 sidecars and no
   number on any drawing could be tapped. `backfillCallouts()` now runs at
   the start of every EPC pull with images: every PNG without a sidecar is
   read once more from BM_IMAGES(_ARC) / SA_IMAGES(_ARC) for its callouts
   alone (a non-FV4 drawing gets `[]`, so it isn't asked again), with
   progress every 500. Checked in Node with a stand-in database; not yet on
   the real one.
   **Tried first and dropped (2026-10-23): reading the callout numbers off
   the PNGs.** A glyph reader taught from the parts lists themselves (blobs
   grouped into words, words matched to the page's callouts, retrained over
   rounds; no OCR library) placed 76% of callouts on held-out pages, and
   every callout on only ~50% of pages; letting the list decide doubtful
   readings made it worse (66%, copies on leader-line stubs). Not good
   enough to point a mechanic at a part, so the user chose the overlay.

## Parts diagrams (built 2026-10-17, the user's request)

The extract already had the drawings (PNG, ~46k, 1.5 GB) in each parts
page's `images`, and 93% of part lines carry a callout ("007C") that is
printed on the drawing ("7C"). No new extraction:

- `/api/parts/diagrams/{doc}` (`parts_browse.py`): the car's parts scope
  (split_scope, filtered in SQL as search is: the chosen engine/model
  codes) as chassis / engines / options > group > subgroup, parsed from
  the page titles. A subgroup filed in several catalogs (markets, periods:
  W124 4,178 pages, 582 titles) is one entry listing every page, the one
  with a drawing first.
- The Parts tab shows that list before a question, and folds it under the
  results after one; each result has its page's first drawing as a
  thumbnail (`/api/search?tab=parts` rows carry `drawings`).
- A parts page opens with its drawings first, then the list as a table with
  a callout column (`calloutLabel`: "007C" -> "7C", "-" -> none); tapping a
  row shows "Find 7C on the drawing -- name, number" above the drawing.
- Box and tests: `tools/make_sample_epc.py` (invented parts + Pillow
  drawings for the sample W210/W201 and engines 112/102/103) is ingested by
  `dev.py`; `tests/test_parts_diagrams.py`.
7. Tests + README/CLAUDE.md updates.

**Shop words and the catalogue's words (2026-10-21).** Counted in both
extracts: EPC mostly uses the shop's own words (WATER PUMP 5,939 lines vs
COOLANT PUMP 515; MUFFLER, TIE ROD, OIL FILTER, CONTROL ARM), and
`synonyms.py` keeps searching the words typed beside its rewrite, so those
were never lost. Five shop words are in neither extract: "sway bar" (WIS
anti-roll bar; EPC STABILIZER or TORSION BAR), "coil pack" (ignition coil),
"flex disc" / "guibo" (WIS flexible coupling; EPC JOINT DISC) and
"harmonic balancer" (vibration damper). A phrase can now have several
manual words (a tuple); `synonyms.translations()` gives one query per
alternative, used by Ask's queries and every tab's search. Left out as
ambiguous: "driveshaft" (WIS uses it for axle shafts too; EPC says
PROPELLER SHAFT or AXLE SHAFT). Tests: `tests/test_synonyms.py`, and "sway
bar" finding the sample STABILIZER line in `tests/test_parts_diagrams.py`.
A parts page has "Back to Parts", which returns to the list with its groups
still open.

**Spellings (2026-10-23).** A W201 "Front window molding clip" Ask came
back not found although CLIP, GARNISH MOULDING BELOW WINDSHIELD
(A 201 988 05 78) is in the catalogue: parts passages *do* reach the
answer without a workshop page, but EPC writes MOULDING 34,231 times to
MOLDING 12,635, and WIS the other way round (molding 4,836, moulding 46).
`synonyms.py` now adds molding -> moulding, rocker panel -> sill,
weatherstrip -> sealing strip, tailpipe -> tail pipe; the typed word is
still searched too. "Front window" vs WINDSHIELD is left to the planner.
Not yet re-asked on a paid answer.

**Drawings as wide as the screen (2026-10-23, the user's request).** On a
parts page each drawing fills the box's width. On a wide screen "Full
width" gives the box the whole window while a parts page is open
(remembered on that device, `autorundown.wideDrawings`; any other tab or
page goes back to two columns). **The drawing is not a button and does not
fold:** a first try wrapped each in a "Drawing · tap to enlarge" section
with a full-screen zoom on tap; the user did not want either (a tap on the
drawing should pick a part -- that needs the overlay, item 6 above). A page
with several drawings (a radiator page has 5) puts each in its own
dropdown, "Drawing 1 of 5" open and the rest folded.

**Drawings open in place (2026-10-23, the user: "by clicking a drop down
it would generate the diagram").** In the Parts tab a subgroup is a
dropdown: opening it fetches its page once and shows the drawing and the
whole list right there, as wide as the box, with "Open as a page" for the
page view (and Full width). Each search result has the same dropdown
("Show drawing and all its parts") in place of the old thumbnail. A page's
`[diagram: …]` markers are no longer rendered under the list -- the
drawing showed twice. (The parts-only "Full width" was later replaced by
"⇤ Wider" for every tab, below.)

**Tappable callouts (2026-10-23, the user: "click on the diagram to select
a part").** The overlay format, read from the EPC client's own
`ImageExtractorFV4.saveCallouts` / `EPCCalloutButton` (`epc.jar`, fetched
from the VM's web server at `/EPC-net/servlets/epc.jar?version-id=2.4.1.0`;
Mercedes software, kept out of the repo): the header's overlay record
32/1 gives the block's offset (+6..7) and length (+10..11); in the block,
bytes 18-19 are a record stride and 20-21 the count; records start at
byte 22: a kind byte, x (2, big-endian), y (2), the label NUL-ended, the
next record at the stride rounded up to 4. x/y are the label's **centre**
in drawing pixels -- the client centres a button `len(label) x width` by
`height` there. `pull_epc.js` writes `<drawing>.callouts.json`
(`[{x, y, c}]`, `[]` for none) beside each PNG; `GET /api/callouts/{doc}/
{image}` serves it (same path checks as `/api/image`, items validated,
`[]` on any error). In the UI each drawing is a `.pdwrap` with transparent
`.pco` buttons placed in % of the drawing's natural size, so they scale
with it; tapping one marks its rows and scrolls to them, tapping a row
marks its number on the drawing (opening a folded drawing). A drawing with
no sidecar still works as before: the row names the number to look for.
`tools/make_sample_epc.py` writes sidecars for its invented drawings.
A picked number **turns red** as in Mercedes' viewer (the user, 2026-10-23):
the button carries the label and, when picked, draws it in red on white
over the drawing's own (font size 24 drawing px, in `cqw` of the
`.pdwrap` container, so it scales with the drawing); the row's callout
goes red too. **Several drawings: each dropdown lists its own parts**
(the user: "it should have the parts in the drop down associated with
that picture"). `splitParts` copies into each drawing's dropdown the rows
whose callout its sidecar has ("Drawing 2 of 5 — 4 parts"); a number on
two drawings is listed under both; rows no drawing shows (no callout, or
not printed) stay below as "Other parts on this page", hidden when there
are none. Without sidecars the page keeps its one list. Picking scrolls
within the same drawing's dropdown when it can.
**Several parts can be picked at once** (the user, 2026-10-24): a tap adds
a number to the picks (`.ppage` `data-picks`), a second tap on it takes it
off, and "Clear" empties them; every pick stays red on the drawing and in
the lists, and the line above the drawing lists each pick (`showPicks`).
Taking one off scrolls nothing.

**Manual contents and the divider (2026-10-24, the user: "change the Manual
page to be like the parts page ... auto fill the page in sections for the
car"; "what if i just want a little more or less").**
- Before a question (a car picked, Clear question, a model/engine change
  while the contents show) the Manual page tab shows the car's manual as
  contents: `GET /api/manual/contents/{doc}?variant&engine&path=…`
  (`manual_browse.py`), one level per call. The pages are the manual part
  of `scope_ids`, filtered as search filters them (general pool
  type-narrowed), so the contents are what a search for this car can find.
  **WIS**: a page's `section_path` only names models, so documents are
  grouped by their number (`wis_links.docnrs`): "ar4210…" = group 42 >
  subgroup 42.10, named by `wis_links.group_names` from WIS's `Gruppen`
  table (`wis_group`, pulled with the vehicle tables since 2026-10-24:
  "42.10 Service brake"; passenger-car names first, then another vehicle
  type's; before that table is pulled, groups take the labour book's
  `labour_group` names and subgroups only their number; checked on the
  real extract: 5,690 names, 264,711 of 271,643 numbered documents in a
  named subgroup, sampled titles fit -- 42.30 ABS "Remove/install ABS
  measuring ring", 88.20 Bumpers "Remove/install rear bumper"). Five
  kinds number their own items after the group digits (`OWN_NUMBERS` RA,
  WH, EA: listed in their group with no subgroup -- "wh0051" is item
  0051, not 00.51, which showed as an unnamed "00.51" until the user
  asked; `NO_GROUP` DH, WB: even the group digits are a chapter, so they
  go under Other documents); every other kind falls 98-100% in a named
  subgroup, and the rest (335 damage-code documents) show their number).
  **Damage-code menus are left out** (`LEFT_OUT` SS, the 99xxxxxx picture
  menus for picking a warranty fault code; their text is code lists):
  17,997 of them, filed under no car, made up over half of every car's
  contents -- the user saw "Connecting rod, piston" twenty times, for the
  M271.9, OM470 and a Fuso 4D34i, under a W210 (2026-10-24). W210 whole
  series 34,238 -> 16,241 documents. Search and Ask leave them out too
  (one ranked #1 for "connecting rod piston"): `search.show_damage_codes`,
  off unless Shop setup -> Manuals -> "Show Mercedes damage-code menus"
  (`Config.show_damage_codes`, the user's choice: off by default, a
  setting to show them). Both use one rule, `search.is_damage_menu` /
  `_DAMAGE_MENU`: an 8-digit DokID starting 99 -- on the real index exactly
  the 17,997 SS documents, nothing else. `eval_wis` ceilings rose on three
  cases: the menus' general-pool slots now go to untied general documents
  that name other models (the menus named other vehicles too, as "BR 400",
  which the guards don't read). Each document is listed with
  its kind; those left (pairs of one title) are one per engine, e.g. the
  E 320's M104 and M112 "Connecting rods"), each with
  what it is (Repair = AR, Function = GF, Service information = SI,
  Capacities = BF, Wiring diagram from `doc_type`; other codes shown as
  they are, never guessed); no number -> "Other documents". **Other
  manuals**: their own `section_path` chapters; a page whose title ends its
  path is listed in that chapter. A single top chapter opens by itself.
  The tree is built once per car (W210 whole series: 34,238 documents,
  ~2.4 s; a variant ~1.2 s) and cached against the index's page count and
  the sidecar's mtime. A document opens as the manual page with
  "← Manual contents", which puts the same contents back, sections left
  open. Tests: `tests/test_wis_links.py`, `tests/test_lemon_import.py`.
- The line between the question side and the right box is a divider
  (`#splitter`, wide screens only, hidden with "Wider"): dragged, or arrow
  keys; it stops at 300 px question / 360 px documents; kept as the
  question side's share in `autorundown.split`; double-click or Home puts
  the default layout back.

**AI chat and Keyword search, follow-ups (2026-10-25, the user's request).**
"If I had a follow up question it can search the database for me ... I can
say I tried this and from the manual it can respond to that"; the first
answer keeps its format ("it's gonna help you find the documentation you need
but not try and give you the answers"); a follow-up "could only use the
manual to respond, not any online source". Then: "a tab on the left page
where one will be AI chat and then one will be keyword search".
- The left side has two tabs (`#modeTabs`, `setMode`/`syncMode`): AI chat
  in `#out`, Keyword search in `#outKw`, each with its own typed text
  (drafts) and results; the right box shows whichever ran last. Remembered
  per device (`autorundown.leftMode`). Clear is "New chat" / "Clear search"
  and clears only that tab.
- A chat is one `history` entry: the first answer is its `result`,
  follow-ups go into `result.turns` (`history.add_turn`; cost added; the
  entry moves to the top). `/api/ask` takes `chat` = that entry's id; the
  server reads the entry as `who` can see it (else 404), requires the same
  `doc_id`, and takes the car (variant, engine, year) and the earlier turns
  from it -- never from the request. At most `query.MAX_FOLLOWUPS` (10).
- A follow-up is one more planner + answer call. The planner sees the
  conversation; the answer call uses `FOLLOWUP_SYSTEM` (ANSWER_SYSTEM plus:
  earlier answers are context, not sources; say what the page says the
  reported results mean or its next step, naming the step; if the manual
  doesn't say, found=false; never diagnose from general knowledge). The
  pages the chat cited (`_carried_pages`, newest first, at most 4, 8,000
  chars each) are read again from the index in the car's scope and lead the
  passages, marked "cited earlier in this conversation"; they are real
  passages, so `_verify_citations` and the other checks apply unchanged.
  A first question sends exactly the request it did before.
- The chat shows newest first under the box: the latest question over its
  full answer, then "Earlier in this chat", short. History shows "AI · N
  follow-ups"; opening a chat redraws every turn for free and can go on.
- Tests: `tests/test_chat.py` (the prompt the model gets, carried pages,
  citation checks, who may follow up, the car from the entry, the limit).
  **Not yet tried on a paid answer.**

**Wider, Clear question, History (2026-10-23, the user's list).**
- "⇤ Wider" at the end of the right box's tab row hides the question column
  on wide screens (≥901 px; remembered, `autorundown.collapsed`); "⇥ Show
  question" brings it back. Not on phones, where the box is already full.
- "Clear question" (shown after an Ask or Search) empties the question and
  the answer and puts every tab back to its no-question view -- Parts
  browse, the empty Labour times, the car's Capacities -- without changing
  the car. Changing car hides it.
- "History": every Ask (the whole answer as returned, `as_dict()`) and every
  question-box Search is kept in `auth.db` `history` (`history.py`; 500 per
  person, 5,000 in all). The PC sees everyone's; a signed-in mechanic their
  own; a phone not signed in only the not-signed-in ones. Opening an Ask
  redraws the stored answer and refills the tabs from its search terms --
  **no new AI call, no charge**; a Search is run again. The car is
  re-picked the recent-cars way (`resolveCarHistory` + `applyCar`).
  `GET /api/history`, `GET /api/history/{id}` (404 for someone else's).
  Tests: `tests/test_history.py`.

See also the decompiled sources (not committed — Mercedes software): the DAO
classes and `QueryStrings` were decompiled with CFR during the investigation.
Never commit `tbjdbc.jar`, the EWA JRE, `epc.jar`, or any extracted part data
(records, drawings).
