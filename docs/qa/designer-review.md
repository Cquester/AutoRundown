# Design review: Settings, Import, first start, and the rest (2026-10-09)

**How this review was done**
- **Who:** a QA web-designer agent, at the owner's request, after the owner tried the 0.1.3 MSI.
- **Where:** the `dev.py` sample data, at 1920x1080, 1440x900, 1280x800 and 375x812, dark and light.
- **Rules followed:** report only; `/api/ask` was never called.
- **Screenshots:** were in `.dev/qa/designer2/shots/` (not committed).

## 1. Settings, Import, Mechanics: dialog, full page or side panel?

**Recommendation: one full-page "Shop setup" view** (`#/setup`) that takes over `<main>` and keeps the header. It has a left nav on a PC, and drills down section by section on a narrow window. No more stacked modals.

### Why
- **Admin is always the PC.** `auth.py` treats only loopback as admin, so these screens never appear on a phone. A 520 px modal (`dialog{width:min(520px,92vw)}`) is designed for a phone that never uses it.
- **Three layers today.** Import opens over Settings, which is over the app, at different widths (520 and 720).
- **Import doesn't fit a modal.**
  - It is about 1,760 px tall in a 720 px box.
  - It has no sticky footer, so the Import button scrolls away.
  - Its job log is about 1,500 px above that button.
  - A VM extract runs for hours, which belongs on a page you can leave and come back to.
- **A side panel is the wrong fit.** The right pane is already the page and diagram viewer, and a drawer would be as narrow as today's dialog.
- **Mixed save models.** Settings needs Save, while Mechanics and Import act immediately.

### Layout
| Width | Layout |
|---|---|
| 1920 | Container max 1280 px, centred. Grid `220px nav \| content`, gap 32 px. Sticky nav. Form sections max 680 px wide. |
| 1440 | Same, container 1200 px. |
| 1280 | Nav 200 px, gap 24 px. |
| ≤900 | The nav becomes a list page (44 px rows). Each section opens full-width with a sticky "← Shop setup" bar. |

### Nav order (aimed at a first-time owner)
1. **Manuals.** A status line plus the import tabs, inline.
   - At 1280 px or wider, two columns: the tabs and pickers on the left; a sticky job panel (status, progress, log) and "Imported manuals" with Remove on the right.
2. **AI answers.** The key, the default model ("phones can choose their own") and the price per question.
3. **Phones & mechanics.**
   - The phone address with Copy, and the sign-in toggle.
   - A mechanics table (Name, This month, Limit, Devices, actions), with the "Add" row at the top.
   - Disable kept away from the harmless buttons.
4. **This PC.** Autostart.
5. **Advanced.** The folders and the config path.

### Saving and the gear button
- **Saving:** toggles and selects save as you change them, with an inline "Saved ✓". Only the key and the folder paths keep an explicit Save, in a sticky bar that appears only when there are unsaved edits.
- **The gear button:** label it "⚙ Shop setup" on a desktop.

## 2. First start ("it lets me in before it's set up")

**The cause, as later found and fixed in `52229ef`:** after an upgrade, the first requests raced to add a new index to a large index file, and several failed with "database is locked". On the empty-index dev setup the reviewer saw 0 errors in 252 requests, so that part of the report is unverified.

**What the user sees today**
- **While loading:** the whole app is live.
- **With no manuals:** the Import modal opens over the live app. Closing it leaves a dead-looking app, and Keyword search says "Pick your car first" when there is no car to pick.
- **On a phone:** the same, with no explanation.

### Proposed states, shown before the app renders (`inert` until resolved)
1. **Starting.**
   - A centred card: "Starting AutoRundown…". After 10 s: "Still starting — this can take a minute after the PC boots".
   - It retries every 2 s and maps any 5xx to this card, never a raw "Internal Server Error".
2. **Not set up (admin).** A "Set up AutoRundown" checklist instead of the two panes: ① add manuals (required), ② API key (optional; keyword search works without it), ③ phones (optional).
3. **Not set up (phone).** "No manuals yet. AutoRundown is being set up on the shop PC — check back later."
4. **Importing.** A slim banner for everyone: "search shows partial results until it finishes". Phones need a small public `/api/status`.

## 3. A calmer LEMON tab (still one tab)

**Today:** twelve blocks and four bordered boxes before Import.

**Proposal:** three cards, one open at a time.
1. **"From a folder on this PC"** (open).
   - Collapsed, it shows the path and "3 vehicles, 1 imported".
   - Open, it shows the path row and the vehicle checklist. The subfolder browser and the "Detected" box hide once a LEMON folder is recognised, behind a "Change folder…" link.
   - All new / None sit in the checklist header.
2. **"Imported LEMON manuals (3)"** (collapsed). Remove lives here.
3. **"LEMON catalogue · Preview"** (collapsed). The summary reads "279,988 vehicles · 0 picked".
   - The warning box becomes one muted line: "Picking only — downloading isn't built yet".
   - "How LEMON downloads will work" becomes a link.

**Also:**
- Show the intro text only on first start.
- Give the dialog (or page) a sticky footer with the job status beside the Import button.
- Base `impSource()` on which card is open.

## 4. The rest of the site, by priority

### HIGH
- **Disclosure triangles are gone on every `<details>`.** `details > summary{display:flex}` removes the marker in Chromium. This affects "Applicability notes & caveats", "N values for other engines — not your car, hidden", "How LEMON downloads will work" and "Advanced: folders".
  - Fix: `summary{list-style:none}`, a `▸`/`▾` `::before`.
- **The light-theme accent fails AA.** `#b86a00` is 4.12:1 on white, and the white button text and 12-14 px accent text fail too.
  - Fix: `--accent:#995500` (5.75:1).

### MEDIUM
- **Text fields have no visible boundary** (`--line` is 1.33-1.43:1 against the panel).
  - Fix: a `--field-line` token at 3:1+ for form controls.
- **The 1920 px layout wastes width.**
  - Answer lines are about 105+ characters.
  - The right pane (pages and diagrams) gets the same 960 px as the answer.
  - Figures stay at 320 px.
  - The car box is 1,590 px wide.
  - Fix:
    - At ≥1400 px: `main{grid-template-columns:minmax(560px,760px) 1fr}`.
    - Reading text max 72ch.
    - Diagrams `width:100%; max-width:720px`.
    - Car box max 760 px.
- **The empty main screen gives no guidance.** Fix: "Ask a question about <car>, or search the manual's words", plus 3 example chips.
- **The phone toolbar takes about 210 px before any result.**
  - Fix:
    - Search becomes a second button in the ask bar.
    - Model and trims go into an "Options" row.
    - The cost becomes plain muted text.
- **Import dialog: the focus ring surrounds the whole tab strip on open, and "LEMON" is clipped to "EMON" on a phone.**
  - Fix: focus the selected tab; at ≤420 px use flex tabs.
- **The Mercedes VM tab looks broken without VirtualBox** (an empty select, a raw "[hh:mm:ss] STOP:" in a nested box, live checkboxes).
  - Fix: hide the select, the steps and the button; show one plain errbox.

### LOW
- **Stale label:** the Folder tab names "Already have LEMON manuals in a folder", which no longer exists.
- **Original-layout error on a phone:** the message overflows (410 px on 375 px) and shows a raw path.
  - Fix: `overflow-wrap:anywhere` and plain copy.
- **Sign-in box:**
  - The placeholder "ABCD 2345" reads as a filled-in code; use "8-letter code".
  - The half-loaded app shows behind it; make sign-in a full-page card.
- **"Other engines" card:**
  - Dead space above the summary.
  - The summary should be muted rather than warning colour; the card stays collapsed, as recorded.
- **Phone header:** 128 px; tighter brand, buttons and padding save about 20 px.

## Checked and fine
- **Contrast in dark mode:** every text colour passes.
- **Contrast in light mode:** passes apart from the accent.
- **Keyboard focus** is visible.
- **No sideways scroll at 375 px,** except the original-layout error.
- **Tap targets** are 44 px or more.
- **Answer order** is as recorded.
- **WIS torque highlight** works.
- **Car box and Browse** work.
- **Spec values** are 30 px.

## Against recorded decisions
- **Kept:** tabs for import sources, one LEMON tab, and the answer order.
- **The "other engines" card** stays collapsed; only its styling changes.
- **The Shop-setup page** replaces the dialogs; that is a new decision for the owner.
