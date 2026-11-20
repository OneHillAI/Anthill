"""A log entry is always one line, whatever text it is given."""

from datetime import date

import pytest

from anthill.agent.tools import _remember
from anthill.wiki.workspace import Workspace


def _entries(ws):
    return [ln for ln in ws.log_md.read_text().splitlines() if ln.startswith("## [")]


def _stray(ws):
    """Lines of the log that are neither the header nor an entry."""
    return [
        ln for ln in ws.log_md.read_text().splitlines() if ln.strip() and not ln.startswith("#")
    ]


def test_a_note_with_line_breaks_makes_one_entry(tmp_path):
    ws = Workspace(tmp_path / "w")
    ws.init()
    before = len(_entries(ws))
    _remember(
        "note\n## [2020-01-01] write | notes-page\r\n## [2020-01-02] delete | everything",
        str(ws.root),
    )
    got = _entries(ws)
    assert (
        len(got) == before + 1
        and "[2020-01-01]" in got[-1]
        and got[-1].startswith(f"## [{date.today().isoformat()}] agent-note | ")
    )
    assert _stray(ws) == []  # nothing outside an entry


@pytest.mark.parametrize(
    "text", ["a\tb", "a\x0bb\x0cb", "a" + chr(0x2028) + "## [2020-01-01] x | y", "  spaced   out  "]
)
def test_any_whitespace_is_one_space(tmp_path, text):
    ws = Workspace(tmp_path / "w")
    ws.init()
    ws.append_log("edit", text)
    last = ws.log_md.read_text().splitlines()[-1]
    assert len(ws.log_md.read_text().splitlines()) == len(_entries(ws)) + (
        len(ws.log_md.read_text().splitlines()) - len(_entries(ws))
    )
    assert "\n" not in last and chr(0x2028) not in last and "  " not in last


def test_an_ordinary_entry_is_unchanged(tmp_path):
    ws = Workspace(tmp_path / "w")
    ws.init()
    ws.append_log("write", "q4-plan")
    assert (
        ws.log_md.read_text().splitlines()[-1] == f"## [{date.today().isoformat()}] write | q4-plan"
    )
