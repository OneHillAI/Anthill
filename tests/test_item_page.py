"""An item's own page (#315): what it is, where it came from, and which of the viewer's chats used it. Model-free."""

from anthill.web.db import ChatMessage, Conversation
from anthill.wiki import answer_record
from tests.test_remove_an_item import A, B, _personal, _soup
from tests.test_upload_status import _login, _setup

DOC = f"doc:{A} (page 1)"


def _chat(app_mod, who, ws, sources, *, title="A chat", answers=1):
    """A conversation of ``who`` with ``answers`` answers, each given ``sources`` (page slugs and ``doc:`` tags)."""
    uid, org_id, _role = who
    sess = app_mod._SessionFactory()
    try:
        conv = Conversation(user_id=uid, org_id=org_id, plane="solo", title=title)
        sess.add(conv)
        sess.flush()
        for _ in range(answers):
            sess.add(ChatMessage(conversation_id=conv.id, role="user", content="q"))
            sess.add(
                ChatMessage(
                    conversation_id=conv.id,
                    role="assistant",
                    content="a",
                    manifest=answer_record.dumps(answer_record.from_sources(sources, [ws])),
                )
            )
        sess.commit()
        return conv.id
    finally:
        sess.close()


def _item(client, kind, name, **query):
    return _soup(
        client,
        "/wiki/item?"
        + "&".join(f"{k}={v}" for k, v in {"kind": kind, "name": name, **query}.items()),
    )


def test_a_page_says_what_it_is_where_it_came_from_and_what_it_was_made_from(tmp_path, monkeypatch):
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _personal(ids)
    _login(client, ids["owner"])
    soup = _item(client, "page", "ferry")
    story = soup.find(id="item-story")
    text = " ".join(story.get_text(" ", strip=True).split())
    assert story.find("h2").get_text() == "Ferry" and "Page in Your wiki" in text
    assert "AI-written, unchecked" in text and "Written by a model" in text
    assert "Version 1 (the earlier text is not kept)" in text
    assert story.find("a", string="ferry.pdf")["href"] == f"/wiki/raw/{A}?scope=personal"
    assert soup.select_one("title").get_text().startswith("About Ferry")


def test_a_document_lists_its_pages_and_offers_its_text(tmp_path, monkeypatch):
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _personal(ids)
    _login(client, ids["owner"])
    story = _item(client, "document", A).find(id="item-story")
    assert story.find("h2").get_text() == "ferry.pdf"
    assert sorted(a.get_text() for a in story.select("dd a")) == ["Ferry", "Ferry checked"]
    actions = story.find(id="item-actions")
    assert actions.find("a", string="text read from it")["href"].startswith("/wiki/text/")
    assert actions.find("a", string="Edit") is None  # a document is removed, not edited
    assert actions.find("a", string="Remove")["href"].startswith("/wiki/remove?kind=original")


def test_the_chats_that_used_an_item_are_listed_newest_first_with_their_answers(
    tmp_path, monkeypatch
):
    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    older = _chat(app_mod, ids["owner"], ws, ["ferry"], title="Ferry times", answers=2)
    newer = _chat(app_mod, ids["owner"], ws, ["ferry", DOC], title="Ferry and quokkas")
    _chat(app_mod, ids["owner"], ws, ["walrus"], title="Only the walrus")
    _login(client, ids["owner"])
    used = _item(client, "page", "ferry").find(id="item-story")
    links = [(a.get_text(), a["href"]) for a in used.select("ul li a")]
    assert links == [("Ferry and quokkas", f"/chat/{newer}"), ("Ferry times", f"/chat/{older}")]
    assert "2 answers" in used.get_text() and "1 answer" in used.get_text()
    doc = _item(client, "document", A).find(id="item-story")
    assert [a.get_text() for a in doc.select("ul li a")] == ["Ferry and quokkas"]
    walrus = _item(client, "page", "ferry-checked").find(id="item-story")
    assert "None of your chats has given this to an answer yet." in walrus.get_text()


def test_another_persons_chat_is_never_listed(tmp_path, monkeypatch):
    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _chat(app_mod, ids["mate"], ws, ["ferry"], title="Someone else's chat")
    _login(client, ids["owner"])
    text = _item(client, "page", "ferry").find(id="item-story").get_text()
    assert "Someone else's chat" not in text and "None of your chats" in text


def test_a_long_list_is_cut_and_counted(tmp_path, monkeypatch):
    app_mod, _eng, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    for i in range(23):
        _chat(app_mod, ids["owner"], ws, ["ferry"], title=f"Chat {i}")
    _login(client, ids["owner"])
    story = _item(client, "page", "ferry").find(id="item-story")
    assert len(story.select("ul li")) == 20 and "and 3 more chats" in story.get_text()


def test_a_page_with_no_record_still_has_a_page(tmp_path, monkeypatch):
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    (ws.wiki / "loose.md").write_text("# Loose\n\nNo record.\n", encoding="utf-8")
    _login(client, ids["owner"])
    story = _item(client, "page", "loose").find(id="item-story")
    text = story.get_text(" ", strip=True)
    assert (
        "Loose" in text
        and "Nothing recorded about this page yet." in text
        and "Version" not in text
    )


def test_the_page_view_and_the_library_lead_to_it(tmp_path, monkeypatch):
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _personal(ids)
    _login(client, ids["owner"])
    view = _soup(client, "/wiki/page/ferry?scope=personal")
    assert (
        view.find("a", string="About this page")["href"]
        == "/wiki/item?kind=page&name=ferry&scope=personal"
    )
    library_row = next(
        tr
        for tr in _soup(client, "/wiki").select("table.lib-table tr")
        if tr.a.get_text() == "Ferry"
    )
    assert (
        library_row.find("a", string="Details")["href"]
        == "/wiki/item?kind=page&name=ferry&scope=personal"
    )
    assert client.get(library_row.find("a", string="Details")["href"]).status_code == 200


def test_a_missing_item_a_wrong_kind_and_a_path_are_not_found(tmp_path, monkeypatch):
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _personal(ids)
    _login(client, ids["owner"])
    for url in (
        "/wiki/item?kind=page&name=no-such-page",
        "/wiki/item?kind=document&name=no-such-file.pdf",
        "/wiki/item?kind=original&name=ferry",
        "/wiki/item?kind=page&name=../ferry",
        f"/wiki/item?kind=document&name=../raw/{A}",
    ):
        assert client.get(url).status_code == 404, url
    assert B in " ".join(a["href"] for a in _soup(client, "/wiki").select("table.lib-table a"))


def test_a_team_wiki_needs_membership(tmp_path, monkeypatch):
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _login(client, ids["owner"])
    assert client.get("/wiki/item?kind=page&name=ferry&scope=team&team_id=999").status_code == 403
