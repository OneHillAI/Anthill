"""A new stable release starts as a pre-release (spec: beta-release-lane R11).

The Release workflow publishes a release minutes before the app files exist, so as the latest release it sent
visitors to a download that was not there and left installed apps without an update feed (v1.0.1, fixed by hand).
Both stable workflows now publish a pre-release; a release owner makes it the latest release by hand once it is
complete and checked. No network.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(name):
    return (ROOT / ".github/workflows" / name).read_text()


def test_both_stable_workflows_publish_a_pre_release_that_is_not_the_latest():
    release, desktop = _text("release.yml"), _text("desktop-release.yml")
    assert re.search(r"(?m)^\s+prerelease: true$", release)
    assert re.search(r"(?m)^\s+make_latest: false$", release)
    assert re.search(r"(?m)^\s+prerelease: true$", desktop)
    assert "prerelease: false" not in release + desktop


def test_the_release_doc_tells_the_owner_how_to_make_it_latest():
    doc = (ROOT / "docs/releasing.md").read_text()
    assert "Set as the latest release" in doc and "pre-release" in doc
