# QA report: performance (qa-performance)

Run 2026-10-09 in an isolated cloud container.

**Why the dev data wasn't enough.** It is tiny: 18 vehicles, 201 pages, 0.4 MB, no variants. The real index is 3.5 GB with about 531,847 page rows.

**What was built to measure at scale.**
- A real-size vehicles payload: 270 vehicles, 1,597 variants, 2,583 car-box entries, 751 KB.
- A 2.18 GB synthetic index with 530,819 pages and chunks, filed the way the importer files the real buckets.

**What its numbers mean.** The synthetic text is random, and common words are far more frequent than in real manuals ("brake" is in 96% of chunks). So absolute search times are a **worst case**, and every number from the synthetic index is labelled **estimate**. The scaling it shows comes straight from the code.

**How it was run.**
- Every timing was repeated at least 5 times (median / worst).
- Scripts, traces and screenshots were under `.dev/qa/performance/` (not committed). The synthetic DB was deleted afterwards; `make_scaled_index.py` rebuilds it in about 140 s.
- No tracked files were changed, `/api/ask` was blocked in every browser session, and the trial servers on 8791/8792 are stopped.

### [MEDIUM] Keyword search scores every match in the whole index, once per bucket, per AND/OR pass, per synonym
- **Where:**
  - `autorundown/search.py:101-114` (`_run`)
  - `search.py:219-224` (AND, then OR)
  - `search.py:295-298` (one `_search_one` per doc_id)
  - `server.py:278-280` (a synonym runs everything twice)
  - `index.py:99-102` (the FTS table has no doc_id)
- **What:** the query plan is `SCAN chunks_fts VIRTUAL TABLE INDEX 0:M3`, then a rowid lookup into `chunks`, then the `c.doc_id = ?` filter.
  - FTS5 finds and scores every matching chunk in all 531k rows before it drops other vehicles' chunks.
  - The vehicle filter does run before ranking, as CLAUDE.md requires, but the cost scales with the whole index, not with the vehicle.
  - A whole-generation scope repeats this for every bucket: 14 ids here, 12 for a real W212. It then repeats again for the OR pass and for the synonym query.
  - One OR-heavy search made 50 FTS scans (19.9 s, all inside `execute`).
- **Evidence (estimate, synthetic index):**
  - One `_run` costs about 0.65 µs per matching chunk anywhere in the index. "brake" (507k matches) took 340 ms; a rare term (286 matches) took 1 ms.
  - `/api/search`, W212-like car, all variants: 1-word 7.2 / 7.5 s, 6-word 8.3 / 8.5 s, OR-heavy 20.0 / 20.2 s.
  - With `variant=0`: 1.4 s, 1.6 s and 3.1 s.
  - Real word frequencies are lower, so real times will be lower too. CLAUDE.md already records about 0.7 s for a 7-bucket W140 search. The Ask path's `multi_search` multiplies this by its number of planner queries.
  - Searches don't block the event loop: `/api/me` and `/api/page` stayed at 2 and 7 ms during three parallel 20 s searches. But an abandoned search keeps a worker thread and a CPU core busy until it finishes.
- **Fix (both measured on the synthetic index):**
  1. **Make FTS5 narrow to the vehicle.** Add `doc_id` as a column of the FTS table (bm25 weight 0) and match `doc_id : "<id>" AND (<query>)`. No re-ingest is needed: creating the table and running `INSERT … VALUES('rebuild')` took 50 s.
     - Per bucket: "brake" 318 → 33 ms, `"torque" AND "oil"` 406 → 47 ms, OR-heavy 687 → 194 ms.
     - The top 24 results were identical in all four tests.
  2. **One statement per query for the whole scope.** Use `c.doc_id IN (…)` with `ROW_NUMBER() OVER (PARTITION BY doc_id ORDER BY score)` from a subquery; the per-list top N and `_interleave` stay as they are.
     - Whole-generation "brake": 7.2 s → 0.47 s with the current FTS table, and → 0.19 s with fix 1.
     - OR-heavy: 20 s → 0.92 s, and → 0.45 s with fix 1.
  
  Expected gain: about 15–40x on whole-generation searches, and Ask gains in proportion.

### [MEDIUM] No compression and no cache headers: every phone visit re-downloads about 860 KB (estimate)
- **Where:** `server.py:131-132` (no `GZipMiddleware`), `server.py:230-232` (`/` is re-read from disk with no ETag or Cache-Control), `server.py:258-264` (`/api/vehicles`).
- **What:**
  - Only `/api/image` sends an ETag, and no response sends Cache-Control or Content-Encoding.
  - `/` is 105,626 B (32,373 B gzipped, 69% smaller).
  - At real scale `/api/vehicles` is about 751 KB (86.6 KB gzipped).
- **Evidence (Playwright, phone at 4x CPU and Fast 3G, 7 runs; "ready" means the car box is filled):**
  - Dev data: ready 2,510 / 2,518 ms.
  - Real-size payload (estimate): ready 6,880 / 6,949 ms, of which `/api/vehicles` alone takes 4.7 s.
  - Reloads re-transferred the same 857 KB every time and stayed at about 6.8 s.
  - Desktop: ready 314 / 420 ms with the real-size payload.
- **Fix:**
  - **Turn on gzip:** `app.add_middleware(GZipMiddleware, minimum_size=1000)`. With that one line, phone ready went from 6,880 to 2,780 ms.
  - **Use `compresslevel=6`, or cache the compressed body.** Level 9 costs about 60 ms of server CPU per `/api/vehicles`.
  - **Answer repeat visits with 304:** an ETag on `/` (keyed on the HTML file's mtime) and on `/api/vehicles` (keyed on the index mtime and the vehicles row count).
  - **Smaller payload:**
    - send `variants` as real JSON rather than a JSON string (−11%);
    - leave `vin9` out until a VIN is typed (−12%).

### [LOW] Startup requests run in series: one extra round trip on a phone
- **Where:** `web/index.html:2055-2058`, `index.html:619-644`.
- **What:** `/api/vehicles` and `/api/models` wait for `loadMe()` to finish. On Fast 3G, `/api/vehicles` started at 1,780 ms, after `/api/me` ended at 1,765 ms.
- **Fix:** start all three requests together; only rendering needs `me`. Expected gain: about 575 ms, so phone ready would go from about 2.8 s to about 2.2 s with gzip.

### [LOW] Building the vehicle picker blocks a phone's main thread for about 330 ms (estimate)
- **Where:** `index.html:619-635` (`loadVehicles`), `index.html:815-852` (`buildCarIndex`).
- **Evidence:**
  - A phone-load trace has one 325.8 ms task: the JSON parse, the per-vehicle variants parse, `refreshPicker` and `buildCarIndex`.
  - At 4x CPU, `buildCarIndex` took 100 / 134 ms.
  - It runs again after every Settings save and every finished import.
- **Fix:**
  - Build `carIndex` lazily, on the first focus or input of `#carInput` or in `requestIdleCallback`.
  - Compute `yearsIn` once per Baumuster rather than once per year (837).

### [LOW] `get_page` walks every row of the bucket: about 60 ms to open a general-pool page
- **Where:** `search.py:326-329`; the indexes are at `index.py:55-56`.
- **What:**
  - `WHERE doc_id=? AND page_no=?` uses `pages_hash (doc_id, text_hash)` and checks each row's `page_no`.
  - Scopes are tried in order (car, engines, general), so opening a general-pool page, or missing, scans every bucket in the scope.
  - The general pool really is about 28.8k rows, so this estimate is close to the real index.
- **Evidence:** car page 3.8 ms; general-pool page 64 / 68 ms; a miss 59 / 78 ms.
- **Callers:** `/api/page`, `/api/original` and every entry of `/api/connector`.
- **Fix:** `CREATE INDEX IF NOT EXISTS pages_doc_page ON pages(doc_id, page_no)`. It took 1 s to create on 531k rows and brought the lookup under 0.1 ms.

### [LOW] The LEMON catalogue costs 1.6–2.1 s on first open and keeps about 90 MB of server memory
- **Where:** `lemon_catalogue.py:94-96`, via `importers/lemon.py:30` `_slug`.
- **Evidence:**
  - The first catalogue request took 1,575 ms.
  - `load()` took 2,062 ms, of which 1,747 ms is 279,988 `_slug` regex calls.
  - Server memory went from 20 to 109 MB.
- **Fix:** slug only the catalogue entries that match the make and year of indexed `lemon` vehicles, or slug lazily per opened folder. Expected gain: about 1.5 s on first open and 60–80 MB of memory.

### [LOW] Catalogue filter rebuilds the whole tree on every keystroke; each tick sends 7 requests
- **Where:** `index.html:1806` (filter `oninput` → `catRender`), 1746-1779, 1791-1797.
- **Evidence:**
  - With 1,049 rows open, a filter keystroke takes 8–79 ms on desktop and 45–378 ms at 4x CPU.
  - Each tick takes 118–182 ms and sends 7 requests. The POST already returns the parent folder, which the UI ignores.
- **Fix:**
  - Debounce the filter (about 150 ms) and toggle the existing rows instead of rebuilding them.
  - After a pick, use the folder the POST returns and refetch only its ancestors.
  - This is admin-only (desktop), hence LOW.

### Checked and fine
- **Car box:** typing with 2,583 entries takes 0.5 / 6.8 ms per keystroke (2.6 / 13.7 ms at 4x), which is frame-bound. VIN typing is the same.
- **Variant menu**, largest generation: 8.6 / 11.6 ms.
- **Answer rendering:** `renderAnswer()` with a large injected payload (60 steps, 150 specs, 20 figures, 30 diagrams) takes 21 / 35 ms.
- **Opening a page:** 9 / 13 ms on dev data. An injected 94 KB WIS page takes 92 / 128 ms, mostly DOM and layout; the real page-size distribution is unknown (unverified).
- **Keyword search round trip on dev data:** 12 / 14 ms.
- **Settings and Import dialogs:** Settings opens in 36 / 51 ms, and the Import dialog in 128 ms with the catalogue already loaded.
- **Server endpoints on dev data:** all GETs 1.2–6.4 ms median. `/api/original` was not measured (no `bodies.pack` in the sample).
- **Per-request overhead:** under 4 ms at real size.
- **Memory:** no growth across 200 searches.
- **Not performance:** `HEAD /` returns 405.
