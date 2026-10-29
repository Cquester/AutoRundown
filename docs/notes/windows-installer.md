# Windows installer, CI and releases

<!-- Moved out of CLAUDE.md on 2026-10-12 to keep that file short; the text
is unchanged apart from file paths (tests/, tools/). "Above" / "below" and
section names may refer to CLAUDE.md or another file in docs/notes/. -->

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
- The WIS extractor is **not bundled**, deliberately: it reads the EWA
  Java and `tbjdbc.jar` from the "Mercedes Documents" folder
  (`extractor/wis_paths.py`; Mercedes software, never shipped).
  `importer_jobs.extractor_candidates()`: config `extractor_dir` (Settings
  -> "Mercedes extractor folder"), then the repo's `extractor/` -- from
  source that is all, no fallback to a second copy (they drift). Only the
  installed app, which has no `extractor/`, also tries
  `~/Desktop/Mercedes Documents/autorundown_extractor` and the OneDrive
  Desktop one (the user's copy). The bundle includes `requirements-extractor.txt`
  (Pillow, ezdxf) because the extractor runs under the bundled Python and its
  build step converts images. CI copies `extractor/` to the runner's Desktop
  and checks `/api/vms` finds it.
- Unsigned: SmartScreen warns (README says what to click). Signing needs a
  certificate; nothing in the build does it yet.
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
