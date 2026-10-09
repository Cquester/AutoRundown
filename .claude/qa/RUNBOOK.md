# QA runbook — shared by the four QA agents

Read this first. It is the same for every agent in `.claude/agents/qa-*.md`.

## Where this runs

In an **isolated environment only**: a Claude Code cloud session or a
Windows Sandbox. Never on the user's own PC with access to their files.
If you can see `~/.autorundown/`, `Desktop/Mercedes Documents/` or an
`index.db` larger than a few MB in the repo root, stop and report that
instead of testing.

## The site under test

`python dev.py --no-browser` — http://localhost:8757, bound to 127.0.0.1.
It builds invented sample data into `.dev/` (gitignored), uses its own
`AUTORUNDOWN_HOME=.dev/home`, and removes `ANTHROPIC_API_KEY` from its
environment. Requests from localhost are **admin** (`autorundown/auth.py`),
so Settings, the Import dialog and `/api/admin/*` are all reachable.

The orchestrator starts it once; check it is up before you begin:

```bash
curl -s http://localhost:8757/api/me
```

If it is not running: `pip install -r requirements.txt -r requirements-dev.txt`,
then `python dev.py --no-browser &` and wait for `/api/me`. Do not start a
second copy. `python dev.py --empty --port 8758 --no-browser` gives the
first-start state if you need it (stop it when done).

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
