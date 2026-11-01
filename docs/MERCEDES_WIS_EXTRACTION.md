# Extracting Mercedes-Benz WIS data from the virtual machine

How AutoRundown gets repair documents, model applicability, vehicle tables and
images out of a Mercedes-Benz EWA/WIS installation running in a VirtualBox VM —
everything learned the hard way, in one place. The scripts are in
[`extractor/`](../extractor); the corpus contract is
[`extractor/IMPORT_SPEC.md`](../extractor/IMPORT_SPEC.md); the early database
bring-up research is in [`extractor/bringup/`](../extractor/bringup).

**Code in the repo, data outside it.** The site runs `extractor/` from this
repo. What it reads (the EWA install) and writes (`autorundown_out/`) stays in
a "Mercedes Documents" folder beside the repo — `extractor/wis_paths.py`
finds it (`AR_OUT_ROOT` = the output folder, `AR_HOME` = the folder holding it
and the EWA install) and refuses an output folder inside the repo.
`python extractor/wis_paths.py` prints where it will read and write.

> Use this only with an installation and data you are licensed to use. The
> extractor reads the database (SELECT only) and never modifies the VM's disk
> contents. Mercedes' own software (the EWA install, the Transbase JDBC driver
> `tbjdbc.jar`) and all extracted data stay on your machine; none of it is in
> this repository.

---

## 1. The easy way: from the site

Settings (gear) → **Import data…** → **From a Mercedes virtual machine** → pick
the VM → **Extract & import**. This runs `extractor/vm_pipeline.py` and then
`rebuild_wis.py`. A first full run takes ~4 hours (mostly images); every step
resumes where it stopped, so a re-run only does what is missing. By hand:

```bash
python extractor/vm_pipeline.py --list-vms
python extractor/vm_pipeline.py --vm "Mercedes_Ultimate_207"     # --out-root <dir> to override
python rebuild_wis.py --archive "<out root>/wis_extract"
```

Steps: `docs` → `applic` → `vehicles` → `images` (VM on) → `build` (offline:
`parse.py` → `apply_applic.py` → `parse_vehicles.py` → `convert_images.py` →
`apply_images.py`). `--steps images,build` runs a subset.

The pipeline starts the VM headless, stops it, and reads the database. Before
a run it takes its own snapshot ("AutoRundown-auto-<date>-<time>", at most one
per 6 hours) so a run that breaks Windows can be rolled back; it never deletes
or restores one by itself (`--restore NAME`, from Shop setup, does). It never
powers the VM off (a shutdown that doesn't finish saves its state), changes
the network adapter or touches the guest's firewall. It finds VBoxManage, the Java bundled with EWA (`sun_jre`) and
`tbjdbc.jar` by itself.

## 2. One-time VM setup (by hand)

1. VM network adapter on **host-only** (guest usually `192.168.56.101`).
2. Inside the guest: log in, start **"Mercedes WSM EPC"** (it starts Tomcat and
   the Transbase databases), turn Windows Firewall **off** (the host-only
   network is reachable only from this PC).
3. Ports: **WIS 2054**, EPC 2034, ewacore 2044 (+1 each for the second port).
   NAT doesn't work — the Transbase multiplexer redirects to the guest's own IP.

## 3. Talking to the database

- Transbase over JDBC: `jdbc:transbase://<guest-ip>:2054/wisnet`, user
  **`tbadmin`, empty password** (the database login, not the EWA web login
  `admin/password`).
- No compiler needed: the 32-bit JRE bundled with EWA runs JavaScript through
  Nashorn: `java -cp tbjdbc.jar jdk.nashorn.tools.Shell pull.js`. Run from a
  space-free staging directory — Nashorn is fussy about spaces in paths.
- Scripts take `AR_HOST`, `AR_PORT`, `AR_DB`, `AR_OUT` (+ `AR_LANG`,
  `AR_TABLES`, `AR_LIMIT`, `AR_SLEEP`) from the environment.
- WIS uses a proprietary codepage and the driver decodes UTF-8 as cp1252 /
  Latin-1: "Coupé" arrives as "CoupÃ©", "–" as "â" + C1 controls.
  `fix_mojibake()` repairs a run only when its bytes are valid UTF-8.

## 4. What is where in WIS

| What | Where | Notes |
|---|---|---|
| Document per language | `W_SprachDokumente` (3.0M rows) | `DokID` (this language), `DokNrID` (shared across languages), `DokTitel`, `FzgArtCodes` (vehicle-type bits), `KurzgueltigkeitID` |
| Body | `ID2Blob_<cluster>` row with `DokID` and `GraphikID = 0` | gzip; LZW (.Z) for checklist XML |
| Graphics | `ID2Blob_<cluster>` with **`DokID = 0`**, `GraphikID = ?` | stored once, not under the citing document; cluster from `ID2Cluster` |
| Cross-references | `DokVerweis` (15M rows) + quoted docnrs in the body | |
| Model applicability | `W_Dok2Reihe` → `WS1-3`/`WC1-8` → `W_Baumusterzeile` | see §5 — the obvious join is wrong |
| Validity text | `W_Kurzgueltigkeit` | "MODEL 129 up to 31.12.93", all languages |
| Vehicles / sales names | `ASRA_FahrzeugII`, `ASRA_Fahrzeug`, `AW_Fahrzeuge` | engine per Baumuster via `MotorMin/MotorMax` / `MotorBM` |
| Vehicle type | `AW_Fahrzeuge.FzgArtCode` → `FzgArt` | CAR / 4xWD / transporter / TRUCK / BUS / UNIMOG / smart |
| US VIN → exact model | **`AW_VIN2FIN`** (7,870 rows) | first 9 VIN chars → WMI + Baumuster |
| US VIN → series | `USTypBM2TypBM` (38 rows) | WMI + position 4 |

**Body format — SIDS.** A page-layout language: blocks (`V … W`), positioned
frames (`V,,,x,y,w,h`), lines and boxes (`G`, `GP`, `GA`), paragraphs (`P`,
`PC,T,…` = a two-column step row), text runs in numbered fonts (`F95 SWISS 10`),
and graphics inline as `".P.<graphik_nr>|<w cm>|<h cm>|TIFF"`. Units are
1/567 cm. `parse_sids.py` extracts the text; `tools/sids_render.py` redraws the
layout (prototype). Printed figure numbers — "W15.18-1024-01" and the older
"P01-5220-13" — are stripped as clutter (the figure itself is placed by its
marker), except where the text refers to one ("see Fig. D27-0007-13", "wiring
diagrams W54-2328-71, … and W54-2349-71"). After a `parse_sids.py` change,
`reparse_bodies.py` updates `body_text` in place and refuses to write if more
than 50 bodies lose or change a word.

**Graphic formats** (by magic bytes): TIFF CCITT G4 → PNG, JPEG 2000 → PNG,
JPEG/PNG/GIF as-is, gzip'd SVG (wiring diagrams), gzip'd or .Z DXF R12 →
SVG via `ezdxf.recover` (WIS DXF reuses entity handles; a strict read silently
draws nothing). Vector graphics carry text labels (component codes like
`N26/15`, wire colours, `PE 54.21-…` references) — indexed so diagrams are
searchable.

## 5. Model applicability — the join that was wrong twice

`W_Dok2Reihe.ReihenID` is an applicability *combination*, **not** a
`BMZeilenNr`. Joining it straight to `W_Baumusterzeile` (the obvious join)
linked 98.4% of documents to the wrong model. The right join:

```
W_Dok2Reihe (DokNrID, ReihenID)            ReihenID -1 = all
  -> WS1..3 / WC1..8 (BMZeilenNr, ReihenID)   table = role:
        1 vehicle, 2 engine, 3 transmission, 4-8 other aggregates
  -> W_Baumusterzeile.BMReihe (series), BM4..BM6 (Baumuster)
```

Validated against the documents' own MODEL lines: **99.94%** agreement
(engine role 99.1%). Always validate an applicability source against the text;
the importer measures agreement on every corpus and ignores the links below 50%.

## 6. Stability — read before a long run

- **The in-VM database drops its connection after a long session** — about
  10 minutes into one, whatever the pace, sometimes mid-document. A reconnect
  doesn't help (the database is gone until the VM restarts). Every pull is
  resumable and checkpointed; the pipeline power-cycles the VM and resumes.
  Images go in **60k batches, no pause, a new connection every 5k, with the
  VM off between batches** -- about 3 minutes a batch, all 272k in ~22 min
  (measured 2026-11-01; 15k batches at 25 ms per image took ~2 h 50 min).
  Documents and images also **stop after 4 minutes in one VM session** and
  resume on a restarted VM: after a drop, Windows in the VM would not shut
  down cleanly (twice), and once the next boot hung at "Starting Windows".
- **This host has hard-powered-off under heavy disk I/O** (Kernel-Power 41,
  and one bugcheck 0x9F with VirtualBox 7.1.4). Stable since VirtualBox
  7.2.18 + batching + throttled conversion (3-4 workers). After any crash,
  verify files (a crash once zeroed already-written data).
- A boot occasionally never brings the database up; the pipeline tries a
  second cold boot. A saved VM state from another VirtualBox version is
  discarded for a cold boot.
- `parse_sids.unlzw()` once garbled LZW bodies after the first code-width
  change; replaced by a port of Mark Adler's `unlzw.c`.

## 7. What is not available

- **EPC data cards** (`DAG_DCPC`, `MBNA_DCNA`, `MBOE_DCCAR`, … on port 2034)
  exist in the install but refuse both accounts the rest uses (`tbadmin`,
  `tbuser`). That is a deliberate lock — use the EPC program in the VM to look
  a VIN up.
- **EPC option-code (SA code) descriptions.** Surveyed 2026-10-08 with
  `extractor/probe_epc_survey.js` (SELECT only, `tbuser`). `SPECIALFILES`
  opens, but 11 of its 12 base tables refuse SELECT; the granted `_V` views
  hold only data-card routing metadata — WMI → market/plant (`WHC_MASTER`),
  WMI + Baumuster → short model description (`DC_MODEL_DESC`, 43,447 rows),
  VIN prefix → model stub (`VIN_MDL`, 74 rows), which data-card DB serves
  which region (`DCDBINFO`), aggregate filters, languages, version
  ("DW 01/2017"). No code texts. `PWORD` was deliberately not read. The
  catalogue databases that would hold code texts (`ALLTEXT`, `COMM`,
  `SA_IMAGES`, `BM_IMAGES`) refuse `tbuser` at login, like the data cards.
- **No per-variant model years** in WIS. `ASRA_FahrzeugII.ProduktionVon/Bis`
  are chassis-number ranges, `W_Baumusterzeile` dates are documentation
  validity. US model years come from NHTSA vPIC, queried once by
  `tools/build_us_years.py` and committed as
  `autorundown/importers/data/mercedes_us_years.csv` (the site never calls
  NHTSA). Generation years are a curated table.

## 8. Output (the corpus AutoRundown imports)

`wis_extract/`: `records.jsonl` (one document per line; schema 1.3:
`model_series`, `engine_series`, `transmission_series`,
`applicability_summary`, `image_files`, `figure_text`, body text with
`[diagram: …]` markers after the step each figure illustrates),
`manifest.json`, `models.jsonl`, `variants.jsonl`, `vin_us.json`,
`vin2fin.json`, and `images/<cluster>/<graphik_id>.<ext>` + `catalog.tsv`.
English set: 272,607 documents, 272,181 graphics (~11 GB).
