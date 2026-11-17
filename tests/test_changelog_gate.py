"""The pre-merge changelog gate (spec: docs/specs/175-intake-trailer-and-changelog-gate.md).

A change to product code (anthill/) must add a changelog fragment before it merges, by the same rule the
post-merge documentation agent uses (`needs_changelog` in operate/docsync-render.py), which the gate loads
rather than copies. Test-only, docs-only and CI-only changes, release cuts, the chore lane and machine authors
need none; a pillar:feature change always does, as before.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / ".github" / "asdd" / "changelog-gate.py"
FRAGMENT = "changelog.d/205.fixed.md"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate = _load(GATE, "changelog_gate")
renderer = _load(ROOT / ".github/asdd/operate/docsync-render.py", "docsync_render_for_test")


def _run(tmp_path, changed, labels=("pillar:platform",), title="fix: a thing", machine="false"):
    (tmp_path / "changed.txt").write_text("".join(p + "\n" for p in changed))
    (tmp_path / "labels.txt").write_text("".join(label + "\n" for label in labels))
    r = subprocess.run(
        [
            sys.executable,
            str(GATE),
            "--changed",
            str(tmp_path / "changed.txt"),
            "--labels",
            str(tmp_path / "labels.txt"),
            "--title",
            title,
            "--machine",
            machine,
        ],
        capture_output=True,
        text=True,
    )
    return r.returncode, r.stdout


def test_product_code_without_a_fragment_fails_with_a_fixable_message(tmp_path):
    code, out = _run(tmp_path, ["anthill/web/app.py", "tests/test_x.py"])
    assert code == 1
    assert "must add a changelog fragment" in out and "changelog.d/<id>.<category>.md" in out


def test_product_code_with_a_valid_fragment_passes(tmp_path):
    code, out = _run(tmp_path, ["anthill/web/app.py", FRAGMENT])
    assert code == 0 and "fragment present" in out


@pytest.mark.parametrize(
    "changed",
    [
        ["tests/test_x.py"],
        [".github/workflows/ci.yml", ".github/asdd/intake-check.sh"],
        ["docs/specs/x.md", "docs/USING_ANTHILL.md"],
        ["src-tauri/Cargo.lock"],
        ["pyproject.toml", "requirements.lock"],
    ],
)
def test_changes_that_touch_no_product_code_need_no_fragment(tmp_path, changed):
    code, out = _run(tmp_path, changed)
    assert code == 0 and "none needed" in out


def test_the_chore_lane_is_exempt_even_for_product_code(tmp_path):
    code, _ = _run(tmp_path, ["anthill/web/app.py"], labels=("chore",))
    assert code == 0


def test_a_release_cut_needs_no_fragment(tmp_path):
    cut = ["CHANGELOG.md", "pyproject.toml", "anthill/__init__.py"]
    assert _run(tmp_path, cut, labels=("chore",))[0] == 0
    assert _run(tmp_path, cut, title="chore: cut release v1.2.0")[0] == 0
    assert _run(tmp_path, cut)[0] == 0  # nothing but release files changed: still a cut


def test_a_machine_author_is_exempt(tmp_path):
    assert _run(tmp_path, ["anthill/web/app.py"], labels=(), machine="true")[0] == 0
    assert _run(tmp_path, ["anthill/web/app.py"], labels=(), machine="false")[0] == 1


def test_a_feature_pr_always_needs_a_fragment_as_before(tmp_path):
    code, out = _run(tmp_path, ["docs/USING_ANTHILL.md"], labels=("pillar:feature",))
    assert code == 1 and "pillar:feature" in out
    assert _run(tmp_path, ["docs/USING_ANTHILL.md", FRAGMENT], labels=("pillar:feature",))[0] == 0


@pytest.mark.parametrize(
    "bad",
    [
        "changelog.d/205.nope.md",  # not a category
        "changelog.d/README.md",
        "changelog.d/205.fixed.txt",
        "docs/changelog.d/205.fixed.md",
    ],
)
def test_only_a_correctly_named_fragment_counts(tmp_path, bad):
    assert _run(tmp_path, ["anthill/web/app.py", bad])[0] == 1


def test_the_gate_agrees_with_the_documentation_agents_rule():
    """One definition: for any path set the gate's 'product code changed' answer is needs_changelog()."""
    cases = [
        ["anthill/a.py"],
        ["tests/a.py"],
        [".github/a.yml", "docs/a.md"],
        ["anthill/__init__.py", "pyproject.toml", "CHANGELOG.md"],
        ["anthill/__init__.py", "pyproject.toml", "CHANGELOG.md", "anthill/web/app.py"],
        ["changelog.d/1.fixed.md"],
        [],
    ]
    for title in ("fix: x", "chore: cut release v2"):
        for paths in cases:
            required, _, _ = gate.verdict(paths, {"pillar:platform"}, title)
            assert required == renderer.needs_changelog(paths, title), (paths, title)


def test_the_workflow_runs_the_gate_without_expanding_the_title_into_the_shell():
    doc = yaml.safe_load((ROOT / ".github/workflows/pr-validation.yml").read_text())
    steps = [s for j in doc["jobs"].values() for s in j.get("steps", [])]
    step = next(
        s for s in steps if s.get("name") == "Product changes must add a changelog fragment"
    )
    assert "${{" not in step["run"]  # the title, labels and author arrive through env:
    assert step["env"]["PR_TITLE"] == "${{ github.event.pull_request.title }}"
    assert "changelog-gate.py" in step["run"]
    assert (
        'git show "origin/${BASE_REF}:.asdd.yml"' in step["run"]
    )  # machine list from the base, not the PR
    assert not any(s.get("name") == "Feature PRs must add a changelog fragment" for s in steps)
