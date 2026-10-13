# Test environments: Windows Sandbox and test boxes

<!-- Moved out of CLAUDE.md on 2026-10-12 to keep that file short; the text
is unchanged apart from file paths (tests/, tools/). "Above" / "below" and
section names may refer to CLAUDE.md or another file in docs/notes/. -->

**A fresh install, isolated: `tools/sandbox/start.ps1`** (2026-10-11, the
user's choice over WSL/Hyper-V/Docker). Windows Sandbox (Windows 11 Pro;
the feature must be turned on once, elevated, by the user -- the script
prints the command) gets an MSI (`-Msi`, `-Run <CI run id>`, `-Release`,
else the newest `dist\*.msi`, else the latest release), installs it like
a user (`AUTOSTART=0`), starts it from the Start-menu shortcut, and the
PC's browser reaches it at **http://localhost:18756 as admin** (Shop
setup works); `http://<sandbox IP>:8756` is the mechanic/phone view.
`-Source` lays this checkout's code (uncommitted too) over the installed
app -- the way to try a branch without building an MSI; it deletes the
MSI's `unchecked-hash` .pyc, or the old code would keep running. Admin
needs loopback peer AND loopback Host, so `forward.py` runs twice: in the
sandbox `<ip>:18756 -> 127.0.0.1:8756`, on the PC `127.0.0.1:18756 ->
<ip>:18756`. The sandbox sees only `.sandbox\in` (read-only: MSI, scripts,
sample manuals, copied to its Desktop), `-Manuals <dir>` read-only at
`C:\ar\manuals`, and writes only `.sandbox\out` (`inside.log`,
`ready.json`/`failed.json`). Closing the window discards everything. One
sandbox at a time (a Windows limit). It has no API key unless one is typed
into its Shop setup -- that spends for real. The forwarder chain was
checked against `dev.py` (admin true, POST 200). **First real run,
2026-10-11** (v0.1.3 release MSI): installed and served inside in ~4 min,
but the PC could ping the sandbox and not connect -- Windows had added
inbound *block* rules for the bundled `pythonw.exe` (the unanswered
first-listen firewall prompt), which beat the port allow rule.
`inside.ps1` now adds program allow rules before the app starts and
removes any AutoRundown block rule; `start.ps1` checks `/api/me` answers
as admin through the forwarder and warns if not. Debug a running sandbox
with `wsb list` / `wsb exec --id <id> -r System -c "<cmd>"` (Windows 11
24H2+), writing output to `C:\ar\out`.

**Test boxes: the app in a container, tested from outside**
(`tools/container/`, 2026-10-11/12; the user chose Docker Engine inside
WSL2 over Docker Desktop -- Ubuntu 26.04, Docker 29.9, the user is in the
`docker` group so no sudo). **A box is the program, not Claude Code** (the
user's rule, 2026-10-12: no sign-in per box, nothing of theirs inside).
`box.sh build` makes the image `autorundown-test` (Python 3.13, the app's
requirements, ruff, Playwright Chromium, Node for check_js; 2.3 GB, ~2.5
min). `box.sh up [name] [--port N]` copies the working tree **as it is
now** (uncommitted too; no .git, indexes, samples, logs, or the other
sessions' `.claude/worktrees/`) into a new container `ar-<name>` and
starts it in ~11 s: `init-firewall.sh` drops **all** outbound traffic
(proves example.com fails), then `dev.py` serves sample data and
`tools/sandbox/forward.py` makes the published port (`127.0.0.1:N` in WSL,
`localhost:N` from Windows) arrive from loopback, so **the user's browser
is admin there**. **Each box has two addresses** (the user's
request, 2026-10-12): admin on N and **phone on N+1** -- `dev.py --lan`
binds 0.0.0.0 and Docker publishes 8757 directly, so those requests come
from Docker's gateway, not loopback: not admin, admin pages 403, paths
hidden, sign-in applies (checked: sign-in turned on at N locks N+1 out,
401). **Several chats at once:** `up` takes the next free pair from 18800,
checked in Docker (label `ar.ports`), WSL (`ss`) and Windows (`netstat`),
and names an unnamed box after its port (`box-18800`); `docker create`
with that name is the lock, so simultaneous `up`s can't share a pair, and
a port that fails at `docker start` moves on to the next pair. Checked:
three `up`s at the same moment got 18802/04/06, a Windows program on
18810 was skipped, an existing name is refused. `up` ends with a
machine-readable `BOX name=… admin=… phone=…` line; `list` shows both
addresses per box. `check` runs every CI check inside (`check.sh`: ruff,
check_js, every `test_*.py`, `tests/test_ui_picks.py` on the box's index,
`ui_check.py` on a second empty instance; ~35 s, all pass); `exec`, `fetch`
(to `.dev/box/<name>/`), `list`, `down`. **Agents run on the PC** as
subagents of a local session and work on the box only through its URL and
`box.sh exec` (`.claude/qa/RUNBOOK.md`). Claude Code inside a box was tried
and dropped: it needs the CLI's own sign-in (`~/.claude/.credentials.json`,
separate from the desktop app's, and expired since 2026-10-08), which the
user did not want per box. **WSL stops its VM a few seconds after the last
Windows command ends**, and Docker with it -- the user found the box
"not running" (Exited 255). Boxes are created `--restart unless-stopped`
(back by themselves in ~1 s when WSL starts; `entrypoint.sh` clears
`/tmp/ready` first), and while someone is using one, a session keeps WSL
up with a background `wsl.exe -d Ubuntu --exec sleep infinity`. The
permanent fix is `vmIdleTimeout=-1` under `[wsl2]` in the user's
`%USERPROFILE%\.wslconfig` -- their setting to change, not ours.
A box tests a **snapshot**: after changing code,
`down` and `up` again. Boxes run from the main folder (the user's
preference over a separate worktree folder). Python on Windows writes CRLF:
edit the `.sh` files with the Write/Edit tools, or bash fails on `\r`
(`.gitattributes` keeps them LF in git). The first `ui_check.py` run in a
box found a real race (below, "Importing from the site"). Windows Sandbox
stays for installer/release checks and for the user clicking through the
real Windows app.
