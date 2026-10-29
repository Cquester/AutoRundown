# The GitHub repository: protection, Actions, releases

How the repository is meant to be set up once it is public, why each setting
is there, and how to check it. `tools/github_setup.py` reads and sets all of
it through `gh`; this note is the reasoning. (Written 2026-10-29, the user:
"I want to get everything set up before making the website public".)

## State on 2026-10-29 (private)

Read with `python tools/github_setup.py` (it only reads unless `--apply`):

- **Branch protection and rulesets: none, and not available.** GitHub
  refuses them (HTTP 403, "Upgrade to GitHub Pro or make this repository
  public") on a private repository on the free plan. So today `main` is only
  protected by habit: PRs, and the user's OK before a merge.
- Collaborators: only the owner (admin). Nobody else can push or approve.
- **Both workflows were disabled** (`gh workflow disable`, 2026-10-23: the
  private repo hit its Actions billing limit). Nothing ran on a merge, no MSI
  was built and no release was made by CI since then; releases before that
  came from `ci.yml`'s `release` job.
- Actions: all actions allowed; the workflow token read-only; workflows
  cannot approve PRs (those two were already right).

## What `--apply` sets, and why

| Setting | Why |
|---|---|
| Ruleset **main**: changes by pull request; checks "Tests (ubuntu-latest)" and "Browser checks (ubuntu-latest)" must pass; conversations resolved; no force-push; no deletion | `main` is what releases are cut from. **Nobody bypasses it**, the owner included: the checks always run. If CI itself is down, the owner switches the ruleset off for that merge. |
| Ruleset **main reviews**: one approval from a **code owner** (`.github/CODEOWNERS`), re-approved after a new push; only the admin role may bypass, and only through a pull request | The user, 2026-10-29: "only i can merge to main on a pr without reviews. Everyone else needs one." A bypass lifts a whole ruleset, so the review is its own ruleset: the owner skips the review, never the checks. On the website the owner ticks "Merge without waiting for requirements"; with `gh`, `gh pr merge N --merge --admin`. `--approvals N` raises the count. |
| "Only people with write access can approve" | GitHub's own rule: a review from someone without write access never counts toward required approvals. Nothing to set; CODEOWNERS makes it explicit (and only write-access users can be owners). |
| Ruleset **release tags** (`v*`): no deleting, moving or re-pointing | A published version must stay what was published. Remove the ruleset for a moment to retract a bad one. |
| Actions: **only GitHub's own actions** | The workflows use only `actions/*`, pinned to a commit. Nothing else may run. Dependabot moves the pins. |
| Fork pull requests **wait for approval** before a workflow runs ("all outside collaborators") | A stranger's PR must not run code on the runners (or burn minutes) until the owner has read it. The workflows also have no secrets and a read-only token. |
| Workflow token read-only; workflows cannot approve PRs | Only `release` (push to main) asks for `contents: write`, at job level. |
| Secret scanning + push protection | A pasted API key is refused at push. (The history was scanned on 2026-10-29: no real key; only test dummies.) |
| Dependabot alerts, security fixes, weekly update PRs (`.github/dependabot.yml`) | Dependencies and action pins stay current through the same checks as any PR. |
| Private vulnerability reporting + `SECURITY.md` | Reports come privately; the file says what is and is not a vulnerability here. |
| Delete branch on merge; no wiki, no projects | Tidy. |
| Workflows turned back on | CI on every PR and every push to main; the installer on merges to main. |

`--apply` refuses while the repository is private (nothing it sets can be
set then) and exits with a message. After `--apply`, run it again without the
flag: it prints what is still missing.

## Workflows

- **`ci.yml`**: on every pull request (no `paths-ignore` there on purpose: a
  required check that never runs leaves a docs-only PR waiting for it for
  ever) and on every push to main (docs-only pushes skipped) and `v*` tags.
  Jobs: `test`, `browser` (Linux; Windows and macOS legs join automatically
  once the repo is public -- they are **not** required checks, so a runner
  quirk cannot block a merge), `changes` (does a PR touch packaging?), `msi`
  (calls `windows-msi.yml`: on main, tags and PRs that touch packaging) and
  `release`.
- **`windows-msi.yml`**: builds the MSI on a clean Windows runner, upgrades
  over 0.1.1, installs, first-starts it from the Start-menu shortcut in Edge,
  indexes and searches a sample corpus with the *installed* Python, checks
  the shortcut has the app's icon, uninstalls and checks nothing is left.
  Still runnable by hand ("Run workflow").
- **`release`** (only on a push to main, only after `test`, `browser` and
  `msi` passed): publishes **`v<__version__>`** with the MSI and its
  `.sha256`. A version that already has a release is left alone with a
  notice -- bump `autorundown/__init__.py` (its own PR) to publish a new one.
  Merges that do not bump the version still build and check the MSI.
- Artifacts live 3-7 days (`retention-days`); the release keeps the MSI. This
  is what stopped "GitHub's artifact storage kept filling" (2026-10-23).
- **Untested since 2026-10-23**: the workflows were restored from git
  history (`c2939f7^`) on 2026-10-29 with retention days, the icon check and
  the PR trigger changed. Watch the first run after Actions is on; the first
  failure is likelier a workflow slip than an app bug.

## Before making it public (checked 2026-10-29)

- Real keys in the tree or the history: none (only `sk-ant-test-…` style
  dummies in tests). Re-run the same scan before the switch:
  `git log --all -p -G"sk-ant-|AIza|xai-|ghp_|PRIVATE KEY"`.
- Mercedes software and WIS/EPC data are not in the repo (`.gitignore`,
  `extractor/wis_paths.py`); the README GIFs are from the invented sample
  data.
- **Commit e-mails**: two personal addresses were in 216 commits. The user
  chose (2026-10-29) to rewrite them to the no-reply address
  `14364346+Cquester@users.noreply.github.com` before going public. To keep
  them out: `git config --global user.email` is the no-reply address on this
  PC (`github_setup.py` reports it), and on github.com -> Settings -> Emails
  "Keep my email addresses private" and "Block command line pushes that
  expose my email" (account settings: the user's to set). **A force push
  alone does not remove old commits**: GitHub keeps every PR's commits under
  `refs/pull/*`, read-only, so the old e-mails stay visible on the closed
  PRs until the repository is recreated or GitHub Support purges them.
- `LICENSE` is MIT; `SECURITY.md`, `CODEOWNERS` and `dependabot.yml` exist.
