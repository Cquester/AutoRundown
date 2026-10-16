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

**Mechanic accounts are optional** (`Config.require_signin`, default off =
open LAN as before). No passwords: the admin issues an 8-character code
(15 min, single use, no 0/O/1/I/L; since 2026-10-16 the fallback under a
sign-in QR, see below), a phone swaps it for a 32-byte token in
an HttpOnly cookie (365 days). Only SHA-256 hashes of codes and tokens are
stored. Disabling a user deletes their tokens at once. 10 wrong codes from
one address = 429 for 15 min. Cross-site POSTs are refused by `Origin` check
(`_origin_ok`), for the admin too. Accounts are in `~/.autorundown/auth.db`,
**not in the index**, so a rebuild cannot lose them.

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
`/api/stats.has_api_key` says whether *it* may ask. Per-mechanic routes:
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

**Spend per mechanic.** `usage_log` gained `user_id`, `user_name` (nullable,
`ADDED_COLUMNS`); the name is a snapshot so reports survive account changes.
A monthly cap (`users.monthly_cap_usd`, UTC calendar month) is checked
before the API is called (402); keyword search stays free. `rebuild_wis.py`
compares log rows on the backup's columns and migrates the restored index
before carrying rows over -- without that, the wider rows never matched and
the insert would have failed.

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
- Phones get no `file_path` (vehicles, page, image/original errors); Settings
  refuses an empty or missing index, a non-index file, an unknown model and
  a network-share extractor; `config.json` is written atomically (a
  half-written file made other requests 500).
