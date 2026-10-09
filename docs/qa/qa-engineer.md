# QA report: functional (qa-engineer)

Run 2026-10-09 in an isolated cloud container against `python dev.py` sample data.

**Setup.**
- Everything that changed settings, accounts, imports or removals ran on a second copy of the server on :8758, with a copy of `.dev/index.db` and its own `AUTORUNDOWN_HOME`. It has since been stopped.
- `/api/ask` was blocked in every browser session (requests counted, then aborted).
- No tracked file was changed.
- Scripts were under `.dev/qa/engineer/` (`http_*.py`, `pw_*.py`), with screenshots in `shots/` (not committed).

**Free suites.**
- `test_access.py`, `test_records.py`, `test_lemon_import.py` and `test_lemon_catalogue.py`: 0 failures.
- `test_remove_manuals.py`: 1 failure (the first finding below).
- `test_ui_picks.py` and `eval_wis.py` were not run, because they need the real index.

**Limit of the sample data.** The sample WIS corpus uses the small invented catalogue, because the real `models.jsonl` isn't here. No WIS vehicle has variants, engines or VIN tables, and there are no `images/` or `bodies.pack`. So the variant and engine picker and US VIN decoding were not exercised.

---

### [HIGH] Suite failure: `test_remove_manuals.py` assumes the real WIS catalogue
- Where: `test_remove_manuals.py:89`.
- What: `check(m["wis"]["entries"] > 100 ...)`. Without the real catalogue, the sample has 16 series (17 entries), so the check fails on any clean checkout, in a cloud session, and in CI.
- Evidence: `FAIL WIS grouped: 17 entries`. All removal checks after it pass.
- Fix: assert that the number equals the count of WIS vehicles in the index (or `> 0`).

### [HIGH] Pressing Enter in the question box sends one paid question per press
- Where: `autorundown/web/index.html:1448-1450` (the Enter handler), `ask()` at 1315-1331.
- What: `ask()` only disables the button. The Enter handler calls `ask()` without checking whether a question is already running. With a key set, every extra Enter press is a separate paid question.
- Evidence: with `/api/ask` made to hang, Enter ×5 gave 5 POSTs in flight while `#askBtn` was disabled, and Enter ×10 gave 10 POSTs (`pw_askspam.py`, `pw_chaos2.py`). This is spend nobody intended, which borders on CRITICAL.
- Fix: keep an `asking` flag in `ask()` and return while it is set, or check `#askBtn.disabled` in the Enter handler.

### [HIGH] A search result clicked after changing car opens the new car's page with the same number
- Where: `index.html:1348` (`onclick="showPage('${page_no}')"`) and `showPage()` at 1402, which uses the current `docId()`. The same pattern is in `specHtml` (1209), `srcBtn` (1217), `figureHtml` (1227) and `imgUrl` (1006).
- What: results and answers stay on screen when the car changes, and they carry only a page number.
- Evidence (`pw_stale.py`):
  - Pick the F-150 and search "oil". Then pick the Camry; the F-150 results are still listed. Clicking one requests `/api/page/2008-toyota-camry-l4-2-4l-2az-fe/2`, the Camry's page.
  - On WIS: W201 results, then pick the W176 and click; the result is `404 page not found`.
- Fix:
  - Carry the doc_id in each hit and citation (`showPage(page, doc)`).
  - Clear `#out` and `#srcout` when the car changes.

### [HIGH] Car box shows one car while searches go to another
- Where: `index.html:637-643` (`loadVehicles` keeps the in-memory `carPick` label), 1663-1677 (the VIN dialog's Find).
- What, in two ways:
  - **Removing the selected manual:** after removal the box still says "1987 Mercedes Benz 190E…", but `#vehicle` is now the Camry and a search returns Camry pages. `carPick` is never cleared when its doc_id disappears.
  - **Browse → VIN → Find:** `#vehicle` changes to the W201, but `#carInput` still shows the F-150. The older `carPick` (with its engine and year) is still sent with the new car.
- Evidence:
  - `pw_remove.py`: `car after removal: '1987 Mercedes Benz 190E L4-2.3L (102.985)' 2008-toyota-camry-l4-2-4l-2az-fe`.
  - `pw_car.py`: `vehicle: mercedes-wis-virtualbox-en-201 | carInput: 2015 Ford F-150 2WD V6-3.5L Turbo`.
- Fix:
  - In `loadVehicles`, set `carPick = null` unless its doc still exists, and always re-set `#carInput`.
  - In `vinFind`, call `clearCarPick()` (or apply the VIN's car) so the box follows the pick.

### [HIGH] A page file name in an imported archive can run script on the admin PC
- Where: `esc()` at `index.html:517` doesn't escape `'`, and it is used inside `onclick="showPage('${esc(p)}')"` (1348, 1209, 1217, 1227, 1194, 1297, 1367-1368, 1435). Page numbers come from `importers/lemon.py:169` (`page_no=path.stem`).
- What: a page file named `3');window.__xss=document.domain;('.html` runs script when its search hit is clicked. On the admin PC that script can POST to `/api/settings`, `/api/import`, `/api/admin/users` or `/api/shutdown`.
- Evidence: the rendered handler was `showPage('3');window.__xss=document.domain;('')`, and `window.__xss = localhost` (`pw_xss.py`). The crafted vehicle was removed afterwards.
- Fix: replace the inline handlers with `data-page` attributes and one delegated listener, or at least escape `'` in `esc()`. Optionally reject page numbers that aren't `[A-Za-z0-9._-]+` at import.

### [HIGH] Saving an empty Index path breaks the whole site, and it survives a reload
- Where: `server.py:580-599` (no validation), `index.py:149-152` (`connect()` creates missing folders and an empty DB).
- What:
  - **Empty path:** the server connects to `.`. Every request except `/api/me` then returns 500, and the empty value is saved.
  - **Typo'd path:** the server silently creates a new empty database and switches to it. The UI then shows the first-start import dialog, as if every manual were gone.
- Evidence:
  - After clearing the field and saving, `/api/vehicles` and `/api/stats` returned 500, and after a reload the page showed "Internal Server Error" (`pw_settings.py`, `shots/31_empty_index_path.png`).
  - A typo'd path returned `/api/vehicles = []` and created a 73 KB file.
- Fix: refuse an `index_path` that is empty or isn't an existing file with a `vehicles` table, and answer 400 with a message.

### [MEDIUM] Saving any setting overwrites the saved Index path
- Where: `index.html:1487` (Save always sends `index_path`), `server.py:567` (GET returns the path in use), `server.py:597`.
- What: with `serve.py --index X`, changing only the model writes `X` into `config.json`. The comment at `server.py:594-596` says this must not happen.
- Evidence: config pointed at `some-other.db`, the server was serving `index.db`. After changing only the model and saving, the config held `index.db` (`pw_settings.py`).
- Fix: send `index_path` only when the field was edited, or show the saved value and the in-use value separately.

### [MEDIUM] Typed car names containing "mercedes" or "benz" are mistaken for VINs
- Where: `index.html:901-902` (`looksLikeVin`).
- What: spaces are removed before the VIN check, so any 11-17 letters and digits without I, O or Q count as a VIN. "mercedes 190", "mercedes w201", "mercedes benz w212" and "2010 mercedes e350" fail with a VIN error.
- Evidence: `looksLikeVin('mercedes 190') === true`. The box showed "No Mercedes model uses the code MER + "C" at position 4…", while `carMatches` finds the W201 (`pw_car.py`, `pw_car2.py`).
- Fix: test the trimmed raw text as one token, or try `carMatches` first and treat the text as a VIN only when there are no matches.

### [MEDIUM] A non-Mercedes VIN picks a Mercedes car
- Where: `index.html:1622-1631` (the FIN branch never checks the manufacturer code).
- What: any VIN whose positions 4-6 are digits is read as a Mercedes FIN. **This contradicts CLAUDE.md**, which says "A non-Mercedes VIN fails with a clear message."
- Evidence: `ZFA20100000123456` (a Fiat) resolved to "Mercedes-Benz W201 190 · from the VIN" in both the box and the dialog.
- Fix: require a Mercedes manufacturer code (`WDB`, `WDD`, `WDC`, `WDF`, `W1K`, `W1N`, `W1V`, `4JG`, `55S` …, or the prefixes in `vin_us.json` / `vin2fin`) before taking the FIN branch.

### [MEDIUM] Mercedes VM tab shows a JavaScript error instead of the real message
- Where: `importer_jobs.py:92` returns `error` as a list, and `index.html:1984` passes it to `esc()`.
- Evidence: the tab shows `(s ?? "").replace is not a function`. The real message is "STOP: VBoxManage not found…" (`shots/41_import_vm.png`).
- Fix: return a string (`"\n".join(...)`), or use `String(r.error)` in the UI.

### [MEDIUM] A wrong folder path silently becomes the home folder, with Import enabled
- Where: `server.py:452-455` (`if not p.is_dir(): p = Path.home()`), `index.html:1694`.
- What: a path that doesn't exist puts the home folder in the field, shows "Detected: Generic folder: 203 HTML / text / PDF files", and enables Import. One click would index the whole home folder. Import was not clicked.
- Evidence: `pw_import.py`, `shots/42_import_nonexistent_path.png`.
- Fix: answer 404 "Folder not found" for an explicit path; fall back to the home folder only for an empty path.

### [MEDIUM] Writing the config file isn't atomic, so other requests fail while settings are saved
- Where: `config.py:42-52` (`write_text`); the middleware reads the config on every request (`server.py:167`).
- What: while one request writes `config.json`, another can read it half-written and fail with `JSONDecodeError`, which returns 500. Ordinary triggers are a double-clicked Save or a model change while phones are using the site.
- Evidence:
  - 400 mixed requests (POST `/api/settings` and GET `/api/me`) returned 289 × 200 and 111 × 500.
  - Triple-clicking Save showed the alert "Settings not saved: Internal Server Error".
- Fix:
  - Write to a temp file and `os.replace()` it, with a lock around the write.
  - Optionally cache the config by modification time.

### [MEDIUM] Slow responses can show results for an older search or page
- Where: `keywordSearch()` (`index.html:1333-1357`) and `showPage()` (1398-1444) have no sequence guard. `impBrowse` and `catRefresh` have one.
- Evidence (`pw_race.py`):
  - The box says "brake", but the list shows oil-change results.
  - Clicking hit 0 and then hit 2 shows hit 0.
- Fix: a per-function counter (`const seq = ++searchSeq; … if (seq !== searchSeq) return;`).

### [MEDIUM] Mechanic spend limits: a negative value blocks the account, a huge value silently means no limit
- Where: `server.py:66-74`, `auth.py:136-163`, `index.html:1570`, 1552-1553.
- What:
  - **In the UI:**
    - Adding a mechanic with a limit of -5 stores -5.0, and that mechanic then gets 402 on every question.
    - `1e999` becomes `null`, i.e. no limit.
    - "Limit" with `1e999` silently changes nothing.
    - "Limit" with -5 gives the misleading error "Not a number: -5".
  - **Raw POSTs:** `"NaN"` is stored as no limit and `"Infinity"` as `inf`.
  - **Accounts can't be renamed or deleted,** so a typo in a name is permanent and that name stays taken.
- Evidence: `http_users.py`, `pw_users.py`.
- Fix:
  - `confloat(ge=0, allow_inf_nan=False)` on both models.
  - The same check in `uAdd`.
  - A clear message ("must be 0 or more").

### [MEDIUM] When an API call hangs or fails, some screens stay blank or do nothing
- Where: `api()` (`index.html:520-528`, no timeout), the Settings button (1460-1461), `manLoad` (1855).
- Evidence (`pw_chaos.py`, `pw_offline.py`):
  - A hanging `/api/me` or `/api/vehicles` leaves a blank page.
  - A failing `/api/settings` makes the gear button do nothing; the only trace is an uncaught "Failed to fetch" in the console.
  - A failing `/api/import/manuals` leaves the LEMON list silently empty.
  - A failing `/api/models` leaves the model menu empty.
  - An offline search shows only "Failed to fetch".
- Fix:
  - Catch these errors and show them.
  - Add a 20 s `AbortController` timeout in `api()`.
  - Replace "Failed to fetch" with "Can't reach AutoRundown — is the PC on?".

### [MEDIUM] On a phone, an answer with diagrams is covered by the diagrams panel
- Where: `index.html:1308-1312` (`openRight()` whenever `d.diagrams` is not empty).
- What: at 900px or narrower the fixed right panel covers the Answer, Procedure and Specs. The phone's Back button then leaves the app.
- Evidence: `shots/90_answer_light_375.png`. In `pw_phone.py`, Back went to `about:blank`.
- Fix: on phones, don't open the panel automatically, and push a history state when it opens.

### [MEDIUM] Short words in the car box match almost every car
- Where: `carMatches()` (`index.html:854-866`) matches each word as a substring.
- What: "c class" lists the G-, E-, B- and A-Class first, and the C-Class only 7th and 10th. "s class" behaves the same.
- Evidence: `pw_car2.py`.
- Fix: match one-letter words only as whole tokens, or rank exact model and class names first.

### [LOW] Search words with accented letters are cut short
- Where: `search.py:20` (`TOKEN_RE = [A-Za-z0-9]…`).
- What: an accented letter splits the word, so only the part after it is searched.
- Evidence: in an FTS test with the same tokenizer:
  - "Ölwanne" becomes `"lwanne"`, with 0 hits.
  - "Zündkerze" becomes `"ndkerze"`, with 0 hits.
- Fix: use a Unicode-aware token regex, or fold accents with NFKD before matching.

### [LOW] File paths on the PC are visible to phones
- Where:
  - `/api/vehicles` and `/api/page` (`file_path`)
  - the `/api/image` 404 message (`server.py:387`)
  - the `/api/original` 404 message
- What: **this contradicts CLAUDE.md**, which says "/api/stats hides this PC's paths from others".
- Evidence: a request from a phone address (`Host: 192.168.1.50`) got `file_path: /home/user/AutoRundown/sample_corpus/wis_en/records.jsonl`.
- Fix: drop `file_path` for non-admins and use generic 404 messages.

### [LOW] Small UI and validation issues
- **Any model name is accepted.** `POST /api/settings {"model":"gpt-4"}` succeeds (`server.py:588`); Settings then shows a blank model, and saving writes `""`. Check against `query.MODELS`.
- **A 100,000-character `archive_dir` makes `/api/browse` return 500** (`server.py:453`).
- **A 10-character FIN gives "No match" in the car box** but works in the VIN dialog, because of the 11-character minimum.
- **An empty search gives no feedback** (`index.html:1335`).
- **With no car chosen (empty index), a search shows the raw text "unknown vehicle".**
- **The phone Close button sits at a fixed `top:70px` and covers content** (`index.html:280`).
- **Double clicks:**
  - Two clicks on "Remove selected" give two confirm prompts (the second gets 409).
  - Three clicks on "Sign-in code" issue three codes, and a reply arriving out of order could show a dead code.
- **A `#code=` link opened in a tab already showing the sign-in dialog doesn't fill in the code.**
- **The sign-in throttle locks out a whole address for 15 minutes,** including everyone behind a shared router. This looks deliberate in `auth.py`.

---

**Checked and found fine:**
- **Search input:** FTS5 syntax, SQL-looking text, NUL bytes, 5,000-character input and emoji are neutralised by `build_match` with no 500s. Bad `limit`, `variant` and `doc_id` values get 404 or 422.
- **`/api/image`:** path traversal is refused.
- **Admin checks:**
  - They hold for Host values `localhost.evil.com`, `127.1`, `0x7f000001` and `localhost.`.
  - Cross-site POSTs and `Origin: null` get 403.
  - Phones get 403 on the admin endpoints.
- **Sign-in codes:**
  - Lowercase and spaced codes work.
  - Reused, reissued and expired codes are refused.
  - Disabling a mechanic signs them out at once.
  - Throttling gives 429 after 10 wrong codes.
- **Escaping:** HTML in names and answer fields is escaped, including through `renderAnswer`.
- **Import jobs:** only one runs at a time (409), and switching tabs during a job is fine.
- **LEMON catalogue:** ticks and indeterminate states are right, the screen and server agreed in 8/8 rapid-tick trials, and Clear picks works.
- **Removal:** a removed manual is gone from vehicles and search, and its files on disk are kept.
- **Page view:** every result link opened, and the existing diagrams load with 200.
- **Car box:**
  - Pressing Enter repeatedly in the car box is harmless.
  - Browse then reload restores the pick.
  - Years outside a car's range don't match.
  - The documented "190 e 2.3 16" two-digit-year quirk reproduces.
- **Rendering and start-up:**
  - An injected answer renders correctly at 1280 and 375 px.
  - An empty index opens the import dialog.
  - There are no console errors on normal screens.
