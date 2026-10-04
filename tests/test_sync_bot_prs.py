"""Open bot PR branches are kept current with main (spec: contribution-policy-refinements R5).

The script makes GitHub API calls and nothing else, so these tests give it a fake `call` and check which pull
requests it touches, what it does to each, and that the workflow is as narrow as it claims. No network.
"""

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("sync_bot_prs", ROOT / "scripts/sync_bot_prs.py")
sb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sb)

REPO = "OneHillAI/Anthill"
BOT = "onehill-dev-agent[bot]"


def _pr(number, *, user=BOT, draft=False, repo=REPO, base="main", sha=None):
    return {
        "number": number,
        "user": {"login": user},
        "draft": draft,
        "base": {"ref": base},
        "head": {
            "ref": f"branch-{number}",
            "sha": sha or f"sha{number}",
            "repo": {"full_name": repo},
        },
    }


class Fake:
    """A stand-in for the GitHub API: which PRs are open, how far behind each branch is, what update returns."""

    def __init__(self, prs, behind=None, update=None):
        self.prs, self.behind, self.update = prs, behind or {}, update or {}
        self.calls = []

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method == "GET" and "/pulls?" in path:
            return 200, self.prs
        if method == "GET" and "/compare/main..." in path:
            branch = path.split("...", 1)[1]
            return 200, {"behind_by": self.behind.get(branch, 0)}
        if method == "PUT" and path.endswith("/update-branch"):
            number = int(path.split("/")[-2])
            return self.update.get(number, (202, {"message": "Updating pull request branch."}))
        raise AssertionError(f"unexpected call {method} {path}")


def test_only_bot_pull_requests_from_this_repo_against_main_are_touched():
    prs = [
        _pr(1),
        _pr(2, user="someone-else"),
        _pr(3, draft=True),
        _pr(4, repo="fork/Anthill"),
        _pr(5, base="release"),
    ]
    assert [p["number"] for p in sb.candidates(prs, REPO)] == [1]


def test_a_pr_that_is_behind_is_updated_with_the_head_it_was_checked_at():
    fake = Fake([_pr(7, sha="abc")], behind={"branch-7": 3})
    assert sb.sync(fake, REPO) == [("synced", 7, "branch-7", "3 commit(s) behind main")]
    put = [c for c in fake.calls if c[0] == "PUT"]
    assert put == [("PUT", f"/repos/{REPO}/pulls/7/update-branch", {"expected_head_sha": "abc"})]


def test_a_pr_that_is_current_is_left_alone():
    fake = Fake([_pr(8)], behind={"branch-8": 0})
    assert sb.sync(fake, REPO) == [("current", 8, "branch-8", "")]
    assert not [c for c in fake.calls if c[0] == "PUT"]


def test_a_conflict_is_reported_and_other_prs_are_still_updated():
    fake = Fake(
        [_pr(9), _pr(10)],
        behind={"branch-9": 1, "branch-10": 2},
        update={9: (422, {"message": "merge conflict between base and head"})},
    )
    outcomes = {r[1]: r[0] for r in sb.sync(fake, REPO)}
    assert outcomes == {9: "conflict", 10: "synced"}


def test_an_unexpected_answer_is_a_failure_not_a_crash():
    fake = Fake([_pr(11)], behind={"branch-11": 1}, update={11: (409, {"message": "head moved"})})
    assert sb.sync(fake, REPO)[0][0] == "failed"


def test_nothing_to_do_without_open_prs():
    assert sb.sync(Fake([]), REPO) == []


def test_main_needs_a_token_and_a_repository(monkeypatch):
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert sb.main() == 2


def _workflow():
    return yaml.safe_load((ROOT / ".github/workflows/sync-bot-prs.yml").read_text())


def test_the_workflow_runs_on_a_push_to_main_and_never_on_a_pull_request():
    wf = _workflow()
    on = wf.get("on", wf.get(True))
    assert set(on) == {"push", "workflow_dispatch"}
    assert on["push"]["branches"] == ["main"]


def test_the_workflow_holds_no_write_permission_and_runs_only_the_script_from_main():
    wf = _workflow()
    assert wf["permissions"] == {"contents": "read"}
    steps = wf["jobs"]["sync"]["steps"]
    checkout = next(s for s in steps if "actions/checkout" in s.get("uses", ""))
    assert "ref" not in checkout.get("with", {})  # the default ref of a push is main itself
    runs = [s["run"] for s in steps if "run" in s]
    assert any("scripts/sync_bot_prs.py" in r for r in runs)
    text = (ROOT / ".github/workflows/sync-bot-prs.yml").read_text()
    assert "pull_request" not in text.replace(
        "pulls/", ""
    )  # no PR trigger or PR-supplied code anywhere


def test_the_update_uses_the_app_token_and_is_dormant_without_the_secrets():
    steps = _workflow()["jobs"]["sync"]["steps"]
    mint = next(s for s in steps if "create-github-app-token" in s.get("uses", ""))
    assert (
        "@" in mint["uses"] and len(mint["uses"].split("@")[1]) == 40
    )  # pinned to a full commit sha
    assert mint["if"] == "${{ steps.cfg.outputs.configured == 'true' }}"
    sync = next(s for s in steps if "sync_bot_prs.py" in s.get("run", ""))
    assert sync["env"]["GH_TOKEN"] == "${{ steps.app.outputs.token }}"
    assert sync["if"] == "${{ steps.cfg.outputs.configured == 'true' }}"
