"""Guards on the Windows full-suite workflow (.github/workflows/windows-full-suite.yml).

It is advisory (it must not turn a pull request red), cheap to leave alone (nightly, by hand, or on a change to itself),
and pinned the way the other workflows are. A workflow cannot be run by a test, so these pin the properties that matter.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"


@pytest.fixture(scope="module")
def text():
    return (WORKFLOWS / "windows-full-suite.yml").read_text()


@pytest.fixture(scope="module")
def wf(text):
    return yaml.safe_load(text)


def _on(doc):
    return doc.get("on") or doc.get(True)  # YAML reads a bare `on:` key as the boolean True


def _steps(wf):
    return wf["jobs"]["suite"]["steps"]


def test_it_runs_nightly_by_hand_and_on_a_change_to_itself_only(wf):
    on = _on(wf)
    assert set(on) == {"workflow_dispatch", "schedule", "pull_request"}  # never on a push to main
    assert on["pull_request"]["paths"] == [
        ".github/workflows/windows-full-suite.yml",
        "scripts/windows_suite_summary.py",
    ]
    assert re.fullmatch(r"\d+ \d+ \* \* \*", on["schedule"][0]["cron"])  # once a day


def test_it_can_read_the_repository_and_nothing_else(wf, text):
    assert wf["permissions"] == {"contents": "read"}
    assert "secrets." not in text and "GITHUB_TOKEN" not in text


def test_it_runs_on_windows_with_the_same_install_and_flags_as_the_linux_test_job(wf):
    job = wf["jobs"]["suite"]
    assert job["runs-on"] == "windows-latest"
    ci = yaml.safe_load((WORKFLOWS / "ci.yml").read_text())
    linux = {s.get("name"): s for s in ci["jobs"]["test"]["steps"]}
    mine = {s.get("name"): s for s in _steps(wf)}
    assert 'pip install -e ".[ci]"' in mine["Install"]["run"]
    assert 'pip install -e ".[ci]"' in linux["Install"]["run"]
    flags = re.search(r"pytest (-n \d+ --dist loadscope)", linux["Test"]["run"]).group(1)
    assert flags in mine["Test"]["run"]  # the same worker setup, so a result is comparable


def test_the_test_step_cannot_fail_the_job_and_the_report_steps_always_run(wf):
    steps = {s.get("name"): s for s in _steps(wf)}
    assert steps["Test"]["continue-on-error"] is True
    assert "--junitxml=windows-suite.xml" in steps["Test"]["run"]
    assert steps["Summarise what failed on Windows"]["if"] == "always()"
    assert "GITHUB_STEP_SUMMARY" in steps["Summarise what failed on Windows"]["run"]
    assert steps["Keep the junit file"]["if"] == "always()"
    assert steps["Keep the junit file"]["with"]["retention-days"] == 7


def test_every_action_is_pinned_to_a_commit(wf):
    uses = [s["uses"] for s in _steps(wf) if "uses" in s]
    assert uses
    for ref in uses:
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}( .*)?", ref), ref


def test_it_leaves_no_test_out_that_the_linux_job_runs(wf):
    # The Linux job ignores nothing, so neither does this one: a left-out file would be a silent gap in the Windows list.
    assert "--ignore" not in {s.get("name"): s for s in _steps(wf)}["Test"]["run"]
    assert "--deselect" not in {s.get("name"): s for s in _steps(wf)}["Test"]["run"]


def test_a_hung_test_ends_the_test_step_not_the_job(wf):
    # The step's own limit is well under the job's, so the step times out first (absorbed by continue-on-error) and the
    # summary and junit upload still have time to run.
    job = wf["jobs"]["suite"]
    step = {s.get("name"): s for s in _steps(wf)}["Test"]
    assert step["continue-on-error"] is True
    assert 0 < step["timeout-minutes"] <= job["timeout-minutes"] - 20


def test_runs_of_different_events_never_cancel_each_other(wf):
    # A run by hand must not cancel the nightly one (or the other way round); a new push to a pull request still
    # replaces the older run of that pull request.
    group = wf["concurrency"]["group"]
    assert "${{ github.event_name }}" in group and "${{ github.ref }}" in group
    assert wf["concurrency"]["cancel-in-progress"] is True
