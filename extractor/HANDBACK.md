# Handback to the extractor session

## 0a. (2026-10-01) RESOLVED — schema 1.2 carries the real applicability

The table was right; the join was wrong. `W_Dok2Reihe.ReihenID` is an
applicability *combination*, resolved to model lines through `WS1-3` /
`WC1-8` (columns `BMZeilenNr, ReihenID`), and the table a row comes from is
its role: 1 = vehicle, 2 = engine, 3 = transmission, 4-8 = other aggregates.
Schema 1.1 joined ReihenID straight to `BMZeilenNr` — different keys whose
ranges overlap.

Validated on the English records (re-run independently on the AutoRundown
side): the vehicle role agrees with the documents' own MODEL lines on
**99.94%** of 108,853; the engine role with ENGINE lines on 99.1%.

New files: `pull_applic2.js` (SELECT-only, ~5 min) ->
`autorundown_out/wis_applic2/` (tables + `doc_applicability.tsv`), and
`apply_applic.py` (offline), which rewrites `records.jsonl` as **schema 1.2**:
`model_series` / `baumuster` from the vehicle role, new `engine_series`,
`transmission_series`, `applicability_summary` (W_Kurzgueltigkeit text), and
years nulled. The 1.1 file is kept as `records.v1.1.jsonl`. Result: 223,968
records with a vehicle series, 76,667 with an engine, 20,233 with a
transmission.

Still to fold in properly: `parse.py` should do this join itself, so a fresh
pull produces 1.2 directly, and `pull_applic.js` should pull WS/WC.

## 0. (2026-09-30) Schema 1.1 model links are wrong — W_Dok2Reihe is not applicability

**Read this first; it supersedes section 4's last paragraph.** The
`model_series` / `baumuster` / `model_year_from` / `model_year_to` fields on
every record come from `W_Dok2Reihe` -> `W_Baumusterzeile`, and that join does
not mean "this document applies to this model".

Evidence, measured on the real `records.jsonl` (272,607 records):

- 97,891 documents carry both a structured link *and* a `MODEL` line in their
  own text. On **98.4%** of them the two share no series at all. Example:
  "Overview of innovations ... Sprinter", text `MODEL 901-904`, linked to the
  447 V-Class.
- `W_Dok2Reihe.ReihenID` also keys `Dokumente1..10`, `WS1..3`, `WC1..9` — it
  is a position in the WIS navigation tree, not a model reference. The join
  to `W_Baumusterzeile` on that id produces plausible-looking nonsense.

Impact: filed by those links, each car on the website showed mostly other
cars' documents — the user reported "no documents" for repair procedures and
torque sequences. That is also a wrong-vehicle safety problem.

What AutoRundown does now: the importer measures agreement between
`model_series` and the text on every corpus, and below 50% ignores the field
entirely and files each document by its own `MODEL` / `ENGINE` lines (it
prints `! structured model links IGNORED`). So nothing breaks today, but:

1. **Please don't build further on schema 1.1's model fields.** The
   per-record years from the same join are equally meaningless.
2. **The real applicability table is still to be found.** Next candidate:
   `W_Kurzgueltigkeit` (797k rows), reached via each document's
   `kurzgueltigkeit_id`. Validate any candidate the same way: on documents
   that state their model in text, the candidate should agree with the text
   on the large majority. Once it does, emitting it in `model_series` needs
   no AutoRundown change — the importer will pass its own check and use it.
3. **Images are the other gap.** Tightening sequences and wiring diagrams are
   graphics (the adapter is `images:false`); 24,972 figure-only records are
   dropped because their bytes are still in the VM. An image pass from the
   `ID2Blob_<cluster>` tables, with DXF/TIFF converted to PNG/SVG, would make
   them searchable and citable.

---

## Earlier (2026-09-28)

Two things: the run did not finish, and there is one real data bug worth fixing
in the applicability rollup.

---

## 1. The run stopped. No documents were extracted.

Evidence, from the AutoRundown side:

- VM `Mercedes_Ultimate_207` is **powered off**; `VMStateChangeTime` is
  `2026-09-28T23:48:20Z` (17:48 local).
- The last file written anywhere in `Mercedes Documents` was 17:48.
- There is **no** `bodies.pack`, `watermark.txt`, `metadata.tsv`,
  `records.jsonl` or `manifest.json` in any location on this machine.
  `autorundown_out/phase4_proof/parsed/` was created and is empty.
- `pull.js` checkpoints every 500 documents, so a run in progress would be
  leaving a growing `bodies.pack` and a `watermark.txt`. Neither exists.

**The applicability pass did succeed** — `valid/pull_applic.log` shows it
completing in 4 seconds:

```
[17:45:05] W_Baumusterzeile rows = 33085
[17:45:09] W_Dok2Reihe rows = 432843
[17:45:09] APPLIC pull done
```

So what is outstanding is the main document pull (the ~10-15 minute,
272,607-document stage) and the parse that turns it into `records.jsonl`.
The output currently lives only in a session scratchpad:
`.../scratchpad/valid/{baumusterzeilen.tsv, dok2reihe.tsv, models.jsonl}`.
If those are not copied into the real `-OutDir`, re-running will redo them.

---

## 2. Bug: one implausible production date poisons a whole series

`models.jsonl` has:

```json
{"reihe": "124", "year_from": "1194-03-01", ...}
```

`1194` is a transposed `1984`. It is the **only** bad row out of all 33,085
(`BMReihe=124`, `BMProdVon=1194-03-01`), but because the catalogue rolls up
`year_from` as a plain `min()` across the series, that one row becomes the
whole W124's start date.

Impact downstream: a year picker built from this ran from **1194 to 2026** —
833 entries — and the W124 matched a search for a 1500 model year.

**Suggested fix in `parse.py`'s series rollup:** ignore dates outside a
plausible window (say 1900-2035) when computing `svon`/`sbis`. For series 124
the next-lowest value is `1983-10-01`, so the rollup would produce a correct
span instead of none.

AutoRundown now rejects out-of-range stamps defensively rather than correcting
them (1194 is obviously 1984, but guessing at production data is not the
importer's job). The cost of that is real: **W124 currently gets no year range
at all**, so the E-Class drops out of year filtering entirely until the rollup
is fixed.

---

## 2b. The production dates are not market years (bigger than the 124 bug)

Checked the whole catalogue against known model histories:

| series | catalogue says | the car actually is |
|---|---|---|
| 140 | 1983-2000 | S-Class W140, 1991-1998 |
| 202 | 1985-2019 | C-Class W202, 1993-2000 |
| 117 | 1985-2016 | CLA C117, 2013-2019 |
| 201 | 1983-2000 | 190E W201, 1982-1993 |

`year_from`/`year_to` are a plain min/max over every `W_Baumusterzeile` row of
a Baureihe, and those rows carry documentation-validity windows rather than
production dates. The `*/*/*` wildcard rows are the worst offenders — for 140
they are stamped 1983-10, 1985-09 and 1989-07 while every specific-Baumuster
row starts 1991-04.

**We are not applying a heuristic to this.** Taking the minimum over only the
specific-Baumuster rows was tested: it fixes 140 (-> 1991-04) and 202
(-> 1993-07), but makes 201 (-> 1985, should be 1982) and 164 (-> 2007, should
be 2005) *worse*, and does nothing for 117. There is no safe rule here.

This is tolerable as-is because the error is almost always *too wide*, which
shows extra cars rather than hiding the right one. Worth knowing, not worth
guessing at. If accurate years matter, `ASRA_Fahrzeug.ProduktionVon`/`Bis` is
per vehicle rather than per Baumusterzeile row and should be far closer — which
is now a second reason to pull that table (see section 4).

---

## 3. Two checks worth running on the real output when it lands

- `manifest.applicability.records_with_models` should be ~268,000 of 272,607.
  **If it is 0 the applicability pull was skipped**, and every car will look
  empty on the website — the exact symptom schema 1.1 was added to fix.
- `model_series_count`: the real catalogue has **406** series, not the 190 in
  the IMPORT_SPEC example. 239 of those 406 have no production dates at all,
  so they will show in the picker without a year. Worth a sanity check that
  this is expected and not a join dropping rows.

---

## 4. Request: model names

`W_Baumusterzeile` carries no marketing or sales name, so the vehicle picker
can only say `MODEL 201`, never `190E` or `C-Class`. If a later pass is
possible, `ASRA_Fahrzeug` (92,787 rows, same catalog) has:

- `Verkaufsbezeichnung` — sales designation ("C 220 CDI", "A 200")
- `Weltherstellercode` + `TypBM` — VIN characters 1-9
- `MotorBM` — engine Baumuster

That is the only remaining reason to touch that table. (The claim that
applicability was solved by the `W_Dok2Reihe` route was wrong — see section 0.)

---

## Confirmed working against the real catalogue

The 406-series `models.jsonl` was read end to end by the AutoRundown importer:
all 406 series codes are well-formed 3-digit values, 167 have usable
production spans, and the W201 resolves to `1983-2000` with `201.034` present
— the 1987 190E 2.3-16 case from IMPORT_SPEC section 3 checks out.
