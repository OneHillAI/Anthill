"""The post-merge test run reports what pytest actually did, with no model in the path.

Runs the real `.github/asdd/operate/test.sh` in an isolated copy of the files it needs, against tiny suites
written for each case. Pins that:

- a green suite is a PASS with its counts, a red one is a FAIL naming the failing cases;
- a suite that cannot be collected is a FAIL, and a run pytest did not complete (no tests, killed) is
  "NO VERDICT"; neither is ever a pass;
- the verdict follows pytest's exit code, and an exit code and JUnit file that disagree are not a pass;
- every run leaves the structured result the `asdd/test` status reads, and exactly one audit record;
- the workflow runs only on main, and neither it nor the script touches a model key or any secret.
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
OPERATE = ROOT / ".github/asdd/operate"

_FILES = [
    ".github/asdd/operate/test.sh",
    ".github/asdd/operate/test-report.py",
    ".github/asdd/operate/test-junit.py",
    "cli/audit.py",
]


@pytest.fixture
def repo(tmp_path):
    """An isolated tree holding just what test.sh needs, plus an empty tests/ directory to fill."""
    r = tmp_path / "repo"
    for f in _FILES:
        dst = r / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / f, dst)
    (r / "tests").mkdir()
    return r


def _suite(repo: Path, body: str) -> None:
    (repo / "tests" / "test_sample.py").write_text(body)


def _run(repo: Path):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ASDD_", "OPENAI_", "PYTEST_"))}
    env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), env["PATH"]])
    out = repo / "report.md"
    r = subprocess.run(
        ["bash", str(repo / ".github/asdd/operate/test.sh"), "abc123", str(out)],
        env=env,
        capture_output=True,
        text=True,
        cwd=repo,
    )
    return r, out.read_text() if out.exists() else ""


def _result(repo: Path) -> dict:
    return json.loads((repo / ".asdd-work/operate-result.json").read_text())


GREEN = (
    "def test_a():\n    pass\n\ndef test_b():\n    assert 1 + 1 == 2\n\ndef test_c():\n    pass\n"
)
RED = "def test_good():\n    pass\n\ndef test_bad():\n    assert 1 == 2\n"


def test_a_green_suite_is_a_pass_with_its_counts(repo):
    _suite(repo, GREEN + "\nimport pytest\n\n@pytest.mark.skip\ndef test_skipped():\n    pass\n")
    r, report = _run(repo)
    assert r.returncode == 0, r.stderr
    assert "## Test agent - result for `abc123`" in report
    assert "**PASS**" in report and "deterministic: pytest" in report
    assert "Passed: 3 / Failed: 0" in report
    res = _result(repo)
    assert res["verdict"] == "pass" and res["runner"] == "pytest"


def test_a_red_suite_is_a_fail_naming_the_failing_case_and_still_exits_zero(repo):
    _suite(repo, RED)
    r, report = _run(repo)
    assert r.returncode == 0  # the workflow still posts the report and records the status
    assert "**FAIL**" in report and "test_bad" in report
    assert "Passed: 1 / Failed: 1" in report
    assert _result(repo)["verdict"] == "fail"


def test_an_erroring_fixture_counts_as_failing(repo):
    _suite(
        repo,
        "import pytest\n\n@pytest.fixture\ndef boom():\n    raise RuntimeError('x')\n\n"
        "def test_uses_boom(boom):\n    pass\n",
    )
    _, report = _run(repo)
    assert "**FAIL**" in report and "test_uses_boom" in report


def test_a_collection_error_is_a_fail_naming_the_module_never_a_pass(repo):
    _suite(
        repo, "def test_a(:\n    pass\n"
    )  # syntax error: under xdist pytest reports it as an error
    r, report = _run(repo)
    assert r.returncode == 0
    assert "**FAIL**" in report and "test_sample" in report and "**PASS**" not in report
    assert _result(repo)["verdict"] == "fail"


def test_a_suite_with_no_tests_is_no_verdict_not_a_pass(repo):
    _suite(repo, "x = 1\n")  # pytest exits 5: nothing collected
    _, report = _run(repo)
    assert "NO VERDICT" in report and "**PASS**" not in report


def test_the_run_needs_no_model_and_leaves_one_audit_record(repo):
    _suite(repo, GREEN)
    r, _ = _run(repo)  # no key, no endpoint, no goose on PATH: a pass anyway
    assert r.returncode == 0
    ledger = (repo / ".asdd-work/audit.jsonl").read_text().strip().splitlines()
    assert len(ledger) == 1
    rec = json.loads(ledger[0])
    assert rec["agent"]["role"] == "test-runner" and rec["action"] == "test.run"
    assert rec["outcome"]["verdict"] == "pass"


# --- the converter on its own (test-junit.py) -----------------------------------------------------


def _load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), OPERATE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _junit(tmp_path, cases: str, wrap="testsuite") -> str:
    p = tmp_path / "junit.xml"
    inner = f'<testsuite tests="9">{cases}</testsuite>'
    p.write_text(f"<testsuites>{inner}</testsuites>" if wrap == "testsuites" else inner)
    return str(p)


OK3 = '<testcase classname="t" name="a"/><testcase classname="t" name="b"/><testcase classname="t" name="c"/>'


def test_exit_zero_with_a_clean_file_is_a_pass(tmp_path):
    mod = _load("test-junit")
    for wrap in ("testsuite", "testsuites"):
        res = mod.build(_junit(tmp_path, OK3, wrap), 0, "abc")
        assert res["verdict"] == "pass" and res["payload"]["passed"] == 3


def test_exit_zero_and_a_file_that_lists_failures_is_not_a_pass(tmp_path):
    mod = _load("test-junit")
    bad = OK3 + '<testcase classname="t" name="d"><failure/></testcase>'
    assert mod.build(_junit(tmp_path, bad), 0, "abc")["verdict"] == "none"


@pytest.mark.parametrize("code", [2, 3, 4, 5, 124, 137])
def test_any_exit_code_other_than_zero_or_one_is_no_verdict(tmp_path, code):
    mod = _load("test-junit")
    assert mod.build(_junit(tmp_path, OK3), code, "abc")["verdict"] == "none"


def test_exit_zero_without_a_readable_file_or_without_tests_is_no_verdict(tmp_path):
    mod = _load("test-junit")
    assert mod.build(str(tmp_path / "missing.xml"), 0, "abc")["verdict"] == "none"
    broken = tmp_path / "broken.xml"
    broken.write_text("<testsuite")
    assert mod.build(str(broken), 0, "abc")["verdict"] == "none"
    assert mod.build(_junit(tmp_path, ""), 0, "abc")["verdict"] == "none"


def test_exit_one_is_a_fail_even_without_a_file(tmp_path):
    mod = _load("test-junit")
    res = mod.build(str(tmp_path / "missing.xml"), 1, "abc")
    assert res["verdict"] == "fail" and res["payload"]["failing"] == []


def test_failing_cases_are_capped_and_clipped(tmp_path):
    mod = _load("test-junit")
    many = "".join(
        f'<testcase classname="t{"x" * 400}" name="c{i}"><failure/></testcase>' for i in range(80)
    )
    res = mod.build(_junit(tmp_path, many), 1, "abc")
    assert len(res["payload"]["failing"]) == 50
    assert all(len(c) <= 200 for c in res["payload"]["failing"])


def test_a_bad_exit_code_argument_is_a_usage_error(tmp_path):
    mod = _load("test-junit")
    assert mod.main(["test-junit.py", str(tmp_path / "x.xml"), "not-a-number", "abc"]) == 2


# --- the workflow ----------------------------------------------------------------------------------


def _workflow():
    text = (ROOT / ".github/workflows/asdd-test.yml").read_text()
    return text, yaml.safe_load(text)


def test_the_workflow_only_runs_on_main_and_never_on_pull_requests():
    text, _ = _workflow()
    on = text.split("\njobs:")[0]
    assert "push:" in on and "branches: [main]" in on
    assert "pull_request" not in on  # it executes the code under test: merged code only
    assert "git merge-base --is-ancestor" in text  # a hand-started run must name a commit on main


def test_no_model_and_no_secret_reach_the_test_run():
    text, doc = _workflow()
    assert (
        "goose" not in text.lower()
        and "ASDD_RUNTIME_TOKEN" not in text
        and "ASDD_MODEL" not in text
    )
    steps = doc["jobs"]["test"]["steps"]
    runner = [s for s in steps if "operate/test.sh" in s.get("run", "")]
    assert len(runner) == 1
    assert "env" not in runner[0]  # the step that runs the merged code is given nothing
    script = (OPERATE / "test.sh").read_text()
    assert "goose" not in script.lower() and "TOKEN" not in script


# --- the report renderer (test-report.py) -----------------------------------------------------------


def test_report_renders_pass_and_fail_and_never_calls_garbage_a_pass():
    mod = _load("test-report")
    ok = mod.render(
        {"verdict": "pass", "payload": {"tested": "pytest", "passed": 5, "failed": 0}}, "abc", "m"
    )
    assert "**PASS**" in ok and "Passed: 5 / Failed: 0" in ok
    bad = mod.render({"verdict": "fail", "payload": {"failing": ["t1", "t2"]}}, "abc", "m")
    assert "**FAIL**" in bad and "`t1`" in bad and "`t2`" in bad
    for junk in ({}, {"verdict": ""}, {"verdict": "PASS!!"}, {"verdict": ["pass"]}):
        assert "NO VERDICT" in mod.render(junk, "abc", "m")


def test_report_says_pytest_ran_it_when_a_pytest_result_has_no_verdict():
    mod = _load("test-report")
    text = mod.render({"verdict": "none", "runner": "pytest"}, "abc", "pytest")
    assert "NO VERDICT (pytest did not give a usable result)" in text
    assert "deterministic: pytest" in text and "test_runner role" not in text


def test_report_caps_what_a_result_file_can_put_in_a_pr_comment():
    mod = _load("test-report")
    text = mod.render(
        {
            "verdict": "fail",
            "reasoning": "x" * 5000,
            "payload": {"tested": "y" * 5000, "failing": [f"case{i}" for i in range(100)]},
        },
        "abc",
        "m",
    )
    assert len(text) < 3000
    assert text.count("`case") == 20  # at most 20 failing cases are listed


def test_report_survives_a_missing_or_corrupt_result_file(tmp_path):
    mod = _load("test-report")
    assert mod.load(tmp_path / "nope.json") == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert mod.load(bad) == {}
    listy = tmp_path / "list.json"
    listy.write_text("[1, 2]")
    assert mod.load(listy) == {}
