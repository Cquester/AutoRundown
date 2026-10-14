# Test boxes

A test box is AutoRundown running in a throwaway Linux container (Docker
Engine inside WSL2 on the user's PC): a copy of this repo, serving invented
sample data, with **no outbound network and nothing of the user's** -- not
their files, index, settings, spend log or Claude sign-in. Ask works: a fake
Anthropic API inside the box (`tools/fake_anthropic.py`, via `dev.py
--fake-api`) answers it from the passages the app sends, so the whole answer
path runs for free. Its answers start "[fake API]".
Use one whenever you want to test "like a user", let an agent loose on the
app, or show the user a change.

Run everything from Windows with `wsl -d Ubuntu -- bash tools/container/box.sh …`
(from Git Bash, prefix `MSYS_NO_PATHCONV=1` so paths aren't rewritten).

## The loop

```bash
box.sh up                      # your own box; prints its name + two addresses
box.sh check <name>            # every CI check inside it (~35 s)
box.sh exec <name> <command>   # run something inside it
box.sh fetch <name> <path>     # copy a file/folder out to .dev/box/<name>/
box.sh down <name>             # when you are finished -- your box only
box.sh list                    # every box, with both addresses
```

`up` ends with one machine-readable line -- keep it, it is how you (and any
agent you start) find your box:

```
BOX name=box-18800 admin=http://localhost:18800 phone=http://localhost:18801
```

| Address | Who the app thinks you are |
|---|---|
| **admin** (`N`) | "This PC": Shop setup, imports, admin pages, settings |
| **phone** (`N+1`) | A mechanic's phone on the shop network: no Shop setup, admin pages 403, sign-in applies |

Both addresses work in the user's own browser on this PC, and in this app's
browser pane. Change something as admin (turn on "require sign-in", issue a
code) and look at what the phone side gets; test the phone side at 375x812.

## Rules

- **One box per chat, and only yours.** Several chats test at once: `up`
  picks free ports and, without a name, names the box after its port. Never
  `down` or `exec` into a box you didn't start; never `down --all` unless
  the user asks.
- **A box is a snapshot.** It holds the working tree as it was at `up`
  (uncommitted changes included). After changing code: `down`, then `up`.
- **Rebuild the image** (`box.sh build`, ~2.5 min) after changing
  `requirements*.txt` or anything in `tools/container/`. Code changes don't
  need it.
- **Agents stay on the PC** and work on the box only through its addresses
  and `box.sh exec`; QA agents follow `.claude/qa/RUNBOOK.md`. Give them the
  `BOX …` line.
- **Free.** Nothing in a box can reach Anthropic, so nothing spends; the
  "$ this session" figure is the fake's made-up token count. Don't type an
  API key into a box's Shop setup (it would still go only to the fake).
- **`entrypoint.sh` and the `Dockerfile` are baked into the image**: after
  changing either, `box.sh build` before `up`.
- **WSL sleeps** a few seconds after the last command from Windows, and
  Docker with it; boxes come back by themselves when WSL starts. While you
  (or the user) are using one, keep WSL up with a background
  `wsl.exe -d Ubuntu --exec sleep infinity`, and stop it when done.
- **Edit the `.sh` files with the Write/Edit tools.** Python on Windows
  writes CRLF, and bash then fails on `\r`.

## Not for

- **The Windows installer** (wizard, shortcuts, tray, SmartScreen): that is
  `tools/sandbox/start.ps1`, a Windows Sandbox.
- **The real manuals** (`eval.py`, `eval_wis.py`, the real index): those run
  on the PC against the user's data, read-only.
- **A real phone:** the phone address is on this PC only; reaching a box
  from the shop Wi-Fi needs WSL's networking changed, which is the user's
  call.

## Files

`Dockerfile` (the image), `entrypoint.sh` (firewall, then `dev.py --lan --fake-api` +
`forward.py`), `init-firewall.sh` (drops all outbound traffic, proves it),
`check.sh` (what `box.sh check` runs), `box.sh` (everything else). The
design notes and what was measured are in CLAUDE.md, "Test boxes".
