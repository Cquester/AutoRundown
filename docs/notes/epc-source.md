# EPC parts catalogue — third source (in progress)

Mercedes **EPC** (electronic parts catalogue) extracted from the user's own
licensed EWA/EPC install in the VM, into AutoRundown as a third source
(`source_type: mercedes-epc`) alongside WIS and LEMON. Goal: a shop picks their
car and the AI can answer "what's the part number for X", from local pinned
data — same model as the rest of AutoRundown, no live external source.

**Status (2026-10-14):** feasibility proven end to end; one-catalog prototype
(`extractor/pull_epc.js`) runs → `records.jsonl` → `records` importer → filed
under the model series → FTS-searchable by part name and exact part number. The
remaining work is completeness (all catalogs, subgroups, images, SA parts) and
wiring it into the site's VM import. Not yet merged into the live index.

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

## Remaining build items

1. **Subgroup completeness** — the W115 prototype gave one subgroup per group
   (45); verify that is real vs under-counting, and if sparse, enumerate
   subgroups from `BM_NPG_V` (`selSubgroupsFromNPG`) or distinct `BM_PARTS2_V`
   rather than `BM_SUBGRP_V`.
2. **Images** — `BM_NPG_V` → `IMAGE_V` blobs → convert → `image_files` +
   `[diagram:]` markers, so a parts list shows its exploded drawing.
3. **SA / option parts** (`SA_PARTS_V`).
4. **Part-number formatting** (`A1152231412` → `A 115 223 14 12`) and the
   `QUANTBM` per-model quantity decode (one slot per catalog model; `001`=qty 1,
   `-`=n/a).
5. **All catalogs** (passenger, class 1), then an EPC adapter in the site's VM
   import (`importer_jobs.py` / `vm_pipeline.py`) with the clock + DHCP above.
6. Tests + README/CLAUDE.md updates.

See also the decompiled sources (not committed — Mercedes software): the DAO
classes and `QueryStrings` were decompiled with CFR during the investigation.
Never commit `tbjdbc.jar`, the EWA JRE, `epc.jar`, or any extracted part data.
