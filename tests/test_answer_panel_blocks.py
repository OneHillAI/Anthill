"""The answer panel lists everything the answer was given, not only pages and documents (#351). Model-free: the panel
is made from a stored record, here written by hand."""

import json

from bs4 import BeautifulSoup

import anthill.web.app as app_mod
from anthill.web import answer_panel

ITEM = {
    "kind": "page",
    "slug": "cabinet",
    "title": "Cabinet",
    "space": "personal",
    "space_ref": "user-1",
    "id": "a" * 32,
    "version": 1,
    "hash": "h" * 64,
    "origin": "own",
    "review": "checked",
}


def _stored(blocks=None, **extra) -> str:
    record = {"v": 1, "items": [ITEM], "more": 0, **extra}
    if blocks is not None:
        record["blocks"] = blocks
    return json.dumps(record)


def _block(kind, **more):
    return {"type": kind, **more, "hash": "0123456789abcdef", "length": 120}


def _html(stored: str) -> BeautifulSoup:
    return BeautifulSoup(app_mod._answer_record_html(stored), "html.parser")


def _also(soup) -> list[str]:
    box = soup.select_one(".answer-also")
    return [li.get_text(" ", strip=True) for li in box.select("li")] if box else []


def test_every_kind_of_thing_the_answer_was_given_has_a_line_in_plain_words():
    blocks = [
        _block("rules"),
        _block("profile"),
        _block("memory"),
        _block("context"),
        _block("history_turn", role="user"),
        _block("history_turn", role="assistant"),
        _block("history_turn", role="user"),
        _block(
            "web_evidence", url="https://example.org/a/page?q=1", fetched="2026-11-25T10:00:00Z"
        ),
    ]
    soup = _html(_stored(blocks))
    assert _also(soup) == [
        "Your rules",
        "Your profile",
        "Notes Anthill remembered",
        "The context this run started with",
        "3 earlier messages in this chat",
        "From the web (not checked): example.org",
    ]
    link = soup.select_one(".answer-also a")
    assert link["href"] == "https://example.org/a/page?q=1"
    assert link["target"] == "_blank" and "noopener" in link["rel"]
    assert "caused" not in soup.get_text().lower() and "relied" not in soup.get_text().lower()


def test_one_earlier_message_is_singular():
    assert _also(_html(_stored([_block("history_turn", role="user")]))) == [
        "1 earlier message in this chat"
    ]


def test_a_record_with_only_items_or_from_before_the_blocks_existed_looks_as_it_did():
    for stored in (_stored(), _stored([]), json.dumps({"v": 1, "items": [ITEM], "more": 0})):
        soup = _html(stored)
        assert soup.select_one(".answer-also") is None and soup.select_one(".answer-by") is None
        assert [a.get_text() for a in soup.select("li a")] == ["Cabinet"]


def test_a_kind_the_screen_does_not_know_is_left_out_not_shown_raw():
    soup = _html(_stored([_block("rules"), _block("something_new"), _block("core_page")]))
    assert _also(soup) == ["Your rules"]
    assert "something_new" not in soup.get_text()


def test_only_an_http_or_https_address_becomes_a_link():
    blocks = [
        _block("web_evidence", url="javascript:alert(1)"),
        _block("web_evidence", url="file:///etc/passwd"),
        _block("web_evidence", url="https://"),
        _block("web_evidence", url="http://ok.example/x"),
    ]
    soup = _html(_stored(blocks))
    hrefs = [a["href"] for a in soup.select(".answer-also a")]
    assert hrefs == ["http://ok.example/x"]
    assert "javascript:alert(1)" in soup.get_text()  # shown as plain text, never as a link


def test_text_in_the_record_is_escaped():
    blocks = [_block("web_evidence", url='https://x.example/"><script>alert(1)</script>')]
    html = app_mod._answer_record_html(_stored(blocks, model="<b>m</b>", route="chat"))
    assert "<script>" not in html and "<b>m</b>" not in html


def test_the_answered_by_line_names_the_model_and_how_when_the_record_has_them():
    assert _html(_stored(model="qwen3.5:9b", route="chat")).select_one(".answer-by").get_text() == (
        "Answered by qwen3.5:9b, in chat"
    )
    assert _html(_stored(model="qwen3.5:9b", route="agent")).select_one(
        ".answer-by"
    ).get_text() == ("Answered by qwen3.5:9b, in agent mode")
    assert (
        _html(_stored(model="qwen3.5:9b", route="a-route-not-known"))
        .select_one(".answer-by")
        .get_text()
        == "Answered by qwen3.5:9b"
    )
    assert _html(_stored(route="chat")).select_one(".answer-by") is None  # no model: no line


def test_the_live_panel_and_the_reloaded_panel_are_made_the_same_way():
    stored = _stored(
        [_block("rules"), _block("history_turn", role="user")], model="m", route="chat"
    )
    live = app_mod._answer_record_html(stored)  # what the stream sends as record_html
    reloaded = app_mod.templates.get_template("_answer_record.html").render(
        panel=answer_panel.panel(stored)
    )  # what the saved chat renders
    assert live == reloaded


def test_a_damaged_record_shows_nothing_and_does_not_fail():
    for stored in ("", "not json", "[]", '{"items": "x"}', '{"items": [], "blocks": 5}'):
        assert answer_panel.panel(stored)["rows"] == []
        assert app_mod._answer_record_html(stored).strip() == ""
