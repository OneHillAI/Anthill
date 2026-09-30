"""The ASDD policy decision point (policy-check.sh) fails closed.

Security review: the PDP validated the review artifact and enforced the per-run rate limit only when
``jq`` was present - so on a runner without jq it skipped those checks and fell through to ALLOW,
contradicting its own fail-closed contract. It now denies when jq is unavailable. Also, ``set-status``
(which writes the merge-gating commit status) is now an allow-listed action so the publish job
authorises it through the PDP like every other action; ``merge`` stays permanently denied.
"""

import subprocess
from pathlib import Path

_PDP = str(Path(__file__).resolve().parents[1] / ".github" / "asdd" / "policy-check.sh")


def _review(tmp_path):
    p = tmp_path / "review.json"
    p.write_text(
        '{"schema":"asdd/review/v0.1","pr_number":7,"recommendation":"comment","lenses":[]}'
    )
    return str(p)


def _run(action, review, *, path=None):
    env = {"ASDD_PHASE": "advisory"}
    if path is not None:
        env["PATH"] = path
    return subprocess.run(
        ["/bin/bash", _PDP, action, review], env=env, capture_output=True, text=True
    )


def test_set_status_is_authorised(tmp_path):
    r = _run("set-status", _review(tmp_path))
    assert r.returncode == 0 and "ALLOW" in r.stdout


def test_comment_is_authorised(tmp_path):
    assert _run("comment", _review(tmp_path)).returncode == 0


def test_merge_is_permanently_denied(tmp_path):
    r = _run("merge", _review(tmp_path))
    assert r.returncode != 0 and "permanently denied" in r.stdout


def test_unknown_action_is_denied(tmp_path):
    r = _run("exfiltrate", _review(tmp_path))
    assert r.returncode != 0 and "not on the allow-list" in r.stdout


def test_pdp_fails_closed_without_jq(tmp_path):
    # No jq on PATH -> the artifact can't be validated, so authorising would trust it blindly. Deny.
    r = _run("comment", _review(tmp_path), path="/nonexistent")
    assert r.returncode != 0 and "jq unavailable" in r.stdout


def test_missing_artifact_is_denied(tmp_path):
    r = _run("comment", str(tmp_path / "does-not-exist.json"))
    assert r.returncode != 0 and "missing/empty" in r.stdout
