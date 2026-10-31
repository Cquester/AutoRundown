# Shop network: who may do what

<!-- Moved out of CLAUDE.md on 2026-10-12 to keep that file short; the text
is unchanged apart from file paths (tests/, tools/). "Above" / "below" and
section names may refer to CLAUDE.md or another file in docs/notes/. -->

### Shop network: who may do what (2026-10-09, the user's decisions)

**The PC running the server is the admin, and the only one.** `auth.py`:
a request is admin when its socket peer is loopback *and* its Host header is
loopback (`localhost`, `127.0.0.1`, `[::1]`) -- the second check stops DNS
rebinding, where a web page's own name resolves to 127.0.0.1. Admin needs no
sign-in. `server.ADMIN_PREFIXES` (`/api/settings`, `/api/browse`,
`/api/vms`, `/api/import`, `/api/admin/`) answer 403 to anything else; the
middleware `who_is_asking` enforces it in one place. Before this,
`POST /api/settings` was open to the LAN: a phone could replace the API key.
uvicorn runs with `proxy_headers=False` so `X-Forwarded-For` can never make a
request look local -- **do not put a reverse proxy on the same PC** in front
of it; every request would then come from loopback.

**Mechanic accounts can be turned off** (`Config.require_signin`; on by
default since 2026-10-29 -- off let any device on the shop's Wi-Fi spend the
company key; a `config.json` that says `false` keeps it, and `dev.py` keeps
a test box's phone side open). No passwords: the admin issues an 8-character code
(15 min, single use, no 0/O/1/I/L; since 2026-10-16 the fallback under a
sign-in QR, see below), a phone swaps it for a 32-byte token in
an HttpOnly cookie. **A token ends after 30 days unused** (`TOKEN_DAYS`;
each use renews it; until 2026-10-29 it was 365 days fixed, which tokens
made then still carry -- the 30-day idle rule ends them too). **The cookie
is named per port** (`ar_token_<port>`, `auth.cookie_name`): browsers send
a host's cookies to every port, so a second copy of the app on the PC (a test
copy, `dev.py`) replaced this one's sign-in; the old shared `ar_token` is
still read, and replaced at the next sign-in. Only SHA-256 hashes of codes and tokens are
stored. Disabling a user deletes their tokens at once. 10 wrong codes from
one address = 429 for 15 min. Cross-site POSTs are refused by `Origin` check
(`_origin_ok`), for the admin too. Accounts are in `~/.autorundown/auth.db`,
**not in the index**, so a rebuild cannot lose them.

**Public paths** (no sign-in): `/`, `/manifest.webmanifest`, `/ca.crt`,
`/api/me`, the sign-in routes, and (2026-10-29) `/favicon.ico` and
`/icons/<file>` -- the app's own icons, a fixed folder, `png`/`ico`/`svg`
only, with the same plain-name check as `/vendor/`; the sign-in screen has
a tab icon. Everything else answers 401 to a device not signed in.

**Sign-in QRs replace typed codes (2026-10-16).** The admin's "Sign-in QR"
button (and adding a mechanic) opens a dialog: `POST
/api/admin/users/{id}/qr` makes a *grant* (`auth.db` table `grants`) with a
32-byte link token (in the QR: `<lan>/signin?t=…`) and an 8-character typed
code (shown under the QR for a camera that can't scan -- kept, same alphabet,
same throttle). Only SHA-256 hashes are stored; the secrets go to the
admin's page once. Using either one uses both (one conditional UPDATE, so
two phones racing get one sign-in); 15 minutes, checked on the server;
closing the dialog (or leaving the page: `pagehide` + keepalive) voids it;
opening a new one voids the mechanic's older one; disabling a mechanic voids
theirs. `GET /signin` is public: throttled like the code, refused when
`Sec-Fetch-Site` says another site sent the phone there (a camera app is
"none"), sets the usual cookie (and ends the phone's previous token, if it
was someone else), and 303s to `/` with `Cache-Control: no-store`, so the
token leaves the address bar. Failures go to `/#signin=expired|refused|wait`
and the page says why. The dialog polls `GET …/qr/{grant}` and shows
"signed in" when used; a grant id under another mechanic is 404.
Migration: the old `codes` table is emptied at connect (codes lived 15
minutes); tokens are untouched, so signed-in phones stay signed in. The QR
holds the PC's LAN address (`lan_ip()`), not autorundown.local: Android
camera apps often can't resolve .local; both addresses are written beside
the site QR. QRs are SVG from `segno` (pure Python, `qr.py`).

**Whose key pays (2026-10-16).** `Config.key_mode` = `"company"` (default)
or `"per_mechanic"`; `Config.mechanic_keys = {"<user id>": {"api_key",
"loaded_usd", "since"}}` in config.json (0600, atomic). `config.pick_key(cfg,
user_id, admin)` is the one place that chooses: company mode and the PC ->
`api_key`/env; a mechanic -> their own key, or `NoKeyForRequest` (a plain 400,
never a fallback); a device not signed in, in per-mechanic mode -> refused.
Spend is tagged `usage_log.key_ref` ("company" / "user:<id>", never the key).
Anthropic gives an ordinary key no balance (cost reports need an Admin key
with analytics scope), so "remaining" = `loaded_usd` minus the logged spend
with that key_ref since `since`; a new key or "Topped up / new key" resets
`since`. The admin sees the last 4; a phone gets only its own
`own_key {has_key, loaded_usd, spent_usd, remaining_usd}` in `/api/me`, and
`/api/stats.has_api_key` says whether *it* may ask. `/api/me` has `can_ask` (any
provider with a key this requester may use, `config.menu_models`) and, when
false, `ask_off` (why): the AI chat then greys out the box and Ask and says
why, with "Add an API key" (the PC) and "Use Keyword search" (2026-10-30, the
user: on a PC with no key a question looked like a search that never ended). Per-mechanic routes:
`POST /api/admin/users/{id}/key` (`api_key`, `loaded_usd`, `reset`,
`clear_loaded`, `remove`). `/api/settings` adds `key_mode`, this month's
`month_questions` / `month_spend_usd` (everyone, every source) and
`company_key_last4`.

**Keys per AI provider (2026-10-16).** `pick_key(cfg, user_id, admin,
provider=None)` picks the key of the active provider (`Config.provider`):
the Anthropic company key is still `api_key`, the others
`provider_keys[provider]`; a mechanic's non-Anthropic keys sit under
`mechanic_keys["<id>"]["providers"]`. key_ref gains ":<provider>" for
non-Anthropic keys ("company:gemini", "user:7:gemini"), so old rows still
count for the Anthropic keys. A mechanic's key for one provider never pays
for another; switching provider keeps every key. `/api/settings` adds
`provider` and `providers` (per provider: label, key page, `has_key`,
last 4 -- never a key); `/api/me` adds `provider`. Details:
`ai-providers.md`.

**Send this car to the desk (2026-10-16, `handoff.py`).** The user's story:
scan or type a VIN on a phone in the bay, walk to the PC, and the car is
waiting there. A phone (any device but the PC) has a **Send this car to the
desk** button under the scope note; it is a button, not automatic, so only
cars the mechanic means to hand over reach the desk (every car typed while
looking something up would otherwise pile up there). `POST /api/handoff`
stores the *structured pick* -- `doc_id`, variant index, engine families,
year, VIN -- in `auth.db` table `handoffs` (not the index: a rebuild must
not touch it). `check_pick` validates it against `index.vehicles` (known,
listed doc_id; variant in range of the stored order; engines among that
car's/variant's `engine_families` or `engine_by_bm`; year 1900..now+2; VIN
`[A-HJ-NPR-Z0-9]{10,17}`) and builds the label from the index; a label sent
by the phone is ignored. The PC cannot send (it is the desk). 30 sends per
sender per 10 minutes (in memory, 429); 10 kept per sender, 200 in all;
24 h expiry; Origin check like every POST. **Who sees what**: the PC sees
all (the latest 5, labelled with the sender's name -- a snapshot, like
`usage_log.user_name`); a signed-in mechanic sees only their own, from any of
their devices; a device not signed in (possible only with sign-in off) sees
only cars sent by devices not signed in -- a shop-wide list, which the phone
says under the button ("Sign-in is off in this shop, so a car sent from here
is shown on the desk PC and on any device on the shop network that is not
signed in. Its VIN goes with it."). That is no wider than sign-in off
already is: such a device can use the whole site. With sign-in required, a
device not signed in is 401 at `who_is_asking` like every other route.
Dismiss (`POST /api/handoff/{id}/dismiss`) works only on a car the caller
can see; anything else is 404. The desk (`#deskCars`, under the header)
asks `GET /api/handoff` every 15 s while the page is visible and at once on
focus (other devices every 60 s) -- no websockets: one small SQLite read,
and the PC is right there. **Open** re-checks the pick against the page's
own `/api/vehicles` list (`waitingEntry`), applies it with `applyCar` -- the
car box's own path, so `variant=`/`engine=`/`year` are exactly what a typed
pick sends -- restores "Any M272" from Browse, and dismisses it. Names and
labels are escaped (`esc`), ids are numbers. The sending device hides its
own sends (`localStorage` `autorundown.sentToDesk`). Hook for a VIN scanner:
`applyCar(carFromVin(vin))`, then `sendToDesk()`. Tests:
`tests/test_handoff.py`; driven end to end in a box (phone signed in by QR
link, VIN typed, sent; desk opens it; the search carries the same
doc_id/variant/engine).

**Spend per mechanic.** `usage_log` gained `user_id`, `user_name` (nullable,
`ADDED_COLUMNS`); the name is a snapshot so reports survive account changes.
A monthly cap (`users.monthly_cap_usd`, UTC calendar month) is checked
before the API is called (402); keyword search stays free. (`rebuild_wis.py`
used to carry log rows across from a restored backup, comparing on the
backup's columns; since 2026-10-16 it rebuilds in place and never moves the
log.)

Phones keep their model choice in `localStorage` and send it per question;
only the PC changes the default. `/api/stats` hides this PC's paths from
others. The UI asks `/api/me` first and shows the gear (admin), an account
button (mechanic) or the sign-in box. `#code=XXXX` in the URL pre-fills it.

**HTTPS (`serve.py --https`, `tls.py`).** Own CA (EC P-256, 10 years,
`~/.autorundown/tls/`), Name Constraints (critical): DNS `localhost`,
`.local`; private IPv4/IPv6 ranges only -- a leaked CA key cannot sign for a
real site. Server cert 397 days, SANs = localhost, `<host>.local`, loopback
and every private address of the PC; re-issued at start when an address is
missing or <30 days remain. `/ca.crt` is public. Verified with Python's ssl
(OpenSSL) for `localhost` and `127.0.0.1` in `tests/test_access.py`. **Not yet
tried on a real iPhone or Android phone** -- in particular whether iOS
accepts the critical Name Constraints. A CA made since 2026-10-09 also
carries EKU serverAuth (a leaked key can't sign code or mail); keys are
created 0600, never written then chmod'ed.

**Hardening from the QA review (2026-10-09; `docs/qa/REPORT.md`).** All in
`server.py` unless named; `tests/test_access.py` covers each.
- **Every request must name this PC** (`auth.host_allowed`): an IP address,
  localhost, the PC's name, `<name>.local`, `autorundown.local`, else 421.
  The admin check alone let a DNS-rebinding page search and *ask on this
  PC's key* as an ordinary LAN user.
- **Admin routes refuse cross-site browser requests** (`Sec-Fetch-Site`
  other than same-origin/none) -- an `<img src=…/api/vms>` on any site ran
  the extractor. Clients without the header (curl, `launcher --stop`) pass.
- **No handler is built from manual text.** Page/connector/view links are
  buttons with `data-page`/`data-doc` and one delegated listener in
  `index.html`; `esc()` escapes `'`. A LEMON page file named `9');…('.html`
  ran script as the admin, and through `extractor_dir` + `/api/vms` ran code
  on the PC. The `lemon` importer skips pages not `[A-Za-z0-9._-]+`;
  `extractor_dir` must be a local absolute folder holding `vm_pipeline.py`.
- `/api/image` serves picture types only, with a sandboxing CSP + nosniff
  (an SVG in `images/` ran as the admin). Every response: X-Frame-Options
  SAMEORIGIN, frame-ancestors 'self', nosniff, no-referrer.
- Bodies over 256 KB are refused unread (`BodyLimit`); search `limit` 1-50,
  `q` 500 chars; a question 2,000 chars (paid input); monthly caps 0-100,000
  and finite (-5 blocked a mechanic for good, Infinity meant no limit).
- No `/docs`, `/redoc` or `/openapi.json` (2026-10-16): they mapped every
  route for any device, and `/docs` loaded Swagger's script from a CDN on
  this origin. A NUL in an `/api/image` name (`x.png%00.db`) is a 404 -- it
  was an unhandled 500 without the security headers.
- **Scan VIN adds no upload route** (2026-10-16). The photo is read in the
  phone's browser (zxing-wasm + tesseract.js) and never sent; the 256 KB
  body cap is unchanged. The only new route is `GET /vendor/<path>`: the
  vendored libraries in `autorundown/web/vendor/`, read-only, not public
  (sign-in rules as for any other path -- signed-out phones get 401), only
  `.js .wasm .gz .txt .md`, resolved inside that folder (`..` and `%2e%2e`
  are 404), `Cache-Control: private, max-age=1 year, immutable` (the version
  is in the folder name). A file input with `capture` is used, not the live
  camera: `getUserMedia` needs a secure context, which plain-http LAN
  access is not. Tests: `tests/test_access.py` ("Scan VIN's libraries").
- On Linux/macOS the app folder is created 0700 and `auth.db` 0600 (sqlite
  made them 0644; an older 0644 `auth.db` is tightened). Windows unchanged.
- Phones get no `file_path` (vehicles, page, image/original errors); Settings
  refuses an empty or missing index, a non-index file, an unknown model and
  a network-share extractor; `config.json` is written atomically (a
  half-written file made other requests 500).
