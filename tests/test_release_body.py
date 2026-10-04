"""The GitHub release page says what changed (spec: release-notes-and-credits R6).

The Release workflow writes fixed download instructions; scripts/release_body.py adds the version's changelog
section to them. These tests pin the script on small synthetic inputs, then run it on the REAL workflow text and
the REAL CHANGELOG.md, so the two cannot drift apart unnoticed. No network.
"""

import importlib.util
import re
import textwrap
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/release_body.py"

_spec = importlib.util.spec_from_file_location("release_body", SCRIPT)
rb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rb)

CHANGELOG = textwrap.dedent(
    """\
    # Changelog

    ## [Unreleased]

    ## [1.2.0] - 2026-10-04

    A short plain summary of this release.
    Second line of the summary.

    ### Added

    - **A new thing.** What it does.

    ### Contributors

    - **someone** (1 change)

    ## [1.1.0] - 2026-09-01

    ### Fixed

    - **An old fix.**
    """
)
BASE = "## Anthill v1.2.0\n\nDownload it.\n\n### Build from source (Mac fallback)\nclone it\n"


def test_section_is_the_versions_text_without_its_heading():
    got = rb.section(CHANGELOG, "1.2.0")
    assert got.startswith("A short plain summary")
    assert "An old fix" not in got and "## [1.2.0]" not in got and "Unreleased" not in got


def test_a_missing_version_is_an_error():
    with pytest.raises(rb.MissingSection):
        rb.section(CHANGELOG, "9.9.9")


def test_a_version_is_matched_exactly_not_by_prefix():
    with pytest.raises(rb.MissingSection):
        rb.section(CHANGELOG, "1.2")


def test_highlights_are_the_text_before_the_first_heading():
    highlights, details = rb.split(rb.section(CHANGELOG, "1.2.0"))
    assert highlights == "A short plain summary of this release.\nSecond line of the summary."
    assert details.startswith("### Added") and "### Contributors" in details


def test_the_body_has_highlights_under_the_title_and_changes_before_build_from_source():
    body = rb.compose(BASE, CHANGELOG, "1.2.0")
    order = [
        body.index("## Anthill v1.2.0"),
        body.index("A short plain summary"),
        body.index("Download it."),
        body.index("## What's changed in this version"),
        body.index("**A new thing.**"),
        body.index("### Contributors"),
        body.index("### Build from source"),
    ]
    assert order == sorted(order)
    # Markdown paragraphs: the title, the Highlights and the download text are separated by blank lines.
    assert "## Anthill v1.2.0\n\nA short plain summary" in body
    assert "Second line of the summary.\n\nDownload it." in body


def test_a_section_with_no_highlights_leaves_no_gap_and_still_lists_the_changes():
    body = rb.compose(BASE, CHANGELOG, "1.1.0")
    assert body.startswith("## Anthill v1.2.0\n\nDownload it.")
    assert "**An old fix.**" in body and "\n\n\n" not in body


def test_without_a_build_from_source_part_the_changes_are_appended():
    body = rb.compose("## Anthill v1.2.0\n\nDownload it.\n", CHANGELOG, "1.2.0")
    assert body.rstrip().endswith("- **someone** (1 change)")


def test_the_cli_writes_the_page_and_fails_on_a_missing_version(tmp_path):
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG)
    (tmp_path / "base.md").write_text(BASE)
    args = ["--changelog", str(tmp_path / "CHANGELOG.md"), "--base", str(tmp_path / "base.md")]
    assert rb.main(["--version", "1.2.0", *args, "--out", str(tmp_path / "out.md")]) == 0
    assert "What's changed in this version" in (tmp_path / "out.md").read_text()
    assert rb.main(["--version", "7.7.7", *args, "--out", str(tmp_path / "none.md")]) == 1
    assert not (tmp_path / "none.md").exists()


def _release_notes_step():
    wf = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    steps = [s for job in wf["jobs"].values() for s in job["steps"]]
    return next(s for s in steps if s.get("name") == "Write release notes"), steps


def test_the_workflow_builds_the_body_with_the_script_and_publishes_that_file():
    step, steps = _release_notes_step()
    run = step["run"]
    assert "scripts/release_body.py" in run and '--version "${GITHUB_REF_NAME#v}"' in run
    assert "notes-base.md" in run and "notes.md" in run
    publish = next(s for s in steps if s.get("name") == "Publish Release")
    assert publish["with"]["body_path"].endswith("/notes.md")


def test_the_script_runs_on_the_real_workflow_text_and_the_real_changelog():
    step, _ = _release_notes_step()
    heredoc = re.search(r"<<'EOF'\n(.*?)\n\s*EOF", step["run"], re.S).group(1)
    base = textwrap.dedent(heredoc).replace("${{ github.ref_name }}", "v1.0.0")
    body = rb.compose(base, (ROOT / "CHANGELOG.md").read_text(), "1.0.0")
    assert "Download for Mac" in body
    assert body.index("## What's changed in this version") < body.index("### Build from source")
    assert "## Anthill v1.0.0" in body.splitlines()[0]
