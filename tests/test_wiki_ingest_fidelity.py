"""Ingest must not change what a research note says (#150). A small local model asked to summarise
short interview notes reworded a verbatim quote into a claim, gave the client a request that a
participant made, invented a page title and invented facts. Two of those are guarded without relying
on the model: the page title is the source's own H1, and every quoted passage in the source appears
in the page word for word (a reworded one is restored under `## Quotes`). The prompt asks the model
for the rest. Model-free: the backend returns the reworded page the issue reported."""

from __future__ import annotations

from anthill.wiki import prompts
from anthill.wiki.ingest import _source_quotes, ingest
from anthill.wiki.workspace import Workspace

COURIER_NOTE = (
    "# Interview: courier (P11), 19 Sep 2026\n"
    "Client: Pierogi&Co delivery app redesign (confidential)\n"
    "- Map pins inaccurate in Gdansk old town; addresses missing gate codes.\n"
    '- Wants a single "can\'t deliver" button with reason codes.\n'
    '- Quote: "The app thinks every street is a straight line."\n'
)

REWORDED_PAGE = (
    "# Courier App Feedback\n"
    "The page summarises feedback on the delivery app.\n\n"
    "- Map pins are inaccurate and gate codes are missing.\n"
    "- The client wants a single can't deliver button with reason codes.\n"
    "- The app incorrectly assumes every street is a straight line.\n\n"
    "## Related\n[[delivery]]\n"
)


class _Backend:
    model = "test-model"

    def __init__(self, reply: str):
        self._reply = reply

    def chat(self, messages, **kwargs):
        return self._reply


def _ingest(tmp_path, name: str, note: str, reply: str) -> tuple[str, str]:
    ws = Workspace(tmp_path / "wiki")
    ws.init()
    ws.write_page("Delivery", "# Delivery\n\nParcels.")  # so [[delivery]] is not a dangling link
    source = tmp_path / name
    source.write_text(note)
    captured: dict = {}
    ingest(ws, source, _Backend(reply), on_write=lambda t, p: captured.update(title=t, page=p))
    return captured["title"], captured["page"]


def test_the_page_title_is_the_source_h1_not_the_model_title(tmp_path):
    title, page = _ingest(tmp_path, "courier.md", COURIER_NOTE, REWORDED_PAGE)
    assert title == "Interview: courier (P11), 19 Sep 2026"
    assert page.startswith("# Interview: courier (P11), 19 Sep 2026\n")
    assert "Courier App Feedback" not in page


def test_a_source_without_an_h1_keeps_the_model_title(tmp_path):
    title, _ = _ingest(
        tmp_path, "notes.txt", "Standup was moved to 10am.", "# Standup Time\nMoved."
    )
    assert title == "Standup Time"


def test_a_reworded_quote_is_restored_word_for_word_before_related(tmp_path):
    _, page = _ingest(tmp_path, "courier.md", COURIER_NOTE, REWORDED_PAGE)
    assert '> "The app thinks every street is a straight line."' in page
    assert page.index("## Quotes") < page.index("## Related")
    assert "[[delivery]]" in page.split("## Related")[1]  # the Related section is kept


def test_a_quote_the_model_kept_is_not_duplicated(tmp_path):
    kept = (
        "# Courier\nSummary.\n\n"
        '- Quote: "The app thinks every street is a straight line."\n'
        '- Wants a single "can\'t deliver" button.\n'
    )
    _, page = _ingest(tmp_path, "courier.md", COURIER_NOTE, kept)
    assert "## Quotes" not in page
    assert page.count("The app thinks every street is a straight line.") == 1


def test_quotes_survive_when_the_model_returns_no_related_section(tmp_path):
    _, page = _ingest(tmp_path, "courier.md", COURIER_NOTE, "# X\nSummary only.")
    assert '> "The app thinks every street is a straight line."' in page


def test_the_page_is_written_under_the_source_title(tmp_path):
    ws = Workspace(tmp_path / "wiki")
    ws.init()
    source = tmp_path / "courier.md"
    source.write_text(COURIER_NOTE)
    path = ingest(ws, source, _Backend(REWORDED_PAGE))
    assert path.stem == "interview-courier-p11-19-sep-2026"
    assert "straight line" in path.read_text()


def test_source_quotes_takes_straight_and_curly_quotes_and_skips_scare_quotes():
    text = 'He said "this is a full quote" and “so is this one”, but "x" and "ok" are not.'
    assert _source_quotes(text) == ["this is a full quote", "so is this one"]


def test_source_quotes_are_distinct_and_whitespace_normalised():
    text = '"two  words here"\n"two words here"'
    assert _source_quotes(text) == ["two words here"]


def test_the_prompt_asks_for_verbatim_quotes_attribution_and_nothing_added():
    system = prompts.summarize_source("schema", "n.md", "body")[0].content
    assert "word for word" in system
    assert "who said, wanted or did what" in system
    assert "Add nothing the source does not say" in system
    assert "PostgreSQL" not in system  # the old example invited confident invented claims
