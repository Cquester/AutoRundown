# QA report: UI/UX (qa-ui-designer)

Run 2026-10-09 in an isolated cloud container against `python dev.py` sample
data. Desktop 1280x800 and phone 375x812, dark and light colour schemes.
Screenshots, scripts (`audit.py`, `probe.py`, `probe_phone.py`) and
measurements (`results.json`) were under `.dev/qa/ui-designer/` (not committed).

**Summary.**
- **Worst problem:** the car box can show one car while searches and answers run against another.
- **Settings needs the most work:** one checkbox is visibly broken, the page loses unsaved edits without warning, and the fields are an ungrouped list with almost no help text.
- **Mercedes VM tab:** shows a JavaScript error instead of the real one.
- **No light theme:** no `prefers-color-scheme` rule exists, so light screenshots are identical to dark. Contrast was therefore measured in dark only.

**What the reviewer changed or triggered in the dev instance.**
- **A test mechanic now exists:** "QA Test Mechanic" ($20 limit) in `.dev/home`'s account database, added to see the account list and code card.
- **Opening the Mercedes VM tab ran a VirtualBox query.** The page called `/api/vms`, which runs `extractor/vm_pipeline.py --list-vms` (it failed; there is no VirtualBox here). Testers should know that opening the tab does this, because it touches the runbook's "nothing that talks to VirtualBox" rule.
- **The sign-in box used a faked response, not the config file.** `/api/me` and `/api/auth/signin` were faked in the browser.

`/api/ask` was never called, no key was entered, and no import or removal job was started.

---

### HIGH The car box can show one car while searches use another
- Where: `autorundown/web/index.html:903-927` (`#carInput` `input`/`blur` handlers); results list `index.html:1347`.
- What: typing a car without picking a suggestion leaves that text in the box. `#vehicle` keeps the previous car, and Keyword search and Ask use the previous car. Nothing on screen says which car was actually searched.
- Evidence: with the F-150 selected, the reviewer typed "2018 Mercedes W213 E-Class" and pressed Keyword search. The box still showed the W213, `#vehicle` was `2015-ford-f-150-2wd-v6-3-5l-turbo`, and the results were F-150 pages. Screenshots: `desk-dark-02f-carbox-stale-search.png`, `desk-dark-02e-carbox-unpicked.png`.
- Fix:
  - On `blur`/`change`, if no suggestion was applied, put the applied car's label back in the box, or apply the top match when there is exactly one.
  - In `ask()` and `keywordSearch()`, refuse to run while the box text differs from the applied car ("Pick your car from the list first").
  - Show a "Results for: <car label>" line on the keyword results and the answer card.

### MEDIUM Settings: the "require each mechanic to sign in" checkbox renders as a 113x48 white block
- Where: `index.html:461-463`. `#sSignin` lacks the inline `style="width:auto;min-height:auto;padding:0"` that `#sAutostart` (450) and `#variants` (314) carry, so the global `select,input,textarea{width:100%;padding:12px;min-height:48px}` rule (33-36) applies to it.
- Evidence: the box measured 112.9x48px, and the label wraps to three lines in a pill (`desk-dark-09b-settings-tall.png`, `phone-dark-09-settings.png`).
- Fix:
  - Add one global rule after line 36, `input[type=checkbox],input[type=radio]{width:20px;height:20px;min-height:0;padding:0;margin:0;flex:none;accent-color:var(--accent)}`, and drop the inline overrides.
  - Don't use `.chip` (a 999px pill, 13px muted text) for a setting. Use `label.check{display:flex;gap:10px;align-items:center;min-height:44px;color:var(--text);font-size:15px}`.

### MEDIUM Settings: "Import data…" and "Mechanics & spend…" silently throw away unsaved edits
- Where: `index.html:1971` (`$('#settings').close()` before `openImport`), `index.html:1577-1578`. Escape and Cancel (466) also discard without a prompt.
- Evidence: the reviewer changed Model to Haiku and filled in an extractor folder, opened Mechanics & spend, closed it and reopened Settings. Both values had reverted, and the server had saved nothing.
- Fix:
  - Track a dirty flag on `input`/`change`.
  - Before Import, Mechanics, Cancel or Escape, save first or ask "Save your changes first?".
  - Better: open Import and Mechanics on top of Settings (nested `showModal()`) instead of closing it.

### MEDIUM Settings: no grouping, no help text, and it scrolls past the screen on a desktop
- Where: `index.html:436-469`; save feedback at 1484-1504.
- What, as a first-time shop owner would see it:
  - **Too tall for the screen.** The dialog is 911px tall in an 800px window, so Save and Cancel are below the fold, and the footer doesn't stay in view (`desk-dark-09-settings.png`).
  - **No grouping.** Everyday choices (API key, model) and rarely-touched file paths (Archive directory, Index path, Mercedes extractor folder) are mixed in one flat list of nine label/field pairs, with no headings.
  - **No help text.** "Archive directory" and "Index path" don't say what they do or what happens when changed, and a wrong Index path quietly points the app at another (or an empty) database. "Data" is a label over a single button.
  - **Sign-in is split up.** "Require sign-in" and "Mechanics & spend…" are separate rows under a label ("Phones and other computers") that doesn't connect them.
  - **The phone address is hard to find.** It is the most useful thing for a shop, but `#sPhone` (454) sits between the autostart and extractor rows and only appears when there is a network address.
  - **The intro sentence names a path that is often wrong.** "Stored locally in ~/.autorundown/config.json" (438) is wrong when `AUTORUNDOWN_HOME` is set, and means nothing to a Windows user.
  - **No sign of whether a key is saved.** With no key the field just shows `sk-ant-...`. Nothing says "No key: Ask is off, keyword search still works", and nothing says where to get a key.
  - **No save feedback.** On success the dialog just closes; on failure it shows an `alert()` (1499).
  - **"Model" means two things.** "Model" is the AI model in Settings (441) and in the toolbar (`#model`, aria-label "Model", 312), but `#vehicle` is also aria-label "Model" (296). A mechanic reads "Model" as the car.
  - **The two AI-model pickers save differently.** The toolbar one saves the default to the server on change (988-997); the Settings one waits for Save.
- Fix: five `<fieldset>`s, each with a `.sechead` legend:
  1. **AI answers.**
     - API key, with a status line: "Key saved", or "No key yet: Ask is off; keyword search works. Get a key at console.anthropic.com".
     - "AI model for answers", with the price line as help text. Rename the toolbar aria-label to "AI model" too.
  2. **Shop phones.**
     - The phone address(es), each with a Copy button.
     - A "Require each mechanic to sign in" toggle with help text ("Off: anyone on your wifi can search and ask on your account").
     - "Mechanics & spend…" directly under the toggle.
  3. **Manuals.** "Import or remove manuals…" plus a one-line summary ("3 manuals, 201 pages indexed").
  4. **This PC.** "Start when I sign in".
  5. **Advanced (folders).** A collapsed `<details>` holding Archive directory, Index path and Mercedes extractor folder, each with one line of help.
  
  Also:
  - Dialog `display:flex;flex-direction:column;max-height:90vh`, with the body scrolling and a sticky footer.
  - Show "Saved" inline for 2 seconds before closing.
  - Show errors inline instead of `alert()`.
  - Replace the line-438 text with the real path from `/api/settings`, or drop it.

### MEDIUM Mercedes VM tab shows a JavaScript error instead of the real reason
- Where: `autorundown/importer_jobs.py:92` returns `error` as a list (`splitlines()[-1:]`). `index.html:1984` calls `esc(r.error)`, which runs `.replace` on a list.
- Evidence: the box reads "(s ?? "").replace is not a function", with an empty VM menu above it (`desk-dark-13-import-vm.png`).
- Fix:
  - Server: return `"\n".join(...) or "VirtualBox not found"`.
  - UI: `esc(String(r.error))`.
  - Hide the VM select and steps when no list is available.

### MEDIUM "Original layout" error state shows a raw server path, also to LAN phones
- Where: `index.html:1371-1377` (`originalFrame`) loads the iframe without checking first. The text comes from `wis_original.NotAvailable` via `server.py:363-370`. `/api/original` is not in `ADMIN_PREFIXES`.
- Evidence: a 70vh grey frame reading "no bodies.pack in /home/user/AutoRundown/sample_corpus/wis_en" (`desk-dark-07-page-original.png`). This contradicts the CLAUDE.md intent that this PC's paths are hidden from others.
- Fix:
  - Server: send non-admins a generic message.
  - UI: check first with HEAD. On a 404, show "Original layout isn't available for this page" with a "Back to reading view" button, and don't remember "original" for this page.
  - A working Original layout could not be seen; the sample has no `bodies.pack` (unverified).

### MEDIUM Phone: the source panel's Close button scrolls away and overlaps titles
- Where: `.closebtn` at `index.html:280` (`position:absolute; top:70px`), inside the scrolling `#right` (324).
- Evidence:
  - After scrolling the panel 900px, Close sat at y = -701. There is no other way back to the answer, and the browser Back button isn't wired.
  - A long title runs under the button (`phone-dark-06f-close-overlap.png`).
- Fix:
  - Make Close stay in view: `position:sticky; top:0; float:right`, or a sticky panel bar with "← Back to answer".
  - Call `history.pushState` when the panel opens and close the panel on `popstate`, so the phone's Back gesture works.

### MEDIUM Touch targets under 44px on the phone
- Where and evidence (measured at 375px):

  | Element | Line | Measured |
  |---|---|---|
  | `.pagelink` | 120 | 37px tall |
  | `.viewtoggle button` | 128 | 36px |
  | `details.caveat summary` | 162 | 37px |
  | `details.lemfolder > summary` | 274 | 29px |
  | `label.chip` "include other trims/engines" | 98 | 34px; its checkbox 13x13 |
  | `select.inline#model` | 39 | 40px |
  | Tree twisty `.trow > .tw` | 254 | 32x36 |
  | `.trow > .nm` | 259 | 36px |
  | Import-dialog list checkboxes | 227-228, 236-241 | 13px wide |

- Fix:
  - `min-height:44px` on `.pagelink`, `.viewtoggle button`, `details summary` (flex-centred), `.chip` and `select.inline`.
  - A 44px tree twisty.
  - The global 20px checkbox rule (see the Settings checkbox finding).
  - Make the whole `.lemrow`/`.trow` row the click target.

### MEDIUM Placeholder text fails contrast, and it carries real instructions
- Where: `index.html:33-36`. There is no `::placeholder` rule, so Chromium's default `rgb(117,117,117)` applies.
- Evidence: 3.28:1 on `#212632`, against the 4.5:1 required. Affected placeholders:
  - `#carInput`
  - `#q`
  - `#sExtractor`, whose placeholder is the field's only help text
  - `#uCap`, whose placeholder is the field's only label
- Fix:
  - Add `::placeholder{color:#8a93a6;opacity:1}` (about 4.6:1).
  - Move the instructions into visible help text and real labels.

### MEDIUM Keyword results and cross-references can't be reached by keyboard
- Where: `.hit` rows are `<div onclick>` (`index.html:1348`). `.xref` is `<a>` with no href (1047), and `.conn` is a `<span onclick>` (1053).
- Evidence: the Tab walk never reaches a result row.
- Fix: render them as `<button type="button">`, styled as plain rows.

### MEDIUM Pressing Enter in the question box spends money; the free search is the secondary button
- Where: `index.html:1448-1450`; Ask is the primary button at 308.
- What: a mechanic who types a keyword and presses Enter starts a paid answer, and nothing on the button says it costs money. CLAUDE.md records that the user is sensitive to spend.
- Fix (a suggestion, not a recorded decision):
  - Label the button "Ask AI" with an estimate under it.
  - Make Enter run whichever action was used last, or make it Keyword search when there is no key.
  - With no key, disable Ask with the hint "Add an API key in Settings".

### LOW Source titles overflow their buttons on the phone
- Where: `.srcbtn` (`index.html:168-177`) inherits `white-space:nowrap` from `button` (78).
- Evidence: the content is 334px inside a 307px button (`phone-dark-08d-answer-full.png`).
- Fix: `.srcbtn{white-space:normal}`.

### LOW Desktop: opening Browse pushes the gear onto its own third header row
- Where: `index.html:51` (`.picker{flex:1 0 100%}`). The button ordering exists only inside the ≤700px media query (63).
- Evidence: the header grows from 70px to 186px (`desk-dark-03-browse.png`).
- Fix: give `#acctBtn,#settingsBtn` `order:2` and `.picker` `order:3` at all widths.

### LOW The suggestion list doesn't line up with the car input
- Where: `.suggest{right:70px}` (`index.html:44`), but Browse is about 100px wide.
- Evidence: the list ends at x = 1130, the input at x = 1092.
- Fix: wrap the input and list in a relative container, and give the list `left:0;right:0`.

### LOW No light theme, and native controls aren't told the page is dark
- Where: `index.html:10-15`.
- Evidence:
  - Light and dark screenshots are identical.
  - Native checkboxes draw as bright white squares.
  - Select pop-ups and scrollbars use light chrome.
- Fix: add `:root{color-scheme:dark}`. Optionally add a `prefers-color-scheme:light` palette, which would help on a bright shop floor. That needs:
  - darker text on `.torque` and `.pagelink`;
  - a variable for the hard-coded `#171b23` table stripe (139).

### LOW Import dialog on the phone: tabs wrap, names are cut off, and the tree is a scroll box inside a scroll box
- Where: `.srctabs` (243, wraps), `.lemrow > .nm` (239, ellipsis), `.cattree` (250, a 46vh scroll area inside a dialog that also scrolls).
- Evidence:
  - The "Folder" tab drops to a second row.
  - "1987 Mercedes Benz 19…" hides the variant being chosen.
  - "None" sits alone on its own row.
- Fix:
  - Keep the tabs on one row and let them scroll sideways.
  - Wrap long names in `#impLemList`, as `.manuals` already does (266).
  - Remove the tree's height cap at ≤700px.
  - Keep "All new" and "None" on one row.

### LOW LEMON tab: the part that works is the least prominent (suggestion on a recorded decision)
- Where: `index.html:372-409`.
- What: the catalogue tree, which can't download anything yet, fills the top of the tab. The folder import that does work is a collapsed `<details>` at the bottom. A user who ticks cars in the tree can't work out why nothing imports.
- Fix: keep one LEMON tab (as recorded) but reorder it:
  1. Folder import, open by default.
  2. The catalogue, with a `.warnbox` "Picking only. Downloading isn't built yet."
  3. "Imported LEMON manuals".
- Also noted:
  - The Folder tab leaves Import enabled for a detected LEMON archive while telling the user to use the LEMON tab (1705).
  - **CLAUDE.md has drifted from the code:** it lists the tabs as "Folder · LEMON · Mercedes VM", but the code has LEMON · Mercedes VM · Folder (commit 0808269).

### LOW Job log: failures don't stand out, and the status can sit below the fold
- Where: `#impStatus` is `.muted` (430, text set at 1938-1939); `.implog` (276) sits after all tab content.
- Evidence: "Failed (exit 1) after 1 h 2 min — see the log" shows in grey, off screen (`desk-dark-17b-import-joblog-failed.png`).
- Fix:
  - Show a failure in red, and copy the log's last line into an `.errbox`.
  - Show the log right under the tabs while a job runs.
  - On phones, don't wrap log lines (`white-space:pre; overflow-x:auto`).

### LOW Mechanics dialog: wording and layout
- Where: `index.html:494-510`, 1515-1539.
- What:
  - The cap field has only a placeholder, cut to "$ limit / m" on the phone.
  - The sign-in code card shows below the whole list, away from its mechanic.
  - "Disable" is styled as a ghost button, like harmless ones.
  - "Limit" uses `prompt()`.
  - There is no empty state.
  - The dialog says "Turn it on in Settings" right after Settings was closed.
- Fix:
  - A visible label "Monthly limit ($, optional)", and stack the Add row on phones.
  - Put the code card inside that mechanic's card.
  - Use `class="danger"` for Disable.
  - An inline number field for the limit.
  - An empty state ("No mechanics yet…").
  - An inline sign-in toggle.

### LOW The sign-in error looks like help text
- Where: `#signinNote` uses `.vinnote` (muted, `index.html:74`, 479).
- Evidence: "That code is wrong or has expired" is the same grey as the instructions (`phone-dark-16b-signin-error.png`).
- Fix:
  - `#signinNote:not(:empty){color:var(--bad)}` and `role="alert"`.
  - Show the code as "ABCD 2345", matching the admin's display.

### LOW Answer formatting
- **Every sentence becomes its own paragraph.** `index.html:1251` splits on `(?<=\.)\s(?=[A-Z])`. Split only on `\n{2,}`.
- **Torque values break awkwardly.** "to 37 Nm (27 lb ft)" becomes two pills with stray brackets. Include the parenthesised value in the same span, and set `.torque{white-space:nowrap}`.
- **The cited diagram shows twice on desktop.** It appears in Figure and again in "Diagrams from cited pages". Drop from `d.diagrams` any image already in `d.figures` (1308).

### LOW Phone header details
- **The `#q` placeholder is cut in half.** It is two lines in a one-row textarea; use a shorter phone placeholder.
- **"VIN" sits alone on a fourth row with Browse open,** because `#variant` is hidden.
- **The header is very tall.** It takes 297 of 812px and stays pinned; consider collapsing the picker after a pick.

### LOW Keyboard focus relies on Chromium's default ring
- Where: the only focus rule is `.srctabs button:focus-visible` (248). The tabs also lack `aria-controls`, `role="tabpanel"` and roving tabindex (350-354).
- Fix:
  - Add `:focus-visible{outline:2px solid var(--accent);outline-offset:2px}`, with a text-coloured outline on orange buttons.
  - Add the missing tab ARIA (`aria-controls`, `role="tabpanel"`, roving tabindex).

---

**Checked and fine:**
- **Contrast in dark:** body text, muted text, chips, torque pills, caveats and the Ask button all pass AA.
- **No sideways scroll at 375px** on any screen tested.
- **Recorded decisions are respected:**
  - Answer order: Answer, Procedure, Figures, Specifications, caveats, sources.
  - Other-vehicle specs collapsed.
  - WIS pages rendered line by line, with the `Nm 130` highlight.
  - Import sources as tabs, and the Reading view / Original layout toggle.
- **Empty and not-found states** are clear.
- **VIN dialog** wording and errors are clear.
- **Spec cards** have a good hierarchy, and the safety note shows on every answer.
- **Sizes:** tree and folder rows are 48px; the main buttons are 48px.
- **The sign-in box** can't be dismissed with Escape, and `#code=` pre-fills the code.
- **No page script errors** in any run.
