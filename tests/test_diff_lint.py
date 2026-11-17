"""The diff-scoped lint gate (spec: docs/specs/176-diff-gates-and-spec-structure.md).

Strict ruff rules (blind except, commented-out code, complexity) are enforced on the lines a PR ADDS, so new
code is held to a higher bar without flagging the 500-odd findings already in the legacy tree. These drive
diff-lint.py with synthetic ruff output and diffs, check the CI step is wired the way the rules assume, and,
when ruff is installed, run the real pipeline end to end.
"""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10: pytest already pulls tomli there
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "asdd" / "diff-lint.py"
spec = importlib.util.spec_from_file_location("diff_lint", SCRIPT)
dl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dl)

DIFF = """diff --git a/anthill/a.py b/anthill/a.py
index 111..222 100644
--- a/anthill/a.py
+++ b/anthill/a.py
@@ -3,0 +4,2 @@ def f():
+    try:
+        pass
@@ -10 +12 @@ def g():
-    old
+    new
diff --git a/anthill/new.py b/anthill/new.py
new file mode 100644
--- /dev/null
+++ b/anthill/new.py
@@ -0,0 +1,3 @@
+a
+b
+c
diff --git a/anthill/gone.py b/anthill/gone.py
deleted file mode 100644
--- a/anthill/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-x
-y
"""


def _finding(path, row, code="BLE001", message="Do not catch blind exception"):
    return {
        "code": code,
        "message": message,
        "filename": str(ROOT / path),
        "location": {"row": row, "column": 5},
    }


def test_added_lines_reads_hunks_new_files_and_ignores_deletions():
    added = dl.added_lines(DIFF)
    assert added["anthill/a.py"] == {4, 5, 12}
    assert added["anthill/new.py"] == {1, 2, 3}
    assert "anthill/gone.py" not in added  # a deleted file adds nothing


def test_added_lines_tracks_context_lines_in_a_normal_diff():
    text = (
        "--- a/x.py\n+++ b/x.py\n@@ -1,3 +1,4 @@\n keep\n+added\n keep2\n-removed\n+replaced\n"
        "\\ No newline at end of file\n"
    )
    assert dl.added_lines(text)["x.py"] == {2, 4}


def test_only_findings_on_added_lines_are_kept():
    added = dl.added_lines(DIFF)
    findings = [
        _finding("anthill/a.py", 4),  # added
        _finding("anthill/a.py", 20),  # legacy line in a touched file
        _finding("anthill/other.py", 4),  # a file the PR did not touch
        _finding("anthill/new.py", 2),  # in a new file
    ]
    hits = dl.findings_on_added_lines(findings, added, root=str(ROOT))
    assert [(h[0], h[1]) for h in hits] == [("anthill/a.py", 4), ("anthill/new.py", 2)]


def _run(tmp_path, findings, diff, *extra):
    (tmp_path / "ruff.json").write_text(json.dumps(findings))
    (tmp_path / "change.diff").write_text(diff)
    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(tmp_path / "ruff.json"),
            str(tmp_path / "change.diff"),
            *extra,
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return r.returncode, r.stdout


def test_a_blocking_finding_on_an_added_line_fails_with_an_annotation(tmp_path):
    code, out = _run(
        tmp_path, [_finding("anthill/a.py", 4, "C901", "`f` is too complex (11 > 10)")], DIFF
    )
    assert code == 1
    assert "::error file=anthill/a.py,line=4,col=5::C901" in out and "split this function" in out


def test_findings_only_on_legacy_lines_pass(tmp_path):
    code, out = _run(tmp_path, [_finding("anthill/a.py", 99), _finding("anthill/z.py", 1)], DIFF)
    assert code == 0 and "no findings on added lines" in out


def test_a_warn_code_is_reported_but_does_not_fail(tmp_path):
    code, out = _run(tmp_path, [_finding("anthill/a.py", 4)], DIFF, "--warn", "BLE001")
    assert (
        code == 0
        and "::warning file=anthill/a.py,line=4" in out
        and "0 blocking, 1 advisory" in out
    )


def test_a_warn_code_does_not_excuse_other_codes(tmp_path):
    findings = [
        _finding("anthill/a.py", 4),
        _finding("anthill/new.py", 1, "ERA001", "Found commented-out code"),
    ]
    code, out = _run(tmp_path, findings, DIFF, "--warn", "BLE001")
    assert code == 1 and "1 blocking, 1 advisory" in out


def test_unreadable_or_malformed_input_is_a_usage_error_not_a_pass(tmp_path):
    assert _run(tmp_path, {"not": "a list"}, DIFF)[0] == 2
    r = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            str(tmp_path / "missing.json"),
            str(tmp_path / "missing.diff"),
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 2
    assert (
        subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True).returncode
        == 2
    )


# --- wiring -----------------------------------------------------------------------------------------


def _step():
    doc = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    steps = doc["jobs"]["lint"]["steps"]
    return next(s for s in steps if s.get("name", "").startswith("Diff-scoped de-slop"))


def test_the_ci_step_runs_on_pull_requests_only_and_keeps_the_base_sha_out_of_the_shell():
    step = _step()
    assert step["if"] == "github.event_name == 'pull_request'"
    assert "${{" not in step["run"]
    assert step["env"]["BASE_SHA"] == "${{ github.event.pull_request.base.sha }}"
    assert "diff-lint.py" in step["run"] and "--warn BLE001" in step["run"]


def test_the_rules_ci_selects_are_exactly_the_ones_pyproject_marks_external():
    """A `# noqa: BLE001` must not trip RUF100 in the main ruff run, so every selected code is `external`."""
    run = _step()["run"]
    selected = set(run.split("--select ")[1].split()[0].split(","))
    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["ruff"]["lint"]
    assert selected == set(cfg["external"])
    assert not selected & set(
        cfg["select"]
    )  # not also enabled globally: the legacy tree would fail


def _ruff():
    beside_python = Path(sys.executable).parent / "ruff"
    return str(beside_python) if beside_python.exists() else shutil.which("ruff")


@pytest.mark.skipif(_ruff() is None, reason="ruff is not installed")
def test_end_to_end_with_real_ruff_honours_noqa_and_only_flags_added_lines(tmp_path):
    src = tmp_path / "anthill"
    src.mkdir()
    code = (
        "def kept(x):\n    try:\n        return int(x)\n"
        "    except Exception:  # noqa: BLE001 - parsing user text must not crash\n        return 0\n\n\n"
        "def bare(x):\n    try:\n        return int(x)\n    except Exception:\n        return 0\n"
    )
    (src / "m.py").write_text(code)
    r = subprocess.run(
        [
            _ruff(),
            "check",
            "anthill",
            "--isolated",
            "--select",
            "BLE001",
            "--output-format",
            "json",
            "--exit-zero",
            "--no-cache",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    findings = json.loads(r.stdout)
    assert [f["location"]["row"] for f in findings] == [
        11
    ]  # the annotated one on line 4 is honoured
    diff = "--- /dev/null\n+++ b/anthill/m.py\n@@ -0,0 +1,12 @@\n" + "".join(
        "+x\n" for _ in range(12)
    )
    only_old = dl.findings_on_added_lines(
        findings,
        dl.added_lines("--- a/anthill/m.py\n+++ b/anthill/m.py\n@@ -1 +1 @@\n-a\n+b\n"),
        root=str(tmp_path.resolve()),
    )
    assert only_old == []  # line 11 is legacy: the change only touched line 1
    flagged = dl.findings_on_added_lines(
        findings, dl.added_lines(diff), root=str(tmp_path.resolve())
    )
    assert [(h[1], h[3]) for h in flagged] == [(11, "BLE001")]
