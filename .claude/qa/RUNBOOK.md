# QA runbook — shared by the four QA agents

Read this first. It is the same for every agent in `.claude/agents/qa-*.md`.

## Where this runs

The app under test runs in a **test box** (`tools/container/box.sh`,
Docker in WSL): a Linux container holding a copy of the repo at `/work`,
serving `dev.py`'s sample data, with no outbound network and no API key.
You run on the PC, as a subagent of a local session, and work on the box
**from outside only**. The orchestrator starts one box per chat with
`wsl -d Ubuntu -- bash tools/container/box.sh up` and hands you the
`BOX name=... admin=... phone=...` line it printed -- several chats test at
once, so always use **your** box's name and ports, never another's:

- **admin** (e.g. http://localhost:18800): arrives from loopback, so you are
  "this PC" -- Shop setup, the import pages and `/api/admin/*` are reachable;
- **phone** (the admin port + 1, e.g. http://localhost:18801): arrives from
  the network, so you are a mechanic's phone -- no Shop setup, admin pages
  answer 403, sign-in codes apply. Change something as admin (turn on
  "require sign-in", issue a code) and see what the phone side gets; test
  the phone side at 375x812;
- commands inside the box: `... box.sh exec <name> <command>` (e.g.
  `python test_records.py`), and `... box.sh check <name>` for every CI
  check at once;
- files out of the box: `... box.sh fetch <name> <path>` ->
  `.dev/box/<name>/`.

Check your box is up before you begin: `curl -s <admin>/api/me`. If it is
not, say so -- do not start a server on the PC instead, and do not `down`
a box that isn't yours.

On the PC, touch nothing but the box and your own files under `.dev/qa/`.
Never open, list or copy `~/.autorundown/`, `Desktop/Mercedes Documents/`,
`index.db` or anything outside this repo; never run `serve.py`, `dev.py`,
`ingest.py` or the extractor on the PC. Inside the box anything goes: it
is thrown away with `box.sh down`.

## The site under test

`dev.py` in the box: invented sample data (three LEMON vehicles, one
imported; a stand-in WIS corpus with variants and VINs), its own
`AUTORUNDOWN_HOME`, no API key. For the first-start state, start a second,
empty instance inside the box:
`box.sh exec qa bash -c 'AUTORUNDOWN_HOME=/tmp/empty python serve.py
--host 127.0.0.1 --port 8758 &'` and drive it from inside (it is not
published to the PC).

## Browser

Python Playwright (in `requirements-dev.txt`). Write scripts under
`.dev/qa/<your-role>/` (gitignored), never in the source tree.

```python
from playwright.sync_api import sync_playwright
import os
def launch(pw):
    exe = "/opt/pw-browsers/chromium"            # cloud container
    if os.path.exists(exe):
        return pw.chromium.launch(executable_path=exe)
    for ch in ("msedge", "chrome", None):         # Windows Sandbox / other
        try: return pw.chromium.launch(channel=ch)
        except Exception: pass
    raise SystemExit("no Chromium found")
```

If `/opt/pw-browsers/chromium` is a directory, find the `chrome` binary in it.
Save screenshots to `.dev/qa/<role>/shots/`. Test at 1280x800 and 375x812
(phone), light and dark (`color_scheme="dark"`).

## Hard rules

- **Report only. Do not edit any tracked file.** Write only under `.dev/qa/`.
- **Never call `/api/ask` and never enter an API key** in Settings. Block it in
  Playwright: `page.route("**/api/ask", lambda r: r.abort())`. To see answer
  rendering, inject a payload into `renderAnswer()` from the console.
- Imports, removals and settings changes only ever touch `.dev/`. Do not point
  the folder browser or an import at anything outside the repo.
- Do not run `extractor/`, `vm_pipeline.py`, `rebuild_wis.py`, or anything that
  talks to VirtualBox.
- No outbound traffic except package installs. Never fetch lemon-manuals.la.
- Do not delete files outside `.dev/qa/`.

## Background

`CLAUDE.md` is the design record: what is deliberate, what was tried, known
gaps. Read the parts relevant to your role before calling something a bug —
and say so when a finding contradicts a recorded decision rather than
silently disagreeing. The whole UI is one file, `autorundown/web/index.html`;
the server is `autorundown/server.py`, access control `autorundown/auth.py`.

## Report format (your final message)

Findings only, most severe first, each as:

```
### [CRITICAL|HIGH|MEDIUM|LOW] <short title>
- Where: <file:line> and/or <screen / URL / endpoint>
- What: <what is wrong, concretely>
- Evidence: <how you saw it: command + output, screenshot path, measurement>
- Fix: <specific suggested change>
```

Severity: CRITICAL = data loss, spend without consent, auth bypass, secret
exposure; HIGH = a main flow broken or wrong-vehicle data shown, or a likely
exploitable issue; MEDIUM = degraded but workable, clear usability or
performance problems; LOW = polish. Mark anything you could not reproduce as
"unverified". End with a short list of what you checked and found fine.
