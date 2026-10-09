# AutoRundown QA review: consolidated report

2026-10-09, branch `claude/qa-agents`. This merges the four agent reports in this folder:

- [qa-security.md](qa-security.md)
- [qa-engineer.md](qa-engineer.md) (functional)
- [qa-ui-designer.md](qa-ui-designer.md)
- [qa-performance.md](qa-performance.md)

Each agent report has the full evidence (commands, measurements, screenshot names). This file merges duplicates and orders everything by severity.

**How it was run.**
- **Environment:** an isolated cloud container. It was checked first: no `~/.autorundown`, no "Mercedes Documents" folder, no `ANTHROPIC_API_KEY`.
- **Site under test:** `python dev.py --no-browser` with invented sample data. The real WIS catalogue wasn't available, so the sample uses the small invented one. As a result, no WIS vehicle had variants, engines, VIN tables, images or `bodies.pack`.
- **Safety rules kept:** `/api/ask` was never called and no API key was entered. Destructive tests ran on separate throwaway server copies. No code was changed.
- **Scale estimates:** performance at real scale was estimated on a 2.18 GB synthetic index with real row counts.

**Free test suites:**
- `test_access`, `test_records`, `test_lemon_import` and `test_lemon_catalogue`: pass.
- `test_remove_manuals`: 1 failure (M1).
- `test_ui_picks` and `eval_wis` were not run, because they need the real index.

**Severity scale** (from `.claude/qa/RUNBOOK.md`):
- **Critical:** data loss, spend without consent, auth bypass, secret exposure.
- **High:** a main flow broken, wrong-vehicle data shown, or a likely exploitable issue.
- **Medium:** degraded but workable.
- **Low:** polish.

**[CLAUDE.md]** marks a finding that touches a decision recorded in `CLAUDE.md`.

---

## Fix status (2026-10-09)

All findings below are fixed on `claude/qa-agents`, in four commits.

| Commit | Findings |
|---|---|
| `3e884b5` Critical and High | C1, C2, C3, H1, H2, H3, S1 (= H4), M1, M9 (page and search sequence guards) |
| `283d357` Settings | S2-S9, plus `color-scheme: dark` (part of L10) |
| `1ff88b8` Medium | M2-M8, M10-M12, M14-M17, L1, Original-layout error state |
| `a8caa1f` Low and search speed | M13, L2-L14, `HEAD /` |

**How they were checked:**
- **Free suites:** `test_access` (new checks for every security fix), `test_records`, `test_lemon_import`, `test_lemon_catalogue` and `test_remove_manuals` all pass.
- **Browser checks:** Playwright scripts against `dev.py` cover each UI fix at 1280 px and 375 px, in light and dark. They live in `.dev/qa/fix/` (not committed).
- **Search change (M13):** compared old against new on a 530k-page synthetic index. 42 searches returned identical rows; median 1.37 s → 0.80 s, worst 9.8 s → 1.3 s.

**Differences from the suggested fixes:**
- **M13:** only the one-statement-per-scope change is done. The FTS `doc_id` column was not added, because it rebuilds the FTS table (see CLAUDE.md).
- **L3:** the CA gained EKU serverAuth, and keys are now created 0600. Email and URI names were not excluded from the CA, because that needs testing on real phones first.
- **L4:** the embeddable Python is checked by its Sigstore signature rather than a pinned hash, because the bundled Python version floats with the runner. Package versions are recorded in `installed-packages.txt` inside each MSI rather than pinned. Exact pins taken from Linux would break the Windows wheels-only install (PyYAML 6.0.1 has no Windows wheel for Python 3.13).
- **L9:** a tick now sends 4 requests instead of 7, and the filter is debounced. Rows are still rebuilt rather than toggled.

**Not verified yet:**
- `test_ui_picks.py`, `eval_wis.py` and `eval.py` need the real index.
- The fixes have not been looked at on a real phone.
- iOS behaviour of the new CA extension is untested.

---

## Critical

### C1. A page file name in an imported manual can run code on the PC
*Found by: security, engineer*
- **Where:**
  - `autorundown/web/index.html:517-518`: `esc()` doesn't escape `'`.
  - `esc()` is used inside `onclick="showPage('${esc(x)}')"` at `index.html:1194, 1209, 1217, 1227, 1297, 1348, 1367-1368, 1435`.
  - The page number comes from `autorundown/importers/lemon.py:169` (`page_no=path.stem`, the raw file name).
  - The code runs through `autorundown/importer_jobs.py:67-73` and `:87`, which load and run Python from `extractor_dir`.
- **Evidence:**
  - A LEMON archive with a page file named `9');<js>;('.html` was imported. Its search hit rendered as `showPage('9');window.__xss=document.domain;('')`, and the script ran as the admin origin when clicked.
  - A second payload set `extractor_dir` through `POST /api/settings`, then called `GET /api/vms`. That ran the attacker's `vm_pipeline.py`, which wrote `PWNED.txt` (`executed by pid 1130 argv=['evilext/vm_pipeline.py', '--list-vms']`).
  - LEMON content comes from a third-party torrent, so this is realistic.
- **Fix:**
  - Stop building inline handlers from data: use `data-page="${esc(p)}"` and one delegated click listener.
  - Until then, add `'` → `&#39;` to `esc()`.
  - In the importer, reject or slug page numbers that aren't `[A-Za-z0-9._-]+`.
  - Accept only an `extractor_dir` that is local, absolute and not UNC.

### C2. DNS rebinding lets any website spend the API key while sign-in is off (the default)
*Found by: security*
- **Where:** `autorundown/server.py:148-170` (`who_is_asking`) and `:120-128` (`_origin_ok`).
- **What:** the Host header is checked only to decide who is admin. A page on `attacker.example` that resolves to 127.0.0.1 passes the middleware as a LAN user. Its `Origin` matches its own `Host`, so its POSTs pass too, including `/api/ask` and every search.
- **Evidence:** with `Host: attacker.example:8759`:
  - `/api/search` returned 200 with results.
  - `POST /api/auth/signout` with a matching Origin returned `{"ok":true}`.
  - `/api/ask` was not called, but nothing else gates it.
- **Fix:** allowlist `Host` on every request and answer 421 otherwise. Allowed: loopback names, the PC's own addresses (`tls.local_names()`), `<host>.local` and the mDNS name.

### C3. Pressing Enter in the question box sends one paid question per press
*Found by: engineer (reported as HIGH, "borders on CRITICAL"); also raised by UI. Placed here because the runbook defines spend without consent as Critical.*
- **Where:** `index.html:1448-1450` (the Enter handler calls `ask()` unconditionally) and `ask()` at `1315-1331`, which only disables the button.
- **Evidence:** with `/api/ask` made to hang, pressing Enter 10 times sent 10 POSTs while `#askBtn` was disabled.
- **Fix:**
  - An `asking` flag in `ask()` that returns while a question is running.
  - Related, from the UI review: Enter starts the *paid* action, and the free Keyword search is the secondary button. Consider "Ask AI" with a cost estimate, or making Enter run Keyword search when there is no key. This is a suggestion; no decision about it is recorded.

---

## High

### H1. The car box shows one car while searches and answers use another
*Found by: UI, engineer*
- **Where:**
  - `index.html:903-927` (`#carInput` input/blur handlers)
  - `index.html:637-643` (`loadVehicles` keeps a stale `carPick`)
  - `index.html:1663-1677` (VIN dialog Find)
- **Evidence:** three separate ways to get there.
  - **Typed but not picked:** with the F-150 picked, typing "2018 Mercedes W213 E-Class" and pressing Keyword search returned F-150 pages, while the box still showed the W213.
  - **Selected manual removed:** the box kept "1987 Mercedes Benz 190E…", but `#vehicle` became the Camry.
  - **Browse → VIN → Find:** `#vehicle` became the W201, but the box still showed the F-150, and the old engine and year were still sent.
- **Why it matters:** this is the wrong-vehicle class of bug that CLAUDE.md treats as the core safety property.
- **Fix:**
  - **Typed text that wasn't picked:** on blur or change, restore the applied car's label (or apply the single top match). `ask()` and `keywordSearch()` should refuse to run while the box text differs from the applied car.
  - **A removed manual:** `loadVehicles` clears `carPick` when its doc_id is gone, and always re-sets `#carInput`.
  - **The VIN dialog:** `vinFind` clears or applies the car pick.
  - **Everywhere:** show "Results for: <car>" on keyword results and answers.

### H2. Clicking an old result after changing car opens the new car's page with the same number
*Found by: engineer*
- **Where:** `index.html:1348` and `showPage()` at `1402`, which uses the current `docId()`. The same pattern is in `specHtml` (1209), `srcBtn` (1217), `figureHtml` (1227) and `imgUrl` (1006).
- **Evidence:** search the F-150 for "oil", switch to the Camry, then click the first hit. That loaded `/api/page/2008-toyota-camry-l4-2-4l-2az-fe/2`, a Camry page shown as the F-150 result. On WIS the same steps gave a 404.
- **Fix:** carry the doc_id in every hit and citation (`showPage(page, doc)`), and clear `#out` and `#srcout` when the car changes.

### H3. `/api/image` serves manual files as active same-origin content
*Found by: security*
- **Where:** `server.py:375-398`.
- **What:** an SVG or HTML file in a manual's `images/` folder is served from the app's origin with no CSP or `nosniff`. Every diagram has "Open full size", so clicking it runs that file's script as the admin.
- **Evidence:** a test `evil.svg` was served as `image/svg+xml`; its script read `/api/settings`.
- **Fix:**
  - Send `Content-Security-Policy: default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox` on image responses.
  - Send `X-Content-Type-Options: nosniff`.
  - Serve only an allowlist of image extensions.

### H4. Saving an empty or mistyped Index path breaks the site (see Settings)
*Found by: engineer.* Details are under **Settings** below (S1).

---

## Medium

### Security
- **M2. Admin GET routes answer cross-site requests.** *(security)*
  - **Where:** `server.py:451-502` (`/api/browse`, `/api/vms`, `/api/import/*`). The Origin check covers only non-GET requests.
  - **Evidence:** an `<img src="http://localhost:…/api/vms">` on another loopback origin ran `vm_pipeline.py`. On Windows, `/api/browse?path=\\host\x` could leak an NTLM hash (not tested on Windows).
  - **Fix:**
    - For every `ADMIN_PREFIXES` path and any method, require `Sec-Fetch-Site: same-origin|none` or a custom header (`X-AR: 1`).
    - Refuse UNC paths.
- **M3. Missing security headers, so clickjacking works.** *(security)*
  - **Where:** every response, e.g. `server.py:230-232`.
  - **Evidence:** another origin framed the admin UI, and `/api/me` inside the frame returned `admin: True`.
  - **Fix:** a header middleware sending:
    - `X-Frame-Options: SAMEORIGIN`
    - `Content-Security-Policy: frame-ancestors 'self'`
    - `X-Content-Type-Options: nosniff`
    - `Referrer-Policy: no-referrer`
- **M4. Unbounded request sizes.** *(security)*
  - **Where:** `server.py:266-269` (`limit`, `q`, `engine`), `:35-47` (`AskBody.question`), and every POST body.
  - **Evidence:**
    - A 100 MB body to the public `/api/auth/signin` raised server memory from 98 MB to 392 MB.
    - 7,000 search terms took 0.95 s on the tiny index.
    - A huge Ask question is paid tokens.
  - **Fix:**
    - Clamp `limit` to 1-50.
    - Cap `q` at 500 characters and 32 terms.
    - Set `question` `max_length=2000`.
    - Reject request bodies over 64 KB in middleware.

### Functional
- **M1. `test_remove_manuals.py` fails on any checkout without the real WIS catalogue.** *(engineer; reported HIGH, placed here because no user-facing flow is affected)*
  - **Where:** `test_remove_manuals.py:89` checks `entries > 100`, but the sample has 17.
  - **Fix:** compare with the number of WIS vehicles in the index.
- **M5. Car names containing "mercedes" or "benz" are mistaken for VINs.** *(engineer)*
  - **Where:** `index.html:901-902` (`looksLikeVin` strips spaces first).
  - **Evidence:** "mercedes 190", "mercedes benz w212" and "2010 mercedes e350" all show a VIN error.
  - **Fix:** test the trimmed text as one token, or try `carMatches` first.
- **M6. A non-Mercedes VIN picks a Mercedes car.** *(engineer)* **[CLAUDE.md] contradicts "A non-Mercedes VIN fails with a clear message."**
  - **Where:** `index.html:1622-1631` (no manufacturer-code check).
  - **Evidence:** `ZFA20100000123456` (a Fiat) resolved to the W201.
  - **Fix:** require a Mercedes WMI (`WDB`, `WDD`, `WDC`, `WDF`, `W1K`, `W1N`, `W1V`, `4JG`, `55S` …, or the prefixes from `vin_us.json`/`vin2fin`).
- **M7. The Mercedes VM tab shows a JavaScript error instead of the real message.** *(UI, engineer)*
  - **Where:** `importer_jobs.py:92` returns `error` as a list, and `index.html:1984` passes it to `esc()`.
  - **Evidence:** the tab shows `(s ?? "").replace is not a function` instead of "STOP: VBoxManage not found…".
  - **Fix:** return `"\n".join(...)`, and use `String(r.error)` in the UI. Hide the VM select when there is no list.
  - **Side note:** simply opening this tab runs `vm_pipeline.py --list-vms`. Anyone testing should know that.
- **M8. A non-existent import folder silently becomes the home folder, with Import enabled.** *(engineer)*
  - **Where:** `server.py:452-455`, `index.html:1694`.
  - **Evidence:** the dialog showed "Generic folder: 203 HTML / text / PDF files" for `/root`, with Import enabled.
  - **Fix:** answer 404 for an explicit path that doesn't exist.
- **M9. Slow responses can show results for an older search or page.** *(engineer)*
  - **Where:** `keywordSearch()` (`index.html:1333-1357`) and `showPage()` (`1398-1444`) have no sequence guard.
  - **Fix:** a per-function request counter, as `impBrowse` and `catRefresh` already have.
- **M10. Failing or hanging API calls leave blank or dead screens.** *(engineer)*
  - **Where:** `api()` at `index.html:520-528` has no timeout; also the Settings button (`1460-1461`) and `manLoad` (`1855`).
  - **Evidence:** a blank page while `/api/me` hangs; a gear button that does nothing when `/api/settings` fails; a bare "Failed to fetch" when offline.
  - **Fix:**
    - Catch the error and show it.
    - A 20 s `AbortController` timeout.
    - Say "Can't reach AutoRundown — is the PC on?" instead of "Failed to fetch".
- **M11. On a phone, the diagrams panel covers an answer, and Back leaves the app.** *(engineer, UI)*
  - **Where:** `index.html:1308-1312` (auto `openRight()`) and `.closebtn` at `index.html:280` (`position:absolute`, which scrolls away; it was at y = -701 after scrolling the panel).
  - **Fix:**
    - Don't open the panel automatically at ≤900px.
    - Make Close sticky, or a "← Back to answer" bar.
    - `history.pushState` when the panel opens, and close it on `popstate`.
- **M12. One-letter words in the car box match almost every car.** *(engineer)*
  - **Where:** `carMatches()`, `index.html:854-866`.
  - **Evidence:** "c class" lists the C-Class only 7th.
  - **Fix:** match one-letter words as whole tokens, or rank exact class and model names first.

### Performance
- **M13. Keyword search scores every match in the whole index, once per bucket.** *(performance)*
  - **Where:** `search.py:101-114`, `219-224`, `295-298`; `server.py:278-280`; `index.py:99-102`.
  - **What:** FTS5 scores every matching chunk in all 531k rows before `doc_id` filters them. This repeats per bucket (12-14 for a whole generation), per AND/OR pass and per synonym query.
  - **Evidence:** on the synthetic index (a worst case): a whole-generation search took 7-20 s. CLAUDE.md already notes about 0.7 s for the W140.
  - **Fix:**
    1. Add `doc_id` as an FTS column (weight 0) and match `doc_id:"<id>" AND (<query>)`. Rebuilding the FTS table takes about 50 s, with no re-ingest. Per bucket, this was about 10x faster.
    2. Run one statement per query across the whole scope, with `ROW_NUMBER() OVER (PARTITION BY doc_id …)`.
    
    Together: 7.2 s → 0.19 s, and the top-24 results were identical.
  - **[CLAUDE.md]:** both fixes keep the vehicle filter in SQL before ranking. In fact they move it *into* the FTS match, which makes the recorded property stronger.
- **M14. No gzip and no cache headers.** *(performance)*
  - **Where:** `server.py:131-132`, `230-232`, `258-264`.
  - **What:** every phone visit re-downloads about 860 KB at real scale.
  - **Evidence (estimate):** phone on Fast 3G, real-size payload: car box ready after 6.9 s.
  - **Fix:**
    - `GZipMiddleware(minimum_size=1000, compresslevel=6)`, measured 6.9 s → 2.8 s.
    - ETags on `/` and `/api/vehicles`.

### UI
- **M15. Touch targets under 44px on the phone.** *(UI)*
  - **Where and evidence:**

    | Element | Line | Measured |
    |---|---|---|
    | `.pagelink` | 120 | 37px |
    | `.viewtoggle button` | 128 | 36px |
    | `details.caveat summary` | 162 | 37px |
    | `details.lemfolder > summary` | 274 | 29px |
    | `.chip`, with its checkbox | 98 | 34px; checkbox 13x13 |
    | `select.inline#model` | 39 | 40px |
    | tree twisty | 254 | 32x36 |
    | list checkboxes in the import dialog | 227-241 | 13px |

  - **Fix:** `min-height:44px` on these, a global 20px checkbox rule (see S2), and whole-row click targets.
- **M16. Placeholder text fails contrast (3.28:1), and some placeholders are the only label or help.** *(UI)*
  - **Where:** `index.html:33-36` (no `::placeholder` rule); affects `#sExtractor` and `#uCap`.
  - **Fix:** `::placeholder{color:#8a93a6;opacity:1}`, and real labels and help text.
- **M17. Keyword results and cross-references can't be reached by keyboard.** *(UI)*
  - **Where:** `.hit` is a `<div onclick>` (`index.html:1348`), `.xref` an `<a>` without href (1047), `.conn` a `<span onclick>` (1053).
  - **Fix:** make them `<button type="button">`. This pairs naturally with the C1 fix (one delegated listener).

---

## Low

- **L1. This PC's file paths are visible to phones.** *(security, engineer, UI)* **[CLAUDE.md] contradicts the intent of "`/api/stats` hides this PC's paths from others".**
  - **Where:**
    - `server.py:258-264` (`/api/vehicles` `file_path`)
    - `:329-346` (`/api/page` `file_path`)
    - `:385-389` (image 404 text)
    - `:363-370` (`/api/original` error)
  - **Evidence:** the UI shows "no bodies.pack in /home/user/…" in a 70vh frame (`index.html:1371-1377`).
  - **Fix:**
    - Drop `file_path` from these responses (the UI doesn't use it).
    - Give non-admins generic 404 messages.
    - Check with HEAD before showing Original layout, and on a 404 show "isn't available for this page" with a way back to the reading view.
- **L2. Search words with accented letters are cut short.** *(engineer)*
  - **Where:** `search.py:20`.
  - **Evidence:** "Ölwanne" is searched as `lwanne`.
  - **Fix:** a Unicode-aware token regex, or NFKD folding.
- **L3. TLS: the CA has no EKU, and email and URI names are unconstrained.** *(security)*
  - **Where:** `tls.py:111-124`.
  - **Fix:**
    - Add `ExtendedKeyUsage([SERVER_AUTH])` to the CA.
    - Exclude email names.
    - Write keys with `os.open(…, 0o600)`.
- **L4. Supply chain.** *(security)*
  - **Where:** `.github/workflows/windows-msi.yml`, `packaging/windows/build.ps1:36`, `requirements.txt`, `importers/folder.py:32`.
  - **What:**
    - Actions are pinned by tag, and the workflow has no `permissions:` block.
    - The Python zip is not hash-checked.
    - Requirements are all `>=`.
    - `pypdf` is used but in no requirements file, and the copy here (6.17.0) has 7 advisories.
  - **Fix:**
    - Pin actions to commit SHAs and add `permissions: contents: read`.
    - Check the zip's SHA-256.
    - Use a constraints file for the MSI.
    - Add `pypdf>=6.19`.
- **L5. Startup requests run one after another.** *(performance)*
  - **Where:** `index.html:2055-2058`.
  - **Fix:** fetch `/api/vehicles` and `/api/models` alongside `/api/me`. Saves about 575 ms on a phone.
- **L6. `buildCarIndex` blocks a phone's main thread for about 330 ms (estimate).** *(performance)*
  - **Where:** `index.html:619-635`, `815-852`.
  - **Fix:** build lazily on first focus, and compute `yearsIn` once per Baumuster.
- **L7. Opening a general-pool page scans the bucket.** *(performance)*
  - **Where:** `search.py:326-329`.
  - **Evidence:** about 60 ms.
  - **Fix:** `CREATE INDEX pages_doc_page ON pages(doc_id, page_no)`, which brings it under 0.1 ms.
- **L8. The LEMON catalogue costs about 2 s on first open and about 90 MB of server memory.** *(performance)*
  - **Where:** `lemon_catalogue.py:94-96`, which slugs 279,988 names.
  - **Fix:** slug only the make and year of indexed LEMON cars, or slug lazily per opened folder.
- **L9. The catalogue filter rebuilds the whole tree on every keystroke, and each tick sends 7 requests.** *(performance)*
  - **Where:** `index.html:1806`, `1746-1779`, `1791-1797`.
  - **Fix:**
    - Debounce the filter and toggle rows instead of rebuilding them.
    - Use the folder the POST returns.
- **L10. No light theme, and `color-scheme` is not declared, so native controls draw light on dark.** *(UI)*
  - **Where:** `index.html:10-15`.
  - **Fix:** `:root{color-scheme:dark}`. Optionally add a light palette, which would help on a bright shop floor.
- **L11. Layout polish.** *(UI)*
  - **Source titles overflow their buttons on the phone.** `.srcbtn` needs `white-space:normal`.
  - **Browse pushes the gear onto a third header row on desktop.** `index.html:51`; set `order` at all widths.
  - **The suggestion list doesn't line up with the car input.** `.suggest{right:70px}` at `index.html:44`.
  - **Header details.**
    - The `#q` placeholder is cut in half.
    - "VIN" sits alone on its own row.
    - The header takes 297 of 812px.
  - **The Import dialog on phones:**
    - Tabs wrap.
    - Names are cut off.
    - There is a scroll box inside a scroll box.
  - **Job-log failures are grey and can sit below the fold.** `#impStatus`.
  - **The sign-in error is grey like the help text.** `#signinNote`, which also needs `role="alert"`.
  - **No custom focus ring, and no tab ARIA.**
- **L12. Answer formatting.** *(UI)*
  - **Every sentence becomes its own paragraph.** `index.html:1251`; split only on blank lines.
  - **Torque values break across pills.** "37 Nm (27 lb ft)" turns into two pills with stray brackets.
  - **The cited diagram shows twice.** It appears in Figure and again in "Diagrams from cited pages" (`index.html:1308`).
- **L13. Small validation and double-click issues.** *(engineer)*
  - **Input checks:**
    - A 100,000-character `archive_dir` gives a 500 from `/api/browse`.
    - A 10-character FIN isn't decoded in the car box.
    - An empty search gives no feedback.
    - With no car chosen, a search shows the raw text "unknown vehicle".
  - **Double clicks:**
    - "Remove selected" gives two confirm prompts.
    - Several "Sign-in code" clicks can show a code that is already dead.
  - **A `#code=` link doesn't fill in the code** when the sign-in dialog is already open.
  - **The sign-in throttle locks out a whole address for 15 minutes,** which affects every device behind a shared router. It looks deliberate in `auth.py`.
  - **`HEAD /` returns 405.**
- **L14. LEMON tab: the part that works is the least prominent.** *(UI)* **[CLAUDE.md] a suggestion on a recorded decision.**
  - **What:** the catalogue tree, which can't download anything yet, fills the top. The working folder import is a collapsed section at the bottom.
  - **Fix:**
    - Keep one LEMON tab (the user's decision), but put folder import first and open, and give the tree a "Picking only, downloading isn't built yet" notice.
    - The Folder tab enables Import for a detected LEMON archive while telling the user to use the LEMON tab (`index.html:1705`). Disable it, or relabel it.
  - **Separately, CLAUDE.md is out of date:** it lists the tabs as "Folder · LEMON · Mercedes VM", but the code (commit 0808269) has LEMON · Mercedes VM · Folder and opens on LEMON. Update the design record.

---

## Settings (its own section: the user thinks it needs the most work)

Settings is `index.html:436-469`, its JS is at `1460-1504`, and the server side is `server.py:560-599`. The findings below are all the ones that touch Settings, from the three agents that tested it.

### S1. [HIGH] Saving an empty or mistyped Index path breaks the site, and it survives a reload *(engineer)*
- **Where:** `server.py:580-599` (no validation), `index.py:149-152` (`connect()` creates missing folders and an empty DB).
- **Evidence:**
  - **Empty field:** clearing the field and saving made `/api/vehicles` and `/api/stats` return 500, and the empty value was written to `config.json`.
  - **Typo'd path:** the server silently created and switched to a new, empty 73 KB database. The UI then showed the first-start import dialog, as if every manual were gone.
- **Fix:** refuse an `index_path` that is empty or isn't an existing file with a `vehicles` table, and answer 400 with a message shown inline.

### S2. [MEDIUM] The "require each mechanic to sign in" checkbox renders as a 113x48 white block *(UI)*
- **Where:** `index.html:461-463`. `#sSignin` lacks the inline override that `#sAutostart` (450) and `#variants` (314) carry, so the global `input{width:100%;padding:12px;min-height:48px}` rule (33-36) applies to it.
- **Fix:**
  - One global rule: `input[type=checkbox],input[type=radio]{width:20px;height:20px;min-height:0;padding:0;margin:0;flex:none;accent-color:var(--accent)}`. Then remove the inline overrides.
  - Use a `label.check` row instead of the `.chip` pill.

### S3. [MEDIUM] Unsaved edits are silently discarded *(UI)*
- **Where:** `index.html:1971` (Settings closes before Import opens) and `1577-1578`. Escape and Cancel also discard without a prompt.
- **Evidence:** the reviewer changed Model and the extractor folder, then opened Mechanics & spend and came back. Both had reverted, and nothing was saved.
- **Fix:**
  - Track a dirty flag.
  - Before navigating away, save or confirm.
  - Better: open Import and Mechanics on top of Settings (nested `showModal()`).

### S4. [MEDIUM] Saving any setting overwrites the saved Index path *(engineer)*
- **Where:** `index.html:1487` (Save always sends `index_path`), `server.py:567` (GET returns the path in use), and `server.py:597`. This goes against the code's own comment at `server.py:594-596`.
- **Evidence:** with `serve.py --index X`, changing only the model wrote `X` into `config.json`.
- **Fix:** send `index_path` only when it was edited, or show the saved value and the in-use value separately.

### S5. [MEDIUM] Writing the config file isn't atomic, so requests fail during a save *(engineer)*
- **Where:** `config.py:42-52` (`write_text`); the middleware reads the config on every request (`server.py:167`).
- **Evidence:**
  - 400 mixed requests returned 111 × 500 (`JSONDecodeError`).
  - Triple-clicking Save showed "Settings not saved: Internal Server Error".
- **Fix:**
  - Write to a temp file, then `os.replace()` it, with a lock around the write.
  - Optionally cache the config by modification time.

### S6. [MEDIUM] Layout: no grouping, no help text, taller than the screen *(UI)*
- **What:**
  - **Too tall:** 911px in an 800px window, so Save and Cancel are off screen.
  - **No grouping:** nine flat label/field pairs, with everyday settings mixed among rarely-touched file paths.
  - **No help text:** "Archive directory", "Index path" and "Data" don't say what they do. A wrong Index path is S1.
  - **Sign-in is split up:** "Require sign-in" and "Mechanics & spend…" are separate rows.
  - **The phone address is buried,** even though it is the most useful thing for a shop.
  - **A hard-coded, often wrong path:** "~/.autorundown/config.json" (438) is wrong under `AUTORUNDOWN_HOME` and means nothing on Windows.
  - **No key status:** nothing says whether a key is saved, or that Ask is off without one.
  - **No save feedback:** success just closes the dialog; failure shows `alert()`.
  - **"Model" means two things:** the AI model in Settings and the toolbar, and also the aria-label of the car picker.
  - **The two AI-model pickers save differently:** the toolbar one saves at once, the Settings one on Save.
- **Fix (proposed layout):** five fieldsets:
  1. **AI answers:** the key with a status line, and "AI model for answers" with its price as help text.
  2. **Shop phones:** the phone addresses with Copy buttons, the sign-in toggle with help text, and "Mechanics & spend…" directly under it.
  3. **Manuals:** "Import or remove manuals…" plus a one-line count.
  4. **This PC:** "Start when I sign in".
  5. **Advanced (folders):** collapsed, holding Archive directory, Index path and the extractor folder, each with one line of help.
  
  Also:
  - A scrolling body with a sticky footer (`max-height:90vh`).
  - An inline "Saved" message, and errors inline instead of `alert()`.
  - Rename the toolbar's aria-label to "AI model".

### S7. [LOW] Settings accepts any model name *(engineer)*
- **Where:** `server.py:588`.
- **What:** `{"model":"gpt-4"}` is saved. Settings then shows a blank model, and the next save writes `""`.
- **Fix:** validate against `query.MODELS`.

### S8. [MEDIUM] Mechanics & spend: limits aren't validated *(engineer)*
- **Where:** `server.py:66-74`, `auth.py:136-163`, `index.html:1570`, `1552-1553`.
- **Evidence:**
  - A limit of -5 is stored, and that mechanic then gets 402 on every question.
  - `1e999` or `"NaN"` silently mean no limit.
  - "Limit -5" says "Not a number".
  - Accounts can't be renamed or deleted.
- **Fix:** `confloat(ge=0, allow_inf_nan=False)` on both models, the same check in the UI, and a clear message.

### S9. [LOW] Mechanics dialog layout and wording *(UI)*
- **What:**
  - The limit field has only a placeholder, cut to "$ limit / m" on a phone.
  - The code card shows below the whole list, away from its mechanic.
  - "Disable" is styled like a harmless button.
  - "Limit" uses `prompt()`.
  - There is no empty state.
  - It says "Turn it on in Settings" right after Settings was closed.
- **Fix:**
  - A visible "Monthly limit ($, optional)" label.
  - The code card inside the mechanic's row.
  - `class="danger"` for Disable.
  - An inline number field for the limit.
  - An empty state.
  - An inline sign-in toggle.

---

## Findings that touch decisions recorded in CLAUDE.md

| Finding | Relationship to CLAUDE.md |
|---|---|
| M6 non-Mercedes VIN accepted | **Contradicts** "A non-Mercedes VIN fails with a clear message." The code doesn't do what the record says. |
| L1 file paths exposed to phones | **Contradicts the intent** of "`/api/stats` hides this PC's paths from others": other endpoints still expose them. |
| L14 LEMON tab order | A **suggestion on a recorded decision** (one LEMON tab, the user's choice); it keeps one tab and only reorders it. Also, CLAUDE.md's tab order "Folder · LEMON · Mercedes VM" is **out of date** against the code. |
| M13 search speed | **Consistent:** both fixes keep the vehicle filter in SQL before ranking, and fix 1 makes it stronger. |
| C3 Enter key | No recorded decision; the UX suggestion (Ask as a secondary action) is the reviewers' opinion. |
| H1, H2 wrong car searched or opened | Breaks the **core safety property** ("A wrong-vehicle spec must never be a candidate") at the UI layer, though the SQL filter itself is intact. |

## Not covered
- **WIS variant and engine picker, US VIN decoding, Original layout, WIS images:** the sample corpus has no real catalogue, `bodies.pack` or images.
- **`test_ui_picks.py` and `eval_wis.py`:** they need the real index.
- **Real phones, Windows-only behaviour and real paid answers:** UNC/SMB paths, SmartScreen and MSI behaviour were out of scope, and no paid answers were asked by design.
