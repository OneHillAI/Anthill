"""The CI workflow runs with a read-only token (docs/specs/ci-read-only-token.md).

Its jobs check out the code, run linters and run tests, so none needs write access. The default is set once
for the whole workflow, and no job may widen it.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


@pytest.fixture(scope="module")
def ci() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def test_the_workflow_default_token_is_read_only(ci):
    assert ci["permissions"] == {"contents": "read"}


def test_no_job_asks_for_more_than_read_access(ci):
    for name, job in ci["jobs"].items():
        granted = job.get("permissions", {})
        assert granted in ({}, "read-all") or all(v == "read" for v in granted.values()), name
        assert "write" not in str(granted), name


def test_every_job_is_still_present(ci):
    assert {"lint", "test", "browser"} <= set(ci["jobs"])
