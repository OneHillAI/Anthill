"""Issue #489: the Security-audit 'Check for UNKNOWN licenses' gate must allow known-but-unclassified
packages (e.g. cuda-toolkit, which ships no license classifier so pip-licenses reports UNKNOWN) while
still hard-failing on a genuinely undeterminable license. This runs the *actual* gate script extracted
from the workflow against synthetic reports, so the allowlist can't silently regress."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/security-audit.yml"


def _gate_script() -> str:
    wf = yaml.safe_load(_WORKFLOW.read_text())
    for job in wf["jobs"].values():
        for step in job.get("steps", []):
            if step.get("name", "").startswith("Check for UNKNOWN"):
                return re.search(r"<<'PYEOF'\n(.*)\nPYEOF", step["run"], re.S).group(1)
    raise AssertionError("could not find the 'Check for UNKNOWN licenses' gate step")


def _run_gate(tmp_path, licenses) -> int:
    report = tmp_path / "licenses.json"
    report.write_text(json.dumps(licenses))
    code = _gate_script().replace('open("/tmp/licenses.json")', f"open({str(report)!r})")
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).returncode


def test_allowlisted_unclassified_package_passes(tmp_path):
    rc = _run_gate(
        tmp_path,
        [
            {"Name": "requests", "Version": "2.32", "License": "Apache-2.0"},
            {"Name": "cuda-toolkit", "Version": "13.0.3.0", "License": "UNKNOWN"},
        ],
    )
    assert rc == 0, "cuda-toolkit (allowlisted) should not fail the gate"


def test_allowlist_matches_normalized_name(tmp_path):
    # pip metadata may report the underscore form; the gate normalizes before matching.
    rc = _run_gate(
        tmp_path, [{"Name": "cuda_toolkit", "Version": "13.0.3.0", "License": "UNKNOWN"}]
    )
    assert rc == 0


def test_genuinely_unknown_license_still_fails(tmp_path):
    rc = _run_gate(
        tmp_path,
        [
            {"Name": "cuda-toolkit", "Version": "13.0.3.0", "License": "UNKNOWN"},
            {"Name": "sketchylib", "Version": "0.1", "License": " UNKNOWN "},
        ],
    )
    assert rc == 1, "a package that is not allowlisted must still hard-fail"


def test_all_known_licenses_pass(tmp_path):
    rc = _run_gate(tmp_path, [{"Name": "requests", "Version": "2.32", "License": "Apache-2.0"}])
    assert rc == 0
