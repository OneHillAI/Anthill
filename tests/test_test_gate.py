"""The pre-merge test-presence gate (spec: docs/specs/176-diff-gates-and-spec-structure.md).

A change to Python product code (anthill/**/*.py) must add or change something under tests/. The chore lane,
a machine author and a release cut are exempt; a change with no Python product code is not covered.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / ".github" / "asdd" / "test-gate.py"
spec = importlib.util.spec_from_file_location("test_gate", GATE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


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


def test_product_code_without_a_test_change_fails_with_a_fixable_message(tmp_path):
    code, out = _run(tmp_path, ["anthill/web/app.py", "docs/x.md"])
    assert code == 1
    assert "must add or change a test under tests/" in out and "chore lane" in out


def test_product_code_with_a_test_change_passes(tmp_path):
    code, out = _run(tmp_path, ["anthill/web/app.py", "tests/test_app.py"])
    assert code == 0 and "test change present" in out


@pytest.mark.parametrize(
    "changed",
    [
        ["docs/specs/x.md"],
        [".github/workflows/ci.yml", ".github/asdd/intake-check.sh"],
        [
            "anthill/web/static/app.css",
            "anthill/web/templates/x.html",
        ],  # product files, but not Python
        ["pyproject.toml", "requirements.lock"],
        ["tests/test_x.py"],
    ],
)
def test_changes_with_no_python_product_code_need_no_test(tmp_path, changed):
    code, out = _run(tmp_path, changed)
    assert code == 0, out


def test_chore_machine_and_release_cut_are_exempt(tmp_path):
    assert _run(tmp_path, ["anthill/web/app.py"], labels=("chore",))[0] == 0
    assert _run(tmp_path, ["anthill/web/app.py"], labels=(), machine="true")[0] == 0
    cut = ["CHANGELOG.md", "pyproject.toml", "anthill/__init__.py"]
    assert _run(tmp_path, cut)[0] == 0
    assert _run(tmp_path, cut, title="chore: cut release v1.2.0")[0] == 0


def test_a_cut_title_does_not_excuse_a_change_that_also_touches_other_product_code(tmp_path):
    changed = ["anthill/__init__.py", "anthill/web/app.py"]
    assert (
        _run(tmp_path, changed, title="chore: cut release v1.2.0")[0] == 0
    )  # the title alone exempts it
    assert _run(tmp_path, changed, title="fix: a thing")[0] == 1


def test_any_file_under_tests_counts(tmp_path):
    assert _run(tmp_path, ["anthill/a.py", "tests/conftest.py"])[0] == 0
    assert _run(tmp_path, ["anthill/a.py", "tests/browser/test_x.py"])[0] == 0
    assert _run(tmp_path, ["anthill/a.py", "docs/tests/notes.md"])[0] == 1  # not under tests/


def test_the_gate_agrees_with_the_changelog_gates_release_cut_rule():
    renderer = importlib.util.spec_from_file_location(
        "dsr", ROOT / ".github/asdd/operate/docsync-render.py"
    )
    mod = importlib.util.module_from_spec(renderer)
    renderer.loader.exec_module(mod)
    for paths in (["anthill/__init__.py", "pyproject.toml"], ["anthill/a.py"], ["CHANGELOG.md"]):
        for title in ("fix: x", "chore: cut release v2"):
            required, _, _ = gate.verdict(paths, {"pillar:platform"}, title)
            expected = any(
                p.startswith("anthill/") and p.endswith(".py") for p in paths
            ) and not mod.is_release_cut(paths, title)
            assert required == expected, (paths, title)


def test_the_workflow_step_takes_the_title_through_env_and_the_machine_list_from_the_base():
    doc = yaml.safe_load((ROOT / ".github/workflows/pr-validation.yml").read_text())
    steps = [s for j in doc["jobs"].values() for s in j.get("steps", [])]
    step = next(s for s in steps if s.get("name") == "Product code must come with a test change")
    assert "${{" not in step["run"]
    assert step["env"]["PR_TITLE"] == "${{ github.event.pull_request.title }}"
    assert (
        "test-gate.py" in step["run"] and 'git show "origin/${BASE_REF}:.asdd.yml"' in step["run"]
    )
