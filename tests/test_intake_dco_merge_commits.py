"""Intake's DCO check counts authored commits, not merge commits (spec: contribution-policy-refinements R4).

Bringing a branch up to date with main (a plain `git merge origin/main`, or GitHub's "Update branch" button)
adds a merge commit that cannot carry a sign-off without rewriting history. Counting it turned a green PR red
for good (PR #63). These tests take the real `git log` line from the intake workflow, run it over a real
history that contains an unsigned merge commit, and feed the result to the real intake-check.sh:

- the merge commit does not fail DCO;
- an unsigned authored commit in the same range still does;
- the same history WITH merges counted fails, which is the bug the flag fixes.

No network, no model. Needs only git.
"""

import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/asdd-intake.yml"
GATE = ROOT / ".github/asdd/intake-check.sh"
_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "t@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
}


def _git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, env=_ENV, capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(repo, name, message):
    (repo / name).write_text(name)
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", message)


def _history(tmp_path, *, authored_signed=True):
    """base on main; a feature branch with one authored commit; main moves; then an unsigned merge of main
    into the branch, exactly what the Update branch button does."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(repo, "a.txt", "base\n\nSigned-off-by: Test <t@example.com>")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "-q", "-c", "feature")
    signoff = "\n\nSigned-off-by: Test <t@example.com>" if authored_signed else ""
    _commit(repo, "b.txt", "feat: the change" + signoff)
    _git(repo, "switch", "-q", "main")
    _commit(repo, "c.txt", "other work on main\n\nSigned-off-by: Test <t@example.com>")
    _git(repo, "switch", "-q", "feature")
    _git(repo, "merge", "--no-ff", "-q", "-m", "Merge branch 'main' into feature", "main")
    return repo, base, _git(repo, "rev-parse", "HEAD")


def _workflow_git_log(no_merges):
    """The workflow's own commit-list command, with the shell variables filled in later."""
    line = next(
        ln.strip()
        for ln in WORKFLOW.read_text().splitlines()
        if ln.strip().startswith("git log") and "commits.txt" in ln
    )
    return line if no_merges else line.replace("--no-merges ", "")


def _intake(repo, base, head, out_dir, *, no_merges=True):
    cmd = (
        _workflow_git_log(no_merges)
        .replace('"$BASE_SHA"', base)
        .replace('"$HEAD_SHA"', head)
        .replace(".asdd-work/commits.txt", str(out_dir / "commits.txt"))
    )
    subprocess.run(["bash", "-c", cmd], cwd=repo, env=_ENV, check=True)
    (out_dir / "body.md").write_text("- [x] Authored by an **AI agent** under human direction.\n")
    (out_dir / "labels.txt").write_text("chore\n")
    (out_dir / "changed.txt").write_text("")
    (out_dir / "meta.env").write_text("pr_number=1\nhead_sha=" + head + "\n")
    out = out_dir / "intake.json"
    subprocess.run(["bash", str(GATE), str(out_dir), str(out)], capture_output=True, cwd=ROOT)
    return json.loads(out.read_text())


def test_the_workflow_lists_commits_without_merges():
    text = WORKFLOW.read_text()
    assert re.search(r'git log --no-merges "\$BASE_SHA"\.\."\$HEAD_SHA" .*commits\.txt', text)


def test_an_unsigned_merge_commit_does_not_fail_dco(tmp_path):
    repo, base, head = _history(tmp_path)
    result = _intake(repo, base, head, tmp_path)
    assert result["signed_off"] is True
    assert result["passed"] is True


def test_an_unsigned_authored_commit_still_fails_dco(tmp_path):
    repo, base, head = _history(tmp_path, authored_signed=False)
    result = _intake(repo, base, head, tmp_path)
    assert result["signed_off"] is False
    assert result["passed"] is False


def test_counting_merge_commits_is_the_failure_this_prevents(tmp_path):
    repo, base, head = _history(tmp_path)
    result = _intake(repo, base, head, tmp_path, no_merges=False)
    assert result["signed_off"] is False
