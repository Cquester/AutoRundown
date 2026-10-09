# QA report: security (qa-security)

Run 2026-10-09 in an isolated cloud container against `python dev.py` sample data
(invented text). Live tests used a second copy on `0.0.0.0:8759` with its own home
and index under `.dev/qa/security/`, reached through the container's non-loopback
address (192.0.2.2) so requests were real LAN requests. `/api/ask` was never
called; no tracked file was changed.

Two critical issues, both working end to end:

- A manual page with a crafted file name runs script as the admin. One click on
  it ran the reviewer's own code on the PC.
- Separately, any website the admin visits can use DNS rebinding to send paid
  questions on the API key while sign-in is off (the default).

### [CRITICAL] Script injection through a page number, leading to code execution on the PC
- **Where:**
  - `autorundown/web/index.html:517-518`: `esc` does not escape `'`.
  - `esc` is used inside `onclick="showPage('${esc(x)}')"` at lines 1194, 1209, 1217, 1227, 1297, 1348, 1367-1368 and 1435.
  - The value comes from `autorundown/importers/lemon.py:169` (`page_no=path.stem`, the raw file name).
  - The code-execution step goes through `importer_jobs.py:67-73` (`exec_module` of `<extractor_dir>/wis_paths.py`) and `:87` (runs `<extractor_dir>/vm_pipeline.py`).
- **What:** `esc` turns `"` into `&quot;` but leaves `'` alone. The browser decodes the attribute, so a quote in a page number closes the JS string. A LEMON archive (third-party content from a torrent) containing a file such as `pages/9');<js>;('.html` gets that JS run when the admin clicks the search result. Running as the admin origin, the script can set `extractor_dir` and then call `/api/vms`, which runs Python from that folder. On Windows a UNC or WebDAV path would load that Python from the attacker's server (not tested on Windows).
- **Evidence:** The reviewer imported a copy of the Camry sample with that page file, searched for it and clicked the result.
  - The result's handler read `showPage('9');window.__xss=document.domain;('')`, and `window.__xss` was `127.0.0.1`.
  - A second page whose name ran `eval(atob(...))` sent `POST /api/settings {"extractor_dir":"evilext"}` and then `GET /api/vms`.
  - `.dev/qa/security/evilext/PWNED.txt` then read `executed by pid 1130 argv=['evilext/vm_pipeline.py', '--list-vms']`.
  - Text payloads (`<img onerror>`, `<svg onload>`, a crafted `[diagram:]`) were escaped correctly.
  - Scripts: `xss_test.py`, `chain.py`. Screenshot: `shots/xss_pageno.png`.
- **Fix:**
  - Stop building inline handlers from data: use `data-page="${esc(p)}"` and one delegated listener that reads `dataset.page`.
  - Until then, add `"'":'&#39;'` to `esc` and pass values through `JSON.stringify` inside handlers.
  - In the importer, reject or slug page numbers that are not `[A-Za-z0-9._-]+`.
  - Accept only an `extractor_dir` that is local, absolute and not UNC (`\\`).

### [CRITICAL] No Host check on non-admin routes, so DNS rebinding can spend the API key
- **Where:** `autorundown/server.py:148-170` (`who_is_asking`), `:120-128` (`_origin_ok`).
- **What:** The Host header is checked only when deciding who is admin. A rebinding page at `attacker.example`, resolving to 127.0.0.1, gets through the middleware as "anyone". Its `Origin` matches its own `Host`, so `_origin_ok` lets its POSTs through. With sign-in off (the default), that page can call `/api/ask` on the PC's key and read the whole index. This also works against the localhost-only `dev.py` binding.
- **Evidence:** All requests came from the loopback peer with `Host: attacker.example:8759`:
  - `/api/me` answered `"admin":false`.
  - `/api/search` answered 200 with results.
  - `POST /api/auth/signout` with a matching Origin answered `{"ok":true}`, so the POST got through.
  
  `/api/ask` was not called, but it sits behind the same middleware with no other gate.
- **Fix:** Allowlist `Host` for every request and answer 421 otherwise. Allowed hosts: loopback names, this PC's own addresses (`tls.local_names()`), `<host>.local` and `app.state.mdns_name`.

### [HIGH] `/api/image` serves archive files as active same-origin content
- **Where:** `autorundown/server.py:375-398`.
- **What:** An SVG (or HTML) file in a manual's `images/` folder is served from the app's origin with its own content type and no CSP or `nosniff`. Every diagram has an "Open full size" link, so clicking it runs the file's script with admin rights.
- **Evidence:** `images/evil.svg` was served as `image/svg+xml` with no other headers. When opened, its script read `/api/settings` and wrote the result into the page: `{"archive_dir":"","index_path":"…","require_signin":false}`.
- **Fix:**
  - On image responses, send `Content-Security-Policy: default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox`.
  - Send `X-Content-Type-Options: nosniff`.
  - Serve only an allowlist of image extensions, and refuse `.html` and `.htm`.

### [MEDIUM] Admin GET routes answer cross-site requests
- **Where:** `autorundown/server.py:451-502` (`/api/browse`, `/api/vms`, `/api/import/*`). The `Origin` check covers only non-GET requests.
- **What:** A plain `<img src="http://localhost:8756/api/...">` on any web page counts as an admin request.
  - `/api/vms` starts a subprocess.
  - `/api/browse?path=C:\` makes the server walk the drive.
  - On Windows, a UNC path would make the PC open an SMB connection and could leak an NTLM hash (not tested on Windows).
- **Evidence:** An `<img src="http://localhost:8759/api/vms">` on another origin ran `evilext/vm_pipeline.py`. Chromium's local-network protection blocked the request when the attacking page came from a non-loopback origin. Firefox has no such block.
- **Fix:**
  - For every `ADMIN_PREFIXES` path and any method, require `Sec-Fetch-Site` to be `same-origin` or `none`, or require a custom header such as `X-AR: 1` (which forces a CORS preflight).
  - Refuse UNC paths in `/api/browse` and `/api/import`.

### [MEDIUM] Missing security headers (clickjacking and others)
- **Where:** every response, e.g. `server.py:230-232`.
- **What:** No `X-Frame-Options` / `frame-ancestors`, CSP, `X-Content-Type-Options` or `Referrer-Policy`. Another origin can frame the admin UI and trick the admin into clicking "Remove all Mercedes WIS data".
- **Evidence:** The response to `/` carries only date, server, content-length and content-type. A page on another origin framed the app, and `/api/me` inside the frame answered `admin: True` (`cj.py`, `shots/clickjack.png`).
- **Fix:** Add a header middleware:
  - `X-Frame-Options: SAMEORIGIN`
  - `Content-Security-Policy: frame-ancestors 'self'`, and a full CSP once the inline handlers are gone
  - `X-Content-Type-Options: nosniff`
  - `Referrer-Policy: no-referrer`

### [MEDIUM] Unauthenticated DoS: request sizes are unbounded
- **Where:**
  - `server.py:266-269`: `limit`, `q` and `engine` have no bounds.
  - `:35-47`: `AskBody.question` has no `max_length`.
  - POST bodies have no size limit.
- **What:**
  - Search cost grows with the number of terms, and `limit` is passed straight to SQLite.
  - The whole POST body is read into memory before the sign-in throttle runs.
  - A huge Ask question is paid input tokens. The monthly cap does not account for the call's size.
- **Evidence:**
  - A query with 7,000 terms and `limit=100000` took 0.95 s per request on the tiny sample index.
  - A 100 MB body to `/api/auth/signin` from the LAN raised the server's peak memory from 98 MB to 392 MB before it answered 429.
- **Fix:**
  - Clamp `limit` to 1-50.
  - Cap `q` at about 500 characters and 32 terms, and `engine` at 8 entries.
  - Set `question: constr(max_length=2000)`.
  - Reject request bodies over 64 KB in a middleware.

### [LOW] This PC's file paths are exposed to the LAN
- **Where:** `server.py:258-264` (`/api/vehicles` `file_path`), `:329-346` (`/api/page` `file_path`), `:385-389` (image 404 text), `:370` (`/api/original` error).
- **What:** This contradicts CLAUDE.md ("`/api/stats` hides this PC's paths from others"). On Windows the paths include the user name.
- **Evidence:** From a LAN address, `/api/vehicles` returned `/home/user/AutoRundown/sample_corpus/wis_en`, and `/api/page` returned the page's full path.
- **Fix:** Drop `file_path` from both responses (the UI doesn't use it), and give non-admins a generic 404.

### [LOW] CA name constraints cover only DNS and IP names
- **Where:** `autorundown/tls.py:111-124`.
- **What:** Email and URI names are not constrained, and the CA has no EKU. A leaked `ca.key` could still issue S/MIME or code-signing certificates.
- **Fix:**
  - Add `ExtendedKeyUsage([SERVER_AUTH])` to the CA.
  - Exclude email names, and give the other name types empty permitted sets.
  - Write keys with `os.open(..., 0o600)` instead of writing then running chmod (`tls.py:86-93`, `config.py:41-46`).
  
  Current permissions are fine: the keys are 0600.

### [LOW] Supply chain: unpinned actions and dependencies
- **Where:** `.github/workflows/windows-msi.yml:24,28,154,284,289`; `packaging/windows/build.ps1:36`; `requirements.txt`; `importers/folder.py:32`.
- **What:**
  - Actions are pinned by tag, not commit SHA, and the workflow has no `permissions:` block.
  - The embeddable Python zip is not hash-checked.
  - Every requirement is `>=`.
  - `pypdf` parses untrusted PDFs but is in no requirements file. The installed copy (6.17.0) has 7 known advisories, fixed in 6.19.0.
- **Evidence:** `pip-audit -r requirements.txt` was clean. Auditing the installed packages flagged pypdf 6.17.0.
- **Fix:**
  - Pin actions to commit SHAs and add `permissions: contents: read`.
  - Check the zip's SHA-256.
  - Use a lock or constraints file for the MSI build.
  - Add `pypdf>=6.19` as an optional extra.

### Checked and found fine
- **Admin enforcement from a real LAN address:**
  - All `ADMIN_PREFIXES` routes and `/api/shutdown` answered 403 or 404, including encoded and odd path forms.
  - Spoofed `X-Forwarded-For`, `X-Real-IP`, `Forwarded` and `Host: localhost` headers were ignored (`proxy_headers=False`).
- **Sign-in required mode:** every non-public route answered 401. A signed-in mechanic still got 403 on admin routes. Codes are single-use.
- **Sign-in throttle and cookie:** 10 wrong codes gave 400, then 429. The cookie is HttpOnly, SameSite=lax, and Secure under HTTPS. Only hashes are stored.
- **POST Origin check:** a foreign Origin and `null` were refused. No CORS headers are sent.
- **Path traversal on `/api/image`:** 10 variants and a symlink all answered 404.
- **SQL and search:** all SQL is parameterised, and FTS5 tokens are quoted.
- **Import jobs:** subprocesses run as argument lists with no shell, and their arguments are checked against server-side lists.
- **`/api/original`:** text is escaped, and the `postMessage` listener checks its source.
- **Other UI output:** the job log uses `textContent`, and user, VM and catalogue names go through `esc` inside double-quoted attributes.
- **Secrets:** the API key never appears in `/api/settings`, `/api/stats` or the HTML. Git history scans found false positives only.
