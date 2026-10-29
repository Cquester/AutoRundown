"""Check (and with --apply, set) the GitHub repository's protection -- FREE.

    python tools/github_setup.py                    # report: what is set, what is missing
    python tools/github_setup.py --apply            # set it (needs `gh` signed in as an admin)
    python tools/github_setup.py --apply --approvals 1   # a second maintainer: one approval to merge

Run it once when the repository goes public (branch rulesets, secret
scanning and fork-PR approval are not available on a private repository on
the free plan, so before that it can only report) and again whenever
something looks off. It never touches files or history; it only calls the
GitHub API through `gh`. See docs/notes/github-repo.md for what each setting
is for.

What it sets:
  - a ruleset on main: changes arrive by pull request, the Linux test and
    browser checks must pass, conversations resolved, no force-push, no
    deletion. Reviews from anyone without write access never count toward the
    approvals (GitHub's rule). The owner (admin role) can bypass through a
    pull request if CI itself is down;
  - a ruleset on v* tags: no deleting or moving a released version;
  - Actions: only GitHub's own actions, a read-only token, fork pull requests
    wait for approval before a workflow runs, no workflow approves PRs;
  - secret scanning with push protection, Dependabot alerts and security
    fixes, private vulnerability reporting, branches deleted after a merge,
    no wiki or projects;
  - the CI and Windows installer workflows turned back on.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

CHECKS = ["Tests (ubuntu-latest)", "Browser checks (ubuntu-latest)"]
TOPICS = ["repair-manual", "service-manual", "sqlite", "fts5", "fastapi", "automotive", "offline-first"]
DESCRIPTION = "Local repair-manual search over your own service-manual archive: SQLite full-text search, cited AI answers, phone-friendly."
RULESETS = ("main", "release tags")


def gh(*args: str, body: dict | None = None, check: bool = True) -> tuple[int, str]:
    r = subprocess.run(["gh", *args], input=json.dumps(body) if body is not None else None,
                       capture_output=True, text=True)
    if check and r.returncode:
        print(f"  gh {' '.join(args[:3])} ... failed: {(r.stderr or r.stdout).strip()[:300]}")
    return r.returncode, r.stdout


def api(method: str, path: str, body: dict | None = None, check: bool = True):
    args = ["api", "-X", method, path] + (["--input", "-"] if body is not None else [])
    rc, out = gh(*args, body=body, check=check)
    try:
        return rc, (json.loads(out) if out.strip() else {})
    except ValueError:
        return rc, {}


def rulesets(approvals: int) -> list[dict]:
    return [
        {"name": "main", "target": "branch", "enforcement": "active",
         "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
         "bypass_actors": [{"actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request"}],
         "rules": [
             {"type": "deletion"}, {"type": "non_fast_forward"},
             {"type": "pull_request", "parameters": {
                 "required_approving_review_count": approvals, "dismiss_stale_reviews_on_push": True,
                 "require_code_owner_review": approvals > 0, "require_last_push_approval": False,
                 "required_review_thread_resolution": True}},
             {"type": "required_status_checks", "parameters": {
                 "strict_required_status_checks_policy": False,
                 "required_status_checks": [{"context": c} for c in CHECKS]}},
         ]},
        {"name": "release tags", "target": "tag", "enforcement": "active",
         "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
         "bypass_actors": [],
         "rules": [{"type": "deletion"}, {"type": "update"}, {"type": "non_fast_forward"}]},
    ]


def report(repo: str) -> int:
    """Read every setting this script sets; print ok / MISSING; the count missing."""
    miss = 0

    def line(ok: bool, what: str) -> None:
        nonlocal miss
        miss += not ok
        print(f"  {'ok     ' if ok else 'MISSING'} {what}")

    _, info = api("GET", f"repos/{repo}")
    print(f"{repo}: {info.get('visibility')} ({'public' if info.get('visibility') == 'public' else 'private: rulesets and secret scanning need a public repository or a paid plan'})")
    line(bool(info.get("delete_branch_on_merge")), "branches are deleted after a merge")
    line(not info.get("has_wiki") and not info.get("has_projects"), "no wiki or projects")
    sa = info.get("security_and_analysis") or {}
    line((sa.get("secret_scanning") or {}).get("status") == "enabled", "secret scanning")
    line((sa.get("secret_scanning_push_protection") or {}).get("status") == "enabled", "push protection for secrets")
    rc, rs = api("GET", f"repos/{repo}/rulesets", check=False)
    have = {r["name"]: r for r in rs} if rc == 0 and isinstance(rs, list) else {}
    for name in RULESETS:
        line(name in have and have[name].get("enforcement") == "active", f"ruleset '{name}' active")
    _, ap = api("GET", f"repos/{repo}/actions/permissions", check=False)
    line(ap.get("allowed_actions") == "selected", "Actions: only GitHub's own actions")
    _, wp = api("GET", f"repos/{repo}/actions/permissions/workflow", check=False)
    line(wp.get("default_workflow_permissions") == "read" and wp.get("can_approve_pull_request_reviews") is False,
         "Actions: read-only token, workflows cannot approve PRs")
    _, fp = api("GET", f"repos/{repo}/actions/permissions/fork-pr-contributor-approval", check=False)
    line(fp.get("approval_policy") in ("all_external_contributors", "first_time_contributors_new_to_github"),
         f"fork pull requests wait for approval ({fp.get('approval_policy', 'not readable')})")
    _, wf = api("GET", f"repos/{repo}/actions/workflows")
    for w in wf.get("workflows", []):
        line(w["state"] == "active", f"workflow '{w['name']}' is on ({w['state']})")
    rc, _ = api("GET", f"repos/{repo}/vulnerability-alerts", check=False)
    line(rc == 0, "Dependabot alerts")
    return miss


def apply(repo: str, approvals: int) -> None:
    _, info = api("GET", f"repos/{repo}")
    if info.get("visibility") != "public":
        print("The repository is private: rulesets, secret scanning and fork-PR settings are refused by GitHub "
              "until it is public (or on a paid plan). Make it public first, then run this again.")
        raise SystemExit(2)
    print("repository")
    gh("repo", "edit", repo, "--delete-branch-on-merge", "--enable-wiki=false", "--enable-projects=false",
       "--description", DESCRIPTION, *[a for t in TOPICS for a in ("--add-topic", t)])
    api("PATCH", f"repos/{repo}", {"security_and_analysis": {
        "secret_scanning": {"status": "enabled"},
        "secret_scanning_push_protection": {"status": "enabled"}}})
    api("PUT", f"repos/{repo}/vulnerability-alerts")
    api("PUT", f"repos/{repo}/automated-security-fixes")
    api("PUT", f"repos/{repo}/private-vulnerability-reporting")
    print("actions")
    api("PUT", f"repos/{repo}/actions/permissions", {"enabled": True, "allowed_actions": "selected"})
    api("PUT", f"repos/{repo}/actions/permissions/selected-actions",
        {"github_owned_allowed": True, "verified_allowed": False, "patterns_allowed": []})
    api("PUT", f"repos/{repo}/actions/permissions/workflow",
        {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": False})
    api("PUT", f"repos/{repo}/actions/permissions/fork-pr-contributor-approval",
        {"approval_policy": "all_external_contributors"})
    print("rulesets")
    _, existing = api("GET", f"repos/{repo}/rulesets")
    ids = {r["name"]: r["id"] for r in existing} if isinstance(existing, list) else {}
    for rs in rulesets(approvals):
        if rs["name"] in ids:
            api("PUT", f"repos/{repo}/rulesets/{ids[rs['name']]}", rs)
        else:
            api("POST", f"repos/{repo}/rulesets", rs)
    print("workflows")
    for name in ("CI", "Windows installer"):
        gh("workflow", "enable", name, "--repo", repo)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default="Cquester/AutoRundown")
    ap.add_argument("--apply", action="store_true", help="set everything (default: only report)")
    ap.add_argument("--approvals", type=int, default=0,
                    help="approvals a PR needs (0 for one maintainer; with 1 the code owner must approve)")
    args = ap.parse_args()
    if args.apply:
        apply(args.repo, args.approvals)
        print()
    missing = report(args.repo)
    print(f"\n{'everything is set' if not missing else f'{missing} setting(s) missing'}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
