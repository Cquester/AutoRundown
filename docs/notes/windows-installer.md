# Windows installer, CI and releases

<!-- Moved out of CLAUDE.md on 2026-10-12 to keep that file short; the text
is unchanged apart from file paths (tests/, tools/). "Above" / "below" and
section names may refer to CLAUDE.md or another file in docs/notes/. -->

**A headless Linux server** (2026-11-06, the user: "turning my desktop into
a ubuntu server with no ui. Everything is ssh") has no package. It runs from a git
checkout through `packaging/linux/install.sh`: a venv in the checkout, the
app's and the extractor's requirements, and `autorundown.service` as a
systemd *user* unit (VirtualBox keeps VMs per user, so the app must run as
the VM's owner; lingering starts it at boot). The script never runs `sudo`.
It names the apt packages to install (`python3-venv`; optionally
`virtualbox`, `openjdk-8-jre-headless`). Admin is over `ssh -L
8756:localhost:8756` (loopback peer + Host, unchanged). Off Windows:
- the Mercedes folder is `~/.local/share/AutoRundown/Mercedes data`
  (`wis_paths.installed_home`, `XDG_DATA_HOME`)
- Browse falls back to the page's own list
- "Delete all Mercedes data" uses the freedesktop Trash
- there is no tray, autostart or MSI
- libtorrent (the hidden CHARM card) is not installed

`wis_paths.REPO` also counts a copy with `tests/` and no `.git` (a test
box's snapshot) as a checkout. Before that, `test_vm_jobs.py` failed in
every box. Checked: `tests/test_linux_server.py` (on Windows and WSL).

**`tools/container/server_test.sh`** runs `install.sh` end to end on a
stand-in server. The server is a Docker Ubuntu (26.04, or `--ubuntu 24.04`)
with systemd as PID 1, sshd and a user `shop` with no sudo. The test
covers:
- **a run with no systemd session** (`sudo -u`): it stops before any work
  and says to sign in over SSH
- **the real run over SSH**: the service is active, the app answers, and
  its log is in the journal
- **admin**: through `ssh -L`, `/api/settings` gives 200; from the
  published port (a phone) it gives 403
- **lingering, then a container restart**: the app answers with nobody
  signed in, running as `shop`
- **a second run (an update)**: one service, still answering

It needs network (apt, pip), so it runs outside the test boxes. It found
that `systemctl --user` fails with a raw dbus error when there is no
session (an SSH login without `libpam-systemd`, `su`, or `sudo -u`). Now
`install.sh` checks that first, uses `/run/user/<uid>` when it is there,
and explains the fix. Still not tried on a real machine: VirtualBox and
the VM pull on Linux.

`packaging/windows/build.ps1` + `AutoRundown.wxs` (WiX 5) build a **per-user
MSI** to `%LOCALAPPDATA%\Programs\AutoRundown`: no UAC prompt, and Python
can write there.

**2026-10-29: CI builds it again, for the public repo** (the user: "main
merges are creating msi and creating new releases"): `ci.yml` calls
`windows-msi.yml` on main, tags and PRs that touch packaging, and its
`release` job publishes `v<__version__>` with the MSI. Artifacts live 3-7
days. Both workflows stay disabled until the repo is public
(`tools/github_setup.py --apply`, `github-repo.md`); the paragraphs below
say "CI builds nothing" for the stretch 2026-10-23 to 2026-10-29. The PC
still builds and installs its own after a merge.

**The app's icons** (`tools/make_icons.py`, drawn from code into
`autorundown/web/icons/`: a hex nut on the tray's amber): the MSI's
`<Icon>` gives the Start-menu, desktop and Startup shortcuts and
"Apps & features" the `.ico`; `autostart.py` sets it on the shortcut the
Settings toggle writes; the tray (`launcher._tray_image`) uses it; the
browser tab and home screen get `/favicon.ico` and `/icons/` (public paths:
the sign-in screen has a tab icon). `windows-msi.yml` checks the file is
installed and the Start-menu shortcut has an icon.

**The MSI was built on the PC, not in CI, 2026-10-23 to 2026-10-29** (the user: "let not
build msi in the workflow. All claude sessions upon a successful merge to
main, will run their version of msi locally"). After its PR is merged to
`main`, a session builds and installs it here:
`$env:PATH="$env:USERPROFILE\.dotnet\tools;$env:PATH"; powershell
-ExecutionPolicy Bypass -File packaging\windows\build.ps1`, then
`msiexec /i dist\AutoRundown-<ver>.msi AUTOSTART=1 /passive`, then start
the Start-menu shortcut if :8756 is not answering. Build it from an
up-to-date `main`, not the PR branch. Python for the build must be the
same version as the bundled one (3.12.10 on this PC). CI no longer
builds it, so nothing published a release after that date (below).
Before then, and still when run by hand ("Run workflow" in the Actions tab),
`.github/workflows/windows-msi.yml` builds it on
`windows-latest`, installs it silently, indexes the sample corpus with the
*installed* Python, serves and searches it, launches a second time, then
uninstalls and checks nothing is left. Before that it does a real first
start: opens the installed Start-menu shortcut (no index) and runs
`packaging/windows/ui_check.py` in Edge -- first-run import dialog, import
job to "Finished" (ingest.py as a child of the bundled Python), car box,
keyword search, page view, phone width; free, `/api/ask` is blocked. Works
locally too (`--executable` for a Chromium binary). Uploads the MSI and
`ui-screenshots` (a cloud session cannot download artifacts: the blob host
is blocked; read the step's "ok" lines in the job log instead). Not covered by CI
(needs a person): SmartScreen, the firewall prompt, Windows 11 desktop.
macOS/Linux run from source (README), by the user's decision.

- **Embeddable Python, not PyInstaller/Briefcase.** Import jobs re-run
  `ingest.py` / `rebuild_wis.py` / `vm_pipeline.py` with `sys.executable`,
  and a frozen exe would launch itself. The bundle is the official
  `python-<ver>-embed-amd64.zip` (same version as the build's Python, so pip
  wheels match) + `pip install --target Lib/site-packages` + the source.
- **`python3XY._pth` replaces sys.path** and stops Python adding a script's
  own folder. Its `..\app` line is what lets `python ingest.py` import
  `autorundown`. Anything the installed app imports must live in `app\`.
- **User data is outside the install** (`~/.autorundown`, `config.py`), so
  upgrade and uninstall never touch the index or the spend log.
  `rebuild_wis.py --index` defaults to the configured index; before
  2026-10-08 it used the repo folder, so a dialog rebuild ignored the served
  index. (Its old pre-WIS backup `index.before-wis.db`, next to the index,
  is no longer used since 2026-10-16: the rebuild is in place.)
- `autorundown/launcher.py` is the shortcut target. The MSI shortcuts run
  `pythonw -m autorundown.launcher --background`: no console, output to
  `~/.autorundown/autorundown.log`, a pystray tray icon (Open / phone
  address / Start when I sign in / Open log / Stop); without a tray it serves
  headless. Console mode (default from source) is the old behaviour. If one
  is already running it only opens the browser; `--stop` POSTs
  `/api/shutdown` (localhost only). A failed start in the background shows a
  message box, since there is no console.
- **The wizard asks** (`AutoRundownUI` in the .wxs, WiX UI extension):
  Welcome -> Options ("Start AutoRundown when I sign in", property
  `AUTOSTART`) -> Ready -> Finish with "Launch AutoRundown" ticked. No
  licence page (WixUI_Minimal's was dropped). The Options box installs the
  `Autostart` component: the same Startup-folder shortcut `autostart.py`
  writes, so Settings can still turn it off. Default: on for a first install
  and for upgrades from versions that never asked (0.1.x <= 0.1.2); after
  that an upgrade keeps the current state (`autostart_asked` registry value +
  a search for the shortcut). Silent installs: on, `AUTOSTART=0` for off. The
  user expected the installer to leave it running -- 0.1.2 only added
  shortcuts, so nothing ran until clicked. CI reads the dialog wiring from
  the MSI tables (no one clicks the wizard there).
- **Not a Windows service, deliberately** (the user asked for one): a service
  needs admin and runs as SYSTEM, which can't see the user's VirtualBox VMs
  or `~/.autorundown`. `autorundown/autostart.py` writes a Startup-folder
  shortcut instead (Settings checkbox and tray item; Windows only, localhost
  only); the MSI removes it on uninstall, and its `StopRunning` custom action
  runs `--stop` before upgrade/uninstall so files aren't in use.
- **`autorundown.local`**: `autorundown/mdns.py` (zeroconf) registers an
  `_http._tcp` service whose host is `autorundown.local.`, so the PC answers
  that name itself -- the user wanted a phone bookmark that survives IP
  changes without router setup. Best effort: any failure is logged and the
  server runs on; Settings shows `phone_urls` (name + numeric). Two PCs
  running it would contend for the name.
- Version: `autorundown/__init__.py` `__version__`; bump it per release (MSI
  MajorUpgrade needs a higher version to replace an install).
- **Both workflows are disabled on GitHub since 2026-10-23** (`gh workflow
  disable`; the private repo hit its Actions billing limit, and the user
  will turn them back on when the repo goes public: `gh workflow enable CI`).
  Until then the same checks run locally, `box.sh check <name>` in a test
  box. What follows describes the workflows as they run when enabled.
- **CI is `.github/workflows/ci.yml`** (2026-10-11, the user's design):
  every PR runs lint (`ruff --select F`), `tools/check_js.py`, the free
  suites and a sample index served and searched (job `test`), and in
  parallel `ui_check.py` and the Playwright tests (job `browser`) -- on
  Linux; Windows and macOS legs join by
  themselves once the repo is public (the user plans to make it public;
  while private, Windows minutes count 2x and macOS 10x of 2,000/month,
  and required checks are not available). **Until 2026-10-23** `main` and
  tags also ran `windows-msi.yml` at the same time as the tests (and PRs
  that touched packaging, via a `changes` job), and a `release` job
  published the result; both jobs are gone from `ci.yml` (the user's
  decision: GitHub's artifact storage kept filling, and each run cost
  Windows minutes). `windows-msi.yml` is now "Run workflow" by hand only.
  Also 2026-10-16: the 0.1.1 installer for the upgrade test is built once
  and cached (`actions/cache`, key `msi-0.1.1-…`), and "Start at sign-in"
  installs only `AUTOSTART=0` -- the default-on case is the upgrade step's
  (uninstall removes `autostart_asked`, so a first install takes the same
  `SetProperty` branch as an upgrade from 0.1.1). `claude/**` pushes
  build nothing: open a PR. No secret is used; `ANTHROPIC_API_KEY` is forced
  empty and `ANTHROPIC_BASE_URL` points at a closed port.
  **Add a new free test to the "Test suites" loop in `ci.yml`** (a browser
  test: a step in the `browser` job, passing `--index` the job's
  sample index -- building its own cost `test_car_history` ~55 s).
- **Releases were published by CI until 2026-10-23** (`ci.yml`'s `release`
  job, from 2026-10-09: the MSI and its `.sha256` as a GitHub release
  `v<__version__>`, never overwriting one). That job is gone with the MSI
  build; nothing publishes a release now. If one is wanted, it would be
  `gh release create v<ver> dist\AutoRundown-<ver>.msi …` from the PC's
  build, and only when the user asks. In a hand-run `windows-msi.yml` the
  artifact uploads are `continue-on-error`: a full artifact quota (it
  happened on PR #22) had skipped the smoke, uninstall and autostart steps.
- The WIS extractor **is bundled** (2026-10-31; before, a new PC failed
  with "vm_pipeline.py was not found" though the user had chosen a VM):
  `build.ps1` copies `extractor/*.py` and `*.js` into `app\extractor`. What
  it runs -- the EWA Java and `tbjdbc.jar` -- is Mercedes software and never
  ships: `vm_pipeline.copy_ewa_from_vm` copies them out of the chosen VM on
  the first run (`importing.md`). In the app, `wis_paths.home()` is
  `%LOCALAPPDATA%\AutoRundown\Mercedes data` (`installed_home`; under
  `AUTORUNDOWN_HOME` when set), never the app's program folder: uninstalling
  must not take the user's extraction with it (the user, 2026-10-31, chose
  "keep it, add a button": Shop setup's "Delete all Mercedes data"). Until
  0.1.10 it was `Desktop\Mercedes Documents`: the user found a 64 GB folder
  on the Desktop showing their own name, and OneDrive syncs a Desktop. As
  the server starts, `importer_jobs.move_mercedes_home` renames that folder
  to the new place (instant on one drive) and repoints `vehicles.file_path`;
  if the rename fails (another drive, a file open) the old folder stays in
  use (`wis_paths.home` prefers it until the new one exists).
  `importer_jobs.extractor_candidates()`: config `extractor_dir` (Settings
  -> "Mercedes extractor folder"), then `extractor/` beside the app (the
  repo's, or the bundled one) -- no fallback to a second copy (they drift).
  Only an older MSI, with no `extractor/`, still tries
  `~/Desktop/Mercedes Documents/autorundown_extractor` and the OneDrive
  Desktop one. The bundle includes `requirements-extractor.txt` (Pillow,
  ezdxf) because the extractor runs under the bundled Python and its build
  step converts images. CI checks `/api/vms` uses the bundled extractor and
  that it would write to `...\Mercedes data\autorundown_out`, and that
  `/api/me` offers Windows' own file dialog (`native_dialog.py`:
  PowerShell's Windows.Forms Open / folder dialogs, run by the server on the
  PC; a page cannot learn a picked file's path, so the browser's own file
  input could not do this).
- **Code signing: SignPath Foundation, wired up, waiting on approval**
  (2026-11-09, the user chose it: free for open source; a paid certificate
  and Azure Trusted Signing were the alternatives). Until it is approved and
  set up, releases are unsigned and SmartScreen warns (README says what to
  click). See "Code signing" below.

- **The embeddable Python is verified** (2026-10-09): `build.ps1` checks
  python.org's Sigstore signature on the download against python.org's
  release signers (`$signers`; 3.13 is thomas@python.org via Google -- the
  first CI run assumed the Windows signer and failed). A new signer fails
  the build with sigstore naming who signed: add that line. `-NoVerify`
  skips it.
- **Every script the app runs as a child must be in `build.ps1`'s file
  list** (`serve.py`, `ingest.py`, `ask.py`, `rebuild_wis.py`,
  `remove_manuals.py`). `remove_manuals.py` was missing until 2026-10-12, so
  Shop setup's "Remove selected" failed in the installed app;
  `windows-msi.yml` now checks those files are installed.

### Code signing

`ci.yml`'s `sign` job sits between the checks and `release`, on a push to
main only. It sends the `AutoRundown-msi` artifact the installer job tested
(`windows-msi.yml` exposes its id as `msi-artifact-id`) to SignPath, waits
up to 2 h for the request to be **approved by hand on signpath.io**, checks
the result with `Get-AuthenticodeSignature` (must be `Valid`, larger than
the unsigned file) and uploads `AutoRundown-msi-signed`, which `release`
then publishes. It signs only a version that has no release yet, so a merge
that doesn't bump `__version__` asks for no approval.

- **Off until set up**: the job runs only when the repository variable
  `SIGNPATH_ORGANIZATION_ID` is set. Without it `sign` is skipped and
  `release` publishes the unsigned MSI, as before.
- **Once on, a failed or unapproved signing publishes nothing** (never an
  unsigned MSI by accident). Approve the request, or re-run the failed jobs.
- Not tried yet: no run has reached SignPath (Actions is off while private).
  Watch the first one.

Setting it up (the owner; needs the repo public and Actions on):
1. Apply at signpath.org (SignPath Foundation, "Apply"). They want: an OSI
   licence (MIT), a public repo, builds on GitHub-hosted runners, the
   README's "Code signing policy" section (attribution line, team roles,
   privacy policy -- already in), and MFA on the GitHub and SignPath
   accounts. They review the project, so approval can take a while; they
   may decline. Risk: the README names LEMON and CHARM archives; the "Legal"
   section is what answers that.
2. When approved, in SignPath: link the project to this GitHub repo
   (trusted build system "GitHub.com"), and set the artifact configuration
   to the uploaded zip with the MSI inside:
   ```xml
   <artifact-configuration xmlns="http://signpath.io/artifact-configuration/v1">
     <zip-file>
       <msi-file path="AutoRundown-*.msi">
         <authenticode-sign />
       </msi-file>
     </zip-file>
   </artifact-configuration>
   ```
   Create a CI user and copy its API token.
3. On GitHub (Settings > Secrets and variables > Actions): secret
   `SIGNPATH_API_TOKEN`; variable `SIGNPATH_ORGANIZATION_ID`; and only if
   SignPath's names differ from the defaults, variables
   `SIGNPATH_PROJECT_SLUG` (default `AutoRundown`) and
   `SIGNPATH_POLICY_SLUG` (default `release-signing`).
4. Bump `__version__`, merge, approve the request on signpath.io, and check
   the release's MSI shows the publisher (right-click > Properties > Digital
   Signatures). Then drop "(Applied for …)" from the README.

The signed MSI says "SignPath Foundation" as publisher, not the user's name
(their certificate). SmartScreen's "Windows protected your PC" can still
show for a new release until it has a download reputation; signing removes
"Unknown publisher" and builds that reputation across versions.
