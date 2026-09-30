"""owner-override.sh (spec: docs/specs/owner-review-override.md).

A PR is exempt from the ASDD gates only when its AUTHOR is a configured owner AND the `owner-override`
label is present - author-binding is the security property, so a non-owner applying the label changes
nothing. Most tests craft a `.asdd.yml` so the logic is pinned independent of the live config; one checks
the real repo config actually names the owner.
"""

import subprocess
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = str(_ROOT / ".github" / "asdd" / "owner-override.sh")


def _env(tmp_path, owners, labels):
    (tmp_path / ".asdd.yml").write_text(
        "review_override_owners:\n"
        + "".join(f'  - "{o}"\n' for o in owners)
        + "\ntriage_labels:\n  - bug\n"  # a following key, so the parser stops at the list end
    )
    (tmp_path / "labels.txt").write_text("\n".join(labels) + ("\n" if labels else ""))


def _run(cwd, author, labels_file="labels.txt", config=".asdd.yml"):
    # owners come from an explicit trusted config path (3rd arg), never the script's CWD.
    r = subprocess.run(
        ["/bin/bash", _SCRIPT, author, str(Path(cwd) / labels_file), str(Path(cwd) / config)],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )
    return r.stdout.strip()


def test_owner_with_label_is_overridden(tmp_path):
    _env(tmp_path, ["welsbach"], ["owner-override", "pillar:platform"])
    assert _run(tmp_path, "welsbach") == "true"


def test_owner_match_is_case_insensitive(tmp_path):
    _env(tmp_path, ["welsbach"], ["owner-override"])
    assert _run(tmp_path, "Welsbach") == "true"


def test_owner_without_the_label_is_not_overridden(tmp_path):
    _env(tmp_path, ["welsbach"], ["pillar:platform"])
    assert _run(tmp_path, "welsbach") == "false"


def test_non_owner_with_the_label_is_not_overridden(tmp_path):
    # The security property: a non-owner applying owner-override cannot bypass.
    _env(tmp_path, ["welsbach"], ["owner-override"])
    assert _run(tmp_path, "random-contributor") == "false"


def test_login_with_a_hyphen_is_preserved(tmp_path):
    # Logins may contain hyphens; the list-marker strip must not eat them.
    _env(tmp_path, ["some-owner"], ["owner-override"])
    assert _run(tmp_path, "some-owner") == "true"


def test_empty_owners_never_overrides(tmp_path):
    _env(tmp_path, [], ["owner-override"])
    assert _run(tmp_path, "welsbach") == "false"


def test_empty_author_is_not_overridden(tmp_path):
    _env(tmp_path, ["welsbach"], ["owner-override"])
    assert _run(tmp_path, "") == "false"


def test_real_repo_config_names_the_owner(tmp_path):
    # The shipped .asdd.yml actually grants the owner the override (with the label).
    (tmp_path / "labels.txt").write_text("owner-override\n")
    assert _run(_ROOT, "welsbach", labels_file=str(tmp_path / "labels.txt")) == "true"


def test_owners_come_from_the_passed_config_not_cwd(tmp_path):
    # The enforced boundary: a hostile .asdd.yml in CWD must not grant the override - only the
    # explicitly-passed trusted config is read (review-found: don't rely on CWD for the security check).
    (tmp_path / ".asdd.yml").write_text(
        'review_override_owners:\n  - "attacker"\n'
    )  # hostile CWD copy
    (tmp_path / "trusted.yml").write_text('review_override_owners:\n  - "welsbach"\n')
    (tmp_path / "labels.txt").write_text("owner-override\n")
    r = subprocess.run(
        [
            "/bin/bash",
            _SCRIPT,
            "attacker",
            str(tmp_path / "labels.txt"),
            str(tmp_path / "trusted.yml"),
        ],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
    )
    assert r.stdout.strip() == "false"  # the CWD .asdd.yml is ignored; the trusted config wins
