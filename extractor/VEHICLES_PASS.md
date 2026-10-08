# Extraction pass 2 — vehicles (year / make / model, and VIN decode)

Goal: let AutoRundown offer a **year / make / model** picker instead of one
"All models (en)" entry, and later resolve a pasted VIN to the right vehicle.

None of that is possible from `records.jsonl` as it stands. A record carries
the 3-digit model series (in `applicability_notes`), an opaque
`kurzgueltigkeit_id`, and a `fzg_art_bits` mask. No year, no vehicle name.

The data is in the VM. This pass fetches it. It is **small** — under 200k rows
total, no blobs — and it reads the same database over the same connection as
`pull.js` (verified: `W_SprachDokumente` and `ASRA_Fahrzeug` are in the same
catalog dump, `autorundown_out/schema/wis.systable.tsv`).

---

## 1. The tables

All column names and row counts below are read from the schema dump already in
`autorundown_out/schema/`, not guessed.

### `ASRA_Fahrzeug` — 92,787 rows — the important one

| column | type | use |
|---|---|---|
| `Weltherstellercode` | char(3) | WMI = **VIN positions 1-3** (WDB, WDC, WDD…) |
| `TypBM` | char(6) | Baumuster = **VIN positions 4-9**; first 3 chars are the model series |
| `ProduktionVon` | char(6) | production from, `YYYYMM` → **the year range** |
| `ProduktionBis` | char(6) | production to, `YYYYMM` |
| `Verkaufsbezeichnung` | char(18) | sales designation — "C 220 CDI", "A 200" — **the model name** |
| `MotorBM` | char(6) | engine Baumuster, e.g. `651911` → engine 651.911 |
| `Lenkung` | char(1) | steering (LHD/RHD) |
| `Werk` | char(1) | plant |
| `SparteAbk`, `FamilieAbk`, `TypkennzahlAbk`, `ProduktionslandAbk` | char(2) | division / family / type-number / country, join keys for `ASRA_Typkennzahl` |

`Weltherstellercode` + `TypBM` is VIN characters 1-9, so this one table is both
the vehicle list and the VIN lookup.

### Supporting tables

| table | rows | columns of interest | why |
|---|---|---|---|
| `FzgArt` | 290 | `SprachCode`, `FzgArtCode`, `FzgArtAbk`, `FzgArtName` | names for `fzg_art_indices` ("Passenger car", "Van") |
| `W_Kurzgueltigkeit` | 796,828 | `KurzgueltigkeitID`, `Kurzgueltigkeit` char(*) | resolves each record's `kurzgueltigkeit_id` to a validity string |
| `W_Baumusterzeile` | 33,085 | `BMReihe` char(3), `BM4/5/6`, `BMProdVon`, `BMProdBis` (datetime) | series-level production windows; cross-check for `ASRA_Fahrzeug` |
| `ASRA_Typkennzahl` | 91,320 | `SprachAbk`, `TypkennzahlName` | body-style names (Saloon, Estate). Optional |

---

## 2. Do this first: sample, then decide

**Dump 20 rows of each table and report what they look like before writing any
parser.** Three things are genuinely unknown and change the design:

1. **What `Kurzgueltigkeit` strings actually contain.** If they name Baumuster
   ranges, documents can be tied to vehicles far more precisely than the
   3-digit series we parse out of prose today. If they are opaque codes, skip
   the table entirely. 797k rows is the largest part of this pass — don't pull
   it until a sample justifies it.
2. **How `fzg_art_indices` maps to `FzgArtCode`.** `fzg_art_bits` is a 32-char
   mask and we take set-bit positions; `FzgArtCode` is a tinyint. Whether bit
   *i* means `FzgArtCode` *i* or *i+1* is an assumption — verify it against a
   document whose subject makes the vehicle type obvious (a Sprinter document
   should come back "Van", not "Passenger car") and say which way it went.
3. **Whether `ProduktionBis` is empty or a sentinel for in-production models.**
   Report the actual value.

Report those three before emitting anything.

---

## 3. What to emit

A new sidecar in the same corpus directory. **Do not change the shape of
`records.jsonl`** unless step 2 finds that `Kurzgueltigkeit` is worth inlining.

```
<corpus dir>/manifest.json      existing, schema_version -> "1.1"
<corpus dir>/records.jsonl      existing, unchanged
<corpus dir>/vehicles.jsonl     NEW — one JSON object per model series
```

### `vehicles.jsonl`

Group `ASRA_Fahrzeug` by model series (`TypBM[0:3]`), one line per series:

```jsonc
{
  "model_series": "204",              // joins to applicability_notes "MODEL 204"
  "make": "Mercedes-Benz",
  "production_from": "2007-01",       // min(ProduktionVon) over the series
  "production_to": "2014-12",         // max(ProduktionBis), null if in production
  "sales_designations": ["C 180", "C 200 CDI", "C 220 CDI", "C 63 AMG"],
  "engines": ["646811", "651911", "271820"],   // distinct MotorBM
  "wmi": ["WDD", "WDB"],              // distinct Weltherstellercode
  "baumuster": ["204.001", "204.008"],// distinct TypBM, dotted
  "vehicle_types": [2],               // FzgArtCode values seen
  "vehicle_type_names": ["Passenger car"],
  "row_count": 1184                   // how many ASRA_Fahrzeug rows rolled up
}
```

`row_count` lets the loader sanity-check the rollup. Keep `baumuster`
full-fidelity; it is what a VIN resolves to.

### Manifest

Add, and bump `schema_version` to `"1.1"`:

```jsonc
{
  "schema_version": "1.1",
  "vehicles_file": "vehicles.jsonl",
  "vehicle_count": 213,
  "vehicle_source_rows": 92787
}
```

The bump is **additive and safe**: AutoRundown's importer gates on the *major*
version, so a 1.1 corpus still loads in today's code and simply ignores the new
file. Nothing breaks if this pass is never run.

---

## 4. Acceptance checks

Before calling it done:

- `vehicles.jsonl` line count == `manifest.vehicle_count`, and the summed
  `row_count` == 92,787.
- Every `model_series` is exactly 3 digits.
- Spot-check three series against the model codes already seen in
  `applicability_notes`: **176**, **204**, **901**. Each must exist in
  `vehicles.jsonl` with a plausible production window and sales designations
  whose letter matches the class (A…, C…, Sprinter for 901).
- `production_from` < `production_to` wherever both are present.
- Report how many distinct model series exist. That number is the size of
  AutoRundown's vehicle picker, so it matters.

---

## 5. What AutoRundown will do with it (context, not work for this session)

- `importers/records.py` reads `vehicles.jsonl` in `discover()` and fills the
  `VehicleRecord` fields that already exist — `make`, `model`, `year_range`,
  `engine`. The index schema and the UI label ("year make model") need no
  change. If the file is absent, vehicles keep their bare `MODEL 204` label.
- Documents attach to a series by the 3-digit code, which is the granularity
  the documents actually carry. The picker will therefore be **series-level**
  ("2007-2014 Mercedes-Benz 204 — C 180 … C 63 AMG"), not per sales
  designation: showing one entry per variant would imply a precision the
  document applicability does not have.
- VIN: characters 1-3 and 4-9 look up `wmi` + `baumuster` directly, which gives
  the series, and the series is the thing that gets selected.
- "C-Class" as a label is *not* in the database. It could be derived from the
  common letter prefix of `sales_designations`, but that is a heuristic and
  will be treated as one.
