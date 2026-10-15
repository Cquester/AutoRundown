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
  The annotation overlay holds callout x/y positions (`saveCallouts`) -- usable
  later for clickable hotspots; not extracted yet.
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

## Joining WIS: the open design problem (found 2026-10-14)

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

Needed: a cross-source join -- a vehicle of one source also searches another
source's documents for the same series (and the same engine buckets), and
the second source's vehicle is not listed separately. Touches
`search.scope_ids`, the vehicles table and the picker
(`docs/notes/vehicle-selection.md` first), so it is a design decision, not
an extractor change. Engine catalogs can be pulled now; they become
reachable once the join exists.

## Remaining build items

1. **Cross-source join with WIS** (above) -- without it EPC is a parallel
   set of vehicles and its engine catalogs are unreachable.
2. **Engine catalogs into the corpus:** `AR_CATNUM=engines` into the same
   `AR_OUT` (resumes; the 282 chassis catalogs are skipped).
3. **Footnotes** (`FOOTNOTES` codes on part rows, `SA_FOOTNOTES_V` for SA) --
   not extracted; they carry "up to chassis …" conditions.
4. **Gearbox catalogs** -- need a car -> gearbox map first.
5. **Site integration:** an EPC adapter in the site's VM import
   (`importer_jobs.py` / `vm_pipeline.py`) with the clock + DHCP above and the
   one-time firewall message.
6. Callout hotspots from the FV4 annotation overlay.
7. Tests + README/CLAUDE.md updates.

See also the decompiled sources (not committed — Mercedes software): the DAO
classes and `QueryStrings` were decompiled with CFR during the investigation.
Never commit `tbjdbc.jar`, the EWA JRE, `epc.jar`, or any extracted part data
(records, drawings).
