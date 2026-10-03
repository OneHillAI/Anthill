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
