#!/usr/bin/env python3
"""Keep open bot-authored PR branches current with main.

When one PR merges, every other open PR falls behind main, and the merge ruleset will not let a branch that is
behind merge. Someone then had to bring each one up to date by hand, and re-run CI on it, every time. This does it
for the PRs the bot itself opened: on each push to main it asks GitHub to merge main into each such branch that is
behind (the update-branch call), which also starts CI on the new head.

It only touches pull requests that were opened by the bot, from a branch in this repository, against main, and
that are not drafts. It never reads or runs anything from a pull request: it makes API calls and nothing else.
A conflict is reported and left alone. A merge commit made this way carries no sign-off, which is fine: intake
counts authored commits only (docs/specs/contribution-policy-refinements.md, R4).

Env: GH_TOKEN (the bot App's token, so that CI starts on the update), GITHUB_REPOSITORY (owner/name).
Prints one line per pull request: current, synced, conflict or failed.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
BOT = "onehill-dev-agent[bot]"


def candidates(prs, repo):
    """The pull requests this is allowed to touch."""
    out = []
    for pr in prs:
        head_repo = (pr.get("head") or {}).get("repo") or {}
        if (
            (pr.get("user") or {}).get("login") == BOT
            and not pr.get("draft")
            and head_repo.get("full_name") == repo
            and (pr.get("base") or {}).get("ref") == "main"
        ):
            out.append(pr)
    return out


def sync(call, repo):
    """call(method, path, body=None) -> (status, json). Returns [(outcome, number, branch, detail)]."""
    status, prs = call("GET", f"/repos/{repo}/pulls?state=open&per_page=100")
    if status != 200:
        return [("failed", 0, "", f"could not list pull requests (HTTP {status})")]
    results = []
    for pr in candidates(prs, repo):
        number, branch, sha = pr["number"], pr["head"]["ref"], pr["head"]["sha"]
        status, cmp = call("GET", f"/repos/{repo}/compare/main...{branch}")
        if status != 200:
            results.append(("failed", number, branch, f"compare failed (HTTP {status})"))
        elif not cmp.get("behind_by"):
            results.append(("current", number, branch, ""))
        else:
            behind = cmp["behind_by"]
            status, body = call(
                "PUT", f"/repos/{repo}/pulls/{number}/update-branch", {"expected_head_sha": sha}
            )
            message = (body or {}).get("message", "") if isinstance(body, dict) else ""
            if status == 202:
                results.append(("synced", number, branch, f"{behind} commit(s) behind main"))
            elif status == 422:
                results.append(("conflict", number, branch, message or "merge conflict"))
            else:
                results.append(("failed", number, branch, f"HTTP {status}: {message}"))
    return results


def _github(token):
    def call(method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(API + path, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github+json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode() or "null"
                return resp.status, json.loads(raw)
        except urllib.error.HTTPError as e:
            raw = e.read().decode() or "null"
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"message": raw[:200]}

    return call


def main() -> int:
    token, repo = os.environ.get("GH_TOKEN", ""), os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        sys.stderr.write("sync_bot_prs: GH_TOKEN and GITHUB_REPOSITORY are required\n")
        return 2
    results = sync(_github(token), repo)
    for outcome, number, branch, detail in results:
        print(f"{outcome:9} #{number} {branch} {detail}".rstrip())
        if outcome in ("conflict", "failed"):
            # A warning, not a failure: a red mark on main's own commit would only confuse.
            print(f"::warning title=PR #{number} not brought up to date::{outcome}: {detail}")
    if not results:
        print("no open bot PRs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
