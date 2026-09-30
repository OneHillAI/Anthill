"""The ASDD intake gate's anti-flood cap (spec: docs/specs/contribution-policy-refinements.md).

The network count lives in the workflow; intake-check.sh only compares two numbers from meta.env. These
model-free tests drive the script over a synthetic, otherwise-valid workdir and pin the cap logic:
over the cap fails, at/under passes, and an absent count or a zero cap skips (backward compatible).
"""

import json
import subprocess
from pathlib import Path

_GATE = str(Path(__file__).resolve().parents[1] / ".github" / "asdd" / "intake-check.sh")


def _workdir(tmp_path, *, meta_extra="", lane="pillar:platform", changed="", spec_ref=True):
    # A spec reference in the body by default, so the spec gate is satisfied unless a test drops it.
    body = "- [x] Authored by a **human**.\n"
    if spec_ref:
        body += "Spec: docs/specs/thing.md\n"
    (tmp_path / "body.md").write_text(body)
    # one DCO-signed commit, NUL-delimited (the format intake-check.sh reads)
    (tmp_path / "commits.txt").write_bytes(b"feat: a thing\n\nSigned-off-by: Ada <ada@x.io>\n\x00")
    (tmp_path / "labels.txt").write_text(lane + "\n" if lane else "")
    (tmp_path / "changed.txt").write_text(changed)
    (tmp_path / "meta.env").write_text("pr_number=7\nhead_sha=deadbeef\n" + meta_extra)
    return tmp_path


_REPO_ROOT = str(Path(__file__).resolve().parents[1])


def _run(workdir, tmp_path):
    out = tmp_path / "intake.json"
    # cwd = repo root so the spec-gate's "referenced spec exists?" check resolves against the real tree.
    subprocess.run(
        ["/bin/bash", _GATE, str(workdir), str(out)], capture_output=True, text=True, cwd=_REPO_ROOT
    )
    return json.loads(out.read_text())


def test_valid_pr_under_cap_passes(tmp_path):
    data = _run(_workdir(tmp_path, meta_extra="max_open_prs=20\nopen_pr_count=3\n"), tmp_path)
    assert data["passed"] is True and data["flood_ok"] is True


def test_over_cap_fails(tmp_path):
    data = _run(_workdir(tmp_path, meta_extra="max_open_prs=20\nopen_pr_count=21\n"), tmp_path)
    assert data["flood_ok"] is False and data["passed"] is False
    assert any("Too many open PRs" in p for p in data["problems"])


def test_exactly_at_cap_is_allowed(tmp_path):
    data = _run(_workdir(tmp_path, meta_extra="max_open_prs=20\nopen_pr_count=20\n"), tmp_path)
    assert data["flood_ok"] is True and data["passed"] is True


def test_absent_count_skips_the_check(tmp_path):
    # No open_pr_count supplied (older caller / local run) -> the cap is skipped, not a false block.
    data = _run(_workdir(tmp_path, meta_extra="max_open_prs=20\n"), tmp_path)
    assert data["flood_ok"] is True and data["passed"] is True


def test_zero_cap_disables_the_check(tmp_path):
    data = _run(_workdir(tmp_path, meta_extra="max_open_prs=0\nopen_pr_count=99\n"), tmp_path)
    assert data["flood_ok"] is True and data["passed"] is True


def test_flood_does_not_mask_other_intake_failures(tmp_path):
    # A missing lane still fails even when the flood check is clean - the cap is additive, not a bypass.
    wd = _workdir(tmp_path, meta_extra="max_open_prs=20\nopen_pr_count=1\n")
    (wd / "labels.txt").write_text("")  # no lane label
    data = _run(wd, tmp_path)
    assert data["flood_ok"] is True and data["laned"] is False and data["passed"] is False


# ── spec gate (spec-driven by default) ──────────────────────────────────────────


def test_referenced_existing_spec_passes(tmp_path):
    # Body references a spec that ACTUALLY EXISTS in the repo tree.
    wd = _workdir(tmp_path, meta_extra="require_spec=true\n", spec_ref=False)
    (wd / "body.md").write_text(
        "- [x] Authored by a **human**.\nSpec: docs/specs/mandatory-spec-gate.md\n"
    )
    data = _run(wd, tmp_path)
    assert data["spec_ok"] is True and data["passed"] is True


def test_fabricated_spec_reference_fails(tmp_path):
    # A spec-looking string whose file does NOT exist must not pass (the review-found bypass).
    wd = _workdir(
        tmp_path, meta_extra="require_spec=true\n", spec_ref=False, changed="M\tanthill/x.py\n"
    )
    (wd / "body.md").write_text(
        "- [x] Authored by a **human**.\nSpec: docs/specs/does-not-exist.md\n"
    )
    data = _run(wd, tmp_path)
    assert data["spec_ok"] is False and data["passed"] is False


def test_spec_included_as_a_changed_file_passes(tmp_path):
    wd = _workdir(
        tmp_path, meta_extra="require_spec=true\n", spec_ref=False, changed="A\tdocs/specs/new.md\n"
    )
    data = _run(wd, tmp_path)
    assert data["spec_ok"] is True and data["passed"] is True  # the PR includes a spec


def test_no_spec_fails_with_actionable_message(tmp_path):
    wd = _workdir(
        tmp_path, meta_extra="require_spec=true\n", spec_ref=False, changed="M\tanthill/x.py\n"
    )
    data = _run(wd, tmp_path)
    assert data["spec_ok"] is False and data["passed"] is False
    assert any("Not based on a spec" in p and "docs/specs/" in p for p in data["problems"])


def test_chore_lane_is_exempt_from_the_spec_gate(tmp_path):
    wd = _workdir(
        tmp_path,
        meta_extra="require_spec=true\n",
        lane="chore",
        spec_ref=False,
        changed="M\tREADME.md\n",
    )
    data = _run(wd, tmp_path)
    assert data["spec_ok"] is True and data["passed"] is True  # trivial lane needs no spec


def test_spec_gate_disabled_skips_the_check(tmp_path):
    # require_spec unset (older caller / adopter who has not enabled it) -> no spec is fine.
    wd = _workdir(tmp_path, spec_ref=False, changed="M\tanthill/x.py\n")
    data = _run(wd, tmp_path)
    assert data["spec_ok"] is True and data["passed"] is True


def test_deleting_a_spec_does_not_satisfy_the_gate(tmp_path):
    # `git diff --name-status` marks a removed spec as D; it must NOT count as an included spec, or a PR
    # could delete docs/specs/old.md, change code, and pass the mandatory-spec gate (review-found bug).
    wd = _workdir(
        tmp_path, meta_extra="require_spec=true\n", spec_ref=False, changed="D\tdocs/specs/old.md\n"
    )
    data = _run(wd, tmp_path)
    assert data["spec_ok"] is False and data["passed"] is False


def test_path_traversal_spec_reference_fails(tmp_path):
    # A `..` reference that resolves to a real non-spec file must not satisfy the gate (review-found
    # path traversal): docs/specs/../../README.md exists but is not a spec.
    wd = _workdir(
        tmp_path, meta_extra="require_spec=true\n", spec_ref=False, changed="M\tanthill/x.py\n"
    )
    (wd / "body.md").write_text(
        "- [x] Authored by a **human**.\nSpec: docs/specs/../../README.md\n"
    )
    data = _run(wd, tmp_path)
    assert data["spec_ok"] is False and data["passed"] is False


# ── owner override ──────────────────────────────────────────────────────────────


def test_owner_override_makes_a_failing_intake_pass(tmp_path):
    wd = _workdir(tmp_path, meta_extra="override=true\n")
    (wd / "labels.txt").write_text("")  # no lane -> would normally fail intake
    data = _run(wd, tmp_path)
    assert data["override"] is True and data["passed"] is True
    assert any("Owner override in effect" in p for p in data["problems"])  # advisory, on the record


def test_without_override_a_failing_intake_still_fails(tmp_path):
    wd = _workdir(tmp_path, meta_extra="override=false\n")
    (wd / "labels.txt").write_text("")
    data = _run(wd, tmp_path)
    assert data["override"] is False and data["passed"] is False
