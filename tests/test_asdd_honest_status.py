"""A review that did not happen must not read like one, and a docs agent that failed must say so.

Pins three behaviours of the ASDD scripts, all with `gh`/`goose`/the model command stubbed (no network):

- set-status.sh: a non-live review (dry-run, adapter-template, degraded) is never described as an
  "Advisory review complete"; the state stays success (an unwired runtime must not block a merge) and
  real failures keep their failure.
- post-review.sh: the comment says plainly when no AI review ran.
- generic.sh: a model that returns unusable output is recorded as mode "degraded", not "live".
- the documentation agent's runner is pinned in tests/test_asdd_docsync.py.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASDD = ROOT / ".github/asdd"


def _stub(tmp_path: Path, name: str, body: str) -> Path:
    d = tmp_path / "bin"
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text("#!/usr/bin/env bash\n" + body)
    p.chmod(0o755)
    return d


def _env(tmp_path: Path, **extra) -> dict:
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}"}
    env.update(extra)
    return env


# --- set-status.sh ---------------------------------------------------------------------------------


def _set_status(tmp_path: Path, review: dict) -> dict:
    """Run set-status.sh against `review`; return the state and description it would post."""
    log = tmp_path / "gh.log"
    _stub(
        tmp_path,
        "gh",
        f'''if [ "$1" = "api" ]; then printf '%s\\n' "$@" >> "{log}"; fi\nexit 0\n''',
    )
    rj = tmp_path / "review.json"
    rj.write_text(json.dumps({"pr_number": 7, "head_sha": "a" * 40, **review}))
    r = subprocess.run(
        ["bash", str(ASDD / "set-status.sh"), str(rj)],
        env=_env(tmp_path, GH_TOKEN="x", REPO="o/r"),
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    args = log.read_text().splitlines()
    return {
        "state": next(a.split("=", 1)[1] for a in args if a.startswith("state=")),
        "description": next(a.split("=", 1)[1] for a in args if a.startswith("description=")),
    }


def test_live_review_keeps_the_advisory_complete_line(tmp_path):
    got = _set_status(tmp_path, {"mode": "live", "recommendation": "comment", "lenses": []})
    assert got == {
        "state": "success",
        "description": "Advisory review complete; a human approves and merges.",
    }


@pytest.mark.parametrize(
    "mode,fragment",
    [
        ("dry-run", "not connected"),
        ("adapter-template", "no model endpoint"),
        ("degraded", "unusable output"),
    ],
)
def test_non_live_review_says_no_ai_review_ran_but_does_not_block(tmp_path, mode, fragment):
    got = _set_status(tmp_path, {"mode": mode, "recommendation": "comment", "lenses": []})
    assert got["state"] == "success"  # an unwired runtime must never block a merge
    assert got["description"].startswith("NO AI REVIEW RAN")
    assert fragment in got["description"]
    assert "Advisory review complete" not in got["description"]


def test_a_real_failure_is_not_softened(tmp_path):
    got = _set_status(tmp_path, {"mode": "live", "recommendation": "request-changes", "lenses": []})
    assert got == {"state": "failure", "description": "Review recommends changes."}


# --- a design-only objection does not fail the status (docs/specs/asdd-design-concerns-advisory.md) ---

DESIGN_ONLY = "Advisory: only the adversarial design pass recommends changes; code, security and spec are ok. A human decides."
REVIEW_FAILS = {"state": "failure", "description": "Review recommends changes."}
SECURITY_BLOCK_LINE = "Security review raised a blocking finding; needs a human resolution."


def _lens(name: str, verdict: str = "ok", *severities: str) -> dict:
    """One lens as the review runtime writes it: a verdict and findings with a severity each."""
    return {
        "lens": name,
        "verdict": verdict,
        "findings": [{"severity": s, "text": f"{s} in {name}"} for s in severities],
    }


def _live(tmp_path, rec: str, *lenses: dict) -> dict:
    return _set_status(tmp_path, {"mode": "live", "recommendation": rec, "lenses": list(lenses)})


def _all_ok_but_quality(quality: dict) -> list:
    return [_lens("code"), _lens("security"), _lens("spec"), quality]


def test_a_quality_only_request_changes_is_green_and_says_so(tmp_path):
    q = _lens("quality", "request-changes", "warn")  # the adversarial pass: a design objection
    got = _live(tmp_path, "request-changes", *_all_ok_but_quality(q))
    assert got == {"state": "success", "description": DESIGN_ONLY}


def test_a_quality_only_request_changes_with_only_notes_or_no_findings_is_green(tmp_path):
    for q in (_lens("quality", "concerns", "note"), _lens("quality", "request-changes")):
        got = _live(tmp_path, "request-changes", *_all_ok_but_quality(q))
        assert got == {"state": "success", "description": DESIGN_ONLY}


@pytest.mark.parametrize("other", ["code", "security", "spec", "impact"])
@pytest.mark.parametrize("verdict", ["concerns", "request-changes"])
def test_a_concern_in_any_other_lens_keeps_the_failure(tmp_path, other, verdict):
    lenses = [
        _lens("code"),
        _lens("security"),
        _lens("spec"),
        _lens("quality", "request-changes", "warn"),
    ]
    lenses = [_lens(other, verdict, "note") if x["lens"] == other else x for x in lenses]
    if other == "impact":
        lenses.append(_lens("impact", verdict, "note"))
    assert _live(tmp_path, "request-changes", *lenses) == REVIEW_FAILS


@pytest.mark.parametrize("other", ["code", "security", "spec", "impact"])
def test_a_warn_finding_in_another_lens_keeps_the_failure_even_with_an_ok_verdict(tmp_path, other):
    lenses = [
        _lens("code"),
        _lens("security"),
        _lens("spec"),
        _lens("quality", "request-changes", "warn"),
    ]
    lenses = [_lens(other, "ok", "warn") if x["lens"] == other else x for x in lenses]
    if other == "impact":
        lenses.append(_lens("impact", "ok", "warn"))
    assert _live(tmp_path, "request-changes", *lenses) == REVIEW_FAILS


@pytest.mark.parametrize("where", ["code", "spec", "quality"])
def test_a_block_finding_in_any_lens_keeps_the_failure(tmp_path, where):
    lenses = [
        _lens("code"),
        _lens("security"),
        _lens("spec"),
        _lens("quality", "request-changes", "warn"),
    ]
    lenses = [_lens(where, "request-changes", "block") if x["lens"] == where else x for x in lenses]
    assert _live(tmp_path, "request-changes", *lenses) == REVIEW_FAILS


def test_a_security_block_keeps_its_own_failure_line_even_when_the_rest_is_design_only(tmp_path):
    lenses = [
        _lens("code"),
        _lens("security", "request-changes", "block"),
        _lens("spec"),
        _lens("quality", "request-changes", "warn"),
    ]
    assert _live(tmp_path, "request-changes", *lenses) == {
        "state": "failure",
        "description": SECURITY_BLOCK_LINE,
    }


def test_request_changes_without_a_quality_lens_is_a_failure(tmp_path):
    # Nothing shows the objection came from the adversarial pass, so it is not treated as design-only.
    assert (
        _live(tmp_path, "request-changes", _lens("code"), _lens("security"), _lens("spec"))
        == REVIEW_FAILS
    )


def test_a_comment_recommendation_is_unchanged_whatever_quality_says(tmp_path):
    got = _live(tmp_path, "comment", *_all_ok_but_quality(_lens("quality", "concerns", "warn")))
    assert got == {
        "state": "success",
        "description": "Advisory review complete; a human approves and merges.",
    }


def test_a_lens_whose_findings_cannot_be_read_keeps_the_failure(tmp_path):
    # If the rule cannot be applied it must not soften anything: the unreadable lens is not "ok".
    lenses = _all_ok_but_quality(_lens("quality", "request-changes", "warn"))
    lenses[0]["findings"] = "not a list"
    assert _live(tmp_path, "request-changes", *lenses) == REVIEW_FAILS


@pytest.mark.parametrize("mode", ["dry-run", "adapter-template", "degraded"])
def test_a_review_that_did_not_run_live_is_never_softened_to_design_only(tmp_path, mode):
    # R3: a non-live review is unchanged. Whatever its lenses say, it keeps the failure it always had.
    q = _lens("quality", "request-changes", "warn")
    got = _set_status(
        tmp_path,
        {"mode": mode, "recommendation": "request-changes", "lenses": _all_ok_but_quality(q)},
    )
    assert got == REVIEW_FAILS


# --- post-review.sh --------------------------------------------------------------------------------


def _post_review(tmp_path: Path, mode: str) -> str:
    log = tmp_path / "gh.log"
    _stub(tmp_path, "gh", f'''printf '%s\\n' "$@" >> "{log}"\nexit 0\n''')
    rj = tmp_path / "review.json"
    rj.write_text(
        json.dumps(
            {
                "pr_number": 7,
                "head_sha": "a" * 40,
                "mode": mode,
                "recommendation": "comment",
                "lenses": [],
            }
        )
    )
    r = subprocess.run(
        ["bash", str(ASDD / "post-review.sh"), str(rj)],
        env=_env(tmp_path, GH_TOKEN="x", REPO="o/r"),
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    return log.read_text()


@pytest.mark.parametrize("mode", ["dry-run", "adapter-template", "degraded"])
def test_comment_says_no_ai_review_ran(tmp_path, mode):
    body = _post_review(tmp_path, mode)
    assert "No AI review ran" in body or "No usable AI review" in body
    assert f"Mode: `{mode}`" in body


def test_live_comment_has_no_warning_note(tmp_path):
    body = _post_review(tmp_path, "live")
    assert "No AI review ran" not in body and "No usable AI review" not in body


# --- generic.sh ------------------------------------------------------------------------------------


def test_unusable_model_output_is_recorded_as_degraded_not_live(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / "meta.env").write_text(
        "pr_number=7\nbase_sha=" + "b" * 40 + "\nhead_sha=" + "a" * 40 + "\n"
    )
    (work / "title.txt").write_text("t")
    (work / "body.md").write_text("b")
    (work / "changes.diff").write_text("")
    bin_dir = _stub(tmp_path, "model", "cat >/dev/null\necho 'this is not json'\n")
    out = tmp_path / "review.json"
    r = subprocess.run(
        ["bash", str(ASDD / "runtime/generic.sh")],
        env=_env(
            tmp_path,
            ASDD_WORKDIR=str(work),
            ASDD_OUT=str(out),
            ASDD_ROOT=str(ROOT),
            ASDD_MODEL_CMD=str(bin_dir / "model"),
        ),
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    review = json.loads(out.read_text())
    assert review["mode"] == "degraded"
    assert review["recommendation"] == "comment"
