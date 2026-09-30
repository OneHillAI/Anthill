"""The release gate (scripts/check-release.sh) enforces that every version is changelogged.

Running it here in the normal suite means a version bump without a matching CHANGELOG entry
fails at PR time, not just when the release tag is pushed.
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check-release.sh"
_FRAG_RE = re.compile(r"\.(added|changed|deprecated|removed|fixed|security)\.md$")


def _run(*args):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True)


def _pyproject_version():
    text = (ROOT / "pyproject.toml").read_text()
    return re.search(r'(?m)^version\s*=\s*"([^"]+)"', text).group(1)


def _pending_fragments():
    out = subprocess.run(
        ["git", "ls-files", "changelog.d/"], cwd=ROOT, capture_output=True, text=True
    ).stdout
    return [ln for ln in out.splitlines() if _FRAG_RE.search(ln)]


def test_current_version_is_changelogged():
    """The version pyproject declares right now must have a CHANGELOG section (gate passes)."""
    ver = _pyproject_version()
    r = _run(f"v{ver}")
    assert r.returncode == 0, f"release gate failed for current version v{ver}:\n{r.stderr}"


def test_unchangelogged_version_is_rejected():
    r = _run("v99.99.99")
    assert r.returncode != 0
    assert "CHANGELOG" in r.stderr  # the failure names the missing changelog entry


def test_leading_v_is_optional():
    ver = _pyproject_version()
    assert _run(ver).returncode == 0  # works with or without the "v" prefix


def test_default_gate_ignores_pending_fragments():
    # #522: between releases changelog.d/ legitimately holds fragments for the NEXT version. The
    # default gate (what the test suite and PR-time CI run) must not fail on that normal steady state.
    if not _pending_fragments():
        pytest.skip("no pending fragments to exercise the regression")
    ver = _pyproject_version()
    r = _run(f"v{ver}")
    assert r.returncode == 0, f"default gate should ignore pending fragments:\n{r.stderr}"


def test_release_mode_flags_unassembled_fragments():
    # At a real release cut the human assembles fragments first (scripts/build_changelog.py); --release
    # enforces that none were left behind. With fragments pending, the release-mode gate must fail.
    if not _pending_fragments():
        pytest.skip("no pending fragments to exercise the release-mode gate")
    ver = _pyproject_version()
    r = _run("--release", f"v{ver}")
    assert r.returncode != 0 and "changelog.d" in r.stderr
