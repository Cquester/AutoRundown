# Vehicle selection: year / make / model, variant, VIN

<!-- Moved out of CLAUDE.md on 2026-10-12 to keep that file short; the text
is unchanged apart from file paths (tests/, tools/). "Above" / "below" and
section names may refer to CLAUDE.md or another file in docs/notes/. -->

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
and none chosen, the scope note lists the car's own engine groups (from
`engineGroups`, e.g. "M272 · V6 petrol, M273 · V8 petrol" -- it used to say
"V6, V8, diesel" for every car) and "Pick your engine" is a button that opens
the Browse row on the Variant menu, in accent colour. On a phone the scope note
and Options live in the car sheet (opened from the car line at the top, which
itself says "N engines — tap to pick yours" until an engine is chosen). Engine labels come from `importers/data/mercedes_engines.csv`
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

**EPC holds no reachable option-code (SA code) descriptions — checked
2026-10-08, do not look again in EPC. WIS has them** (found 2026-10-11:
`SACode`, 23,105 English rows, and `SANummer`; see "WIS reference tables").
The EPC side, for the record: `SPECIALFILES` (1.2 MB; "42 tables" counted
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
(The code list now comes from WIS instead; the data card itself is still
EPC-only and locked.) Survey output:
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
`tests/test_ui_picks.py` now checks that the sent index names the shown car, for
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
- **Add a row when a new model appears.** `tests/test_records.py` checks the file is
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
variant)` returns the selected vehicle, then its engine buckets, then any
joined parts catalogue buckets, then the general pool (`general_doc_id`). **The core property is intact** — each query
is still filtered to one doc_id before ranking; the scope only decides which
doc_ids are queried, so a wrong-model spec is still never a candidate.

The general pool is 28,829 pages since schema 1.2 and it is narrowed by
vehicle type (see the `records` importer section), because not all of it is
universal.

**Duplicates are removed by `page_no`, not `page_uid`.** A document naming
both a model and an engine is filed in both buckets, so a scope holding both
would show it twice. `page_no` is unique within a scope: the one source
joined into another's scope -- the EPC parts catalogue, see below --
prefixes its page numbers (`P123`), so they never equal a WIS DokID.

**A parts catalogue joins the manual's car (2026-10-14).**
`index.vehicles()` runs `link_sources()`: a manual vehicle gets
`joined_ids` (the `doc_type "parts"` vehicles of the same maker and
`model_code`, "210" or an engine bucket "E112") and `engine_joins`
(engine family -> parts engine buckets, for every engine its variants
use, whether or not WIS has a bucket for that engine). `scope_ids` adds
them after the car's own engine buckets (named in `Scope.parts`); `search`
ranks them as a separate group appended after the manual's results, so
the manual's top N is unchanged (`parts_slots`, `PARTS_CAP`). A variant
or engine choice narrows the parts engine buckets exactly as it narrows
the WIS ones. Parts chunks live in their own full-text table (`parts_fts`)
so their BM25 statistics never shift the manual's rankings. A
joined parts vehicle carries `joined_into` and is not listed by
`/api/vehicles` (lookups by doc_id still find it); one with no manual
counterpart is listed on its own. `/api/image` falls back to a joined
corpus's `images/` for a name the car's own corpus lacks, because the UI
asks for every image through the selected car. A manual is never joined
into another manual. `tests/test_epc_join.py`.

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
