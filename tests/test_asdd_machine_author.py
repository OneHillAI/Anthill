"""Machine authors (spec: docs/specs/dependabot-intake-machine-authors.md).

Dependabot writes its own PR body and labels, so it can neither tick the disclosure box nor carry a lane tag,
and `intake` is a required check: no Dependabot PR could ever merge. A PR authored by a login listed in
`machine_authors` (trusted base .asdd.yml) is waived those two requirements, but only while every commit in it
is authored by that same login. DCO, the anti-flood cap, a second lane label and everything after intake still
apply. These drive machine-author.sh and intake-check.sh over synthetic workdirs.
"""

import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MACHINE = str(ROOT / ".github" / "asdd" / "machine-author.sh")
GATE = str(ROOT / ".github" / "asdd" / "intake-check.sh")
BOT = "dependabot[bot]"
DEPENDABOT_BODY = "Bumps [rustls](https://github.com/rustls/rustls) from 0.23.45 to 0.23.46.\n"
SIGNED = b"chore(deps): bump rustls\n\nSigned-off-by: dependabot[bot] <support@github.com>\n\x00"


# --- machine-author.sh ----------------------------------------------------------------------------


def _config(tmp_path, authors, text=None):
    body = text or (
        "machine_authors:\n"
        + "".join(f'  - "{a}"\n' for a in authors)
        + "\nreview_override_owners:\n  - welsbach\n"  # a following key: the list must stop here
    )
    (tmp_path / ".asdd.yml").write_text(body)
    return str(tmp_path / ".asdd.yml")


def _is_machine(author, config):
    r = subprocess.run(["/bin/bash", MACHINE, author, config], capture_output=True, text=True)
    return r.stdout.strip()


def test_a_listed_author_is_a_machine_author_with_its_bot_suffix(tmp_path):
    assert _is_machine(BOT, _config(tmp_path, [BOT])) == "true"


def test_the_match_is_case_insensitive_and_exact(tmp_path):
    cfg = _config(tmp_path, [BOT])
    assert _is_machine("Dependabot[bot]", cfg) == "true"
    assert _is_machine("dependabot", cfg) == "false"  # not a prefix match
    assert _is_machine("dependabot[bot]x", cfg) == "false"
    assert _is_machine("someone-else", cfg) == "false"


def test_missing_config_empty_author_or_empty_list_is_never_a_machine(tmp_path):
    assert _is_machine(BOT, str(tmp_path / "absent.yml")) == "false"
    assert _is_machine("", _config(tmp_path, [BOT])) == "false"
    assert _is_machine(BOT, _config(tmp_path, [])) == "false"
    assert _is_machine(BOT, _config(tmp_path, [], text="max_open_prs_per_author: 20\n")) == "false"


def test_only_the_machine_authors_list_counts_not_other_lists(tmp_path):
    cfg = _config(tmp_path, [], text=f'machine_authors:\n  - "other[bot]"\nagents:\n  - "{BOT}"\n')
    assert _is_machine(BOT, cfg) == "false"
    assert _is_machine("other[bot]", cfg) == "true"


def test_comments_and_quoting_in_the_list_are_ignored(tmp_path):
    cfg = _config(tmp_path, [], text=f"machine_authors:\n  # who\n  - '{BOT}'  # the updater\n")
    assert _is_machine(BOT, cfg) == "true"


def test_only_a_bot_login_can_be_a_machine_author_even_if_listed(tmp_path):
    cfg = _config(tmp_path, ["ada", "ada-lovelace", BOT])
    assert _is_machine("ada", cfg) == "false"  # a person, listed by mistake or on purpose
    assert _is_machine("ada-lovelace", cfg) == "false"
    assert _is_machine(BOT, cfg) == "true"


def test_the_real_config_lists_dependabot():
    assert _is_machine(BOT, str(ROOT / ".asdd.yml")) == "true"
    assert _is_machine("welsbach", str(ROOT / ".asdd.yml")) == "false"


# --- intake-check.sh ------------------------------------------------------------------------------


def _workdir(
    tmp_path,
    *,
    machine="true",
    body=DEPENDABOT_BODY,
    labels="dependencies\nrust\n",
    commits=SIGNED,
    authors=BOT + "\n",
    author=BOT + "\n",
    meta_extra="require_spec=true\n",
):
    (tmp_path / "body.md").write_text(body)
    (tmp_path / "commits.txt").write_bytes(commits)
    (tmp_path / "labels.txt").write_text(labels)
    (tmp_path / "changed.txt").write_text("M\tsrc-tauri/Cargo.lock\n")
    if authors is not None:
        (tmp_path / "authors.txt").write_text(authors)
    if author is not None:
        (tmp_path / "author.txt").write_text(author)
    (tmp_path / "meta.env").write_text(
        f"pr_number=195\nhead_sha=deadbeef\nmachine_author={machine}\n" + meta_extra
    )
    return tmp_path


def _intake(workdir, tmp_path):
    out = tmp_path / "intake.json"
    subprocess.run(
        ["/bin/bash", GATE, str(workdir), str(out)], capture_output=True, text=True, cwd=str(ROOT)
    )
    return json.loads(out.read_text())


def test_a_dependabot_pr_passes_intake_with_no_box_and_no_lane_label(tmp_path):
    data = _intake(_workdir(tmp_path), tmp_path)
    assert data["passed"] is True and data["machine_author"] is True
    assert data["disclosed"] and data["laned"] and data["signed_off"] and data["spec_ok"]
    assert data["problems"] == []


def test_the_same_pr_from_a_login_not_on_the_list_still_fails(tmp_path):
    data = _intake(_workdir(tmp_path, machine="false"), tmp_path)
    assert data["passed"] is False and data["machine_author"] is False
    assert data["disclosed"] is False and data["laned"] is False


def test_a_human_commit_on_the_branch_ends_the_waiver(tmp_path):
    data = _intake(_workdir(tmp_path, authors=f"{BOT}\nAda Lovelace\n"), tmp_path)
    assert data["machine_author"] is False and data["passed"] is False
    assert data["disclosed"] is False


def test_without_the_commit_author_data_there_is_no_waiver(tmp_path):
    for missing in ({"authors": None}, {"author": None}):
        sub = tmp_path / next(iter(missing))
        sub.mkdir()
        data = _intake(_workdir(sub, **missing), sub)
        assert data["machine_author"] is False and data["passed"] is False


def test_commit_author_comparison_ignores_case_and_a_trailing_cr(tmp_path):
    data = _intake(
        _workdir(tmp_path, authors="Dependabot[bot]\r\n", author="dependabot[bot]\n"), tmp_path
    )
    assert data["machine_author"] is True and data["passed"] is True


def test_dco_is_still_required_of_a_machine_author(tmp_path):
    data = _intake(_workdir(tmp_path, commits=b"chore(deps): bump rustls\n\x00"), tmp_path)
    assert data["machine_author"] is True and data["signed_off"] is False
    assert data["passed"] is False


def test_the_anti_flood_cap_still_applies_to_a_machine_author(tmp_path):
    meta = "require_spec=true\nmax_open_prs=20\nopen_pr_count=21\n"
    data = _intake(_workdir(tmp_path, meta_extra=meta), tmp_path)
    assert data["flood_ok"] is False and data["passed"] is False


def test_an_explicit_chore_label_is_fine_and_two_lane_labels_are_not(tmp_path):
    assert _intake(_workdir(tmp_path, labels="chore\ndependencies\n"), tmp_path)["passed"] is True
    two = _intake(_workdir(tmp_path, labels="chore\npillar:platform\n"), tmp_path)
    assert two["laned"] is False and two["passed"] is False


def test_a_non_chore_lane_label_put_on_a_machine_pr_keeps_the_spec_rule(tmp_path):
    data = _intake(_workdir(tmp_path, labels="pillar:platform\ndependencies\n"), tmp_path)
    assert data["laned"] is True and data["spec_ok"] is False and data["passed"] is False


def test_an_older_workflow_that_never_sets_machine_author_behaves_as_before(tmp_path):
    wd = _workdir(tmp_path)
    (wd / "meta.env").write_text("pr_number=195\nhead_sha=deadbeef\nrequire_spec=true\n")
    data = _intake(wd, tmp_path)
    assert data["machine_author"] is False and data["passed"] is False


def test_an_ordinary_human_pr_is_unchanged(tmp_path):
    wd = _workdir(
        tmp_path,
        machine="false",
        body="- [x] Authored by a **human**.\nSpec: docs/specs/mandatory-spec-gate.md\n",
        labels="pillar:platform\n",
        authors="Ada\n",
        author="ada\n",
    )
    data = _intake(wd, tmp_path)
    assert data["passed"] is True and data["machine_author"] is False


# --- the workflow wiring --------------------------------------------------------------------------


def test_the_workflow_reads_the_machine_list_from_the_trusted_base_commit():
    text = (ROOT / ".github/workflows/asdd-intake.yml").read_text()
    assert 'machine-author.sh "$PR_AUTHOR" .asdd-work/base-asdd.yml' in text
    assert (
        'git show "$BASE_SHA:.asdd.yml" > .asdd-work/base-asdd.yml' in text
    )  # not the PR's working tree
    assert "--format='%an' > .asdd-work/authors.txt" in text
    assert 'echo "machine_author=${MACHINE}"' in text
