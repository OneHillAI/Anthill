"""An item's own page (#315): what it is and where it came from. Model-free."""

from tests.test_remove_an_item import A, B, _personal, _soup
from tests.test_upload_status import _login, _setup


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
    assert "Version 1 (no earlier versions yet)" in text
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


def test_a_page_with_earlier_versions_links_to_its_history_and_a_document_says_nothing_of_them(
    tmp_path, monkeypatch
):
    from anthill.wiki import page_store

    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    page_store.keep_previous(ws, "ferry", (ws.wiki / "ferry.md").read_text(encoding="utf-8"))
    _login(client, ids["owner"])
    story = _item(client, "page", "ferry").find(id="item-story")
    link = story.find("a", string="History")
    assert link["href"] == "/wiki/page/ferry/history?scope=personal"
    assert "earlier versions are kept" in story.get_text(" ", strip=True)
    assert "no earlier versions yet" not in story.get_text(" ", strip=True)
    assert "not kept" not in story.get_text()  # the old sentence is gone
    doc = _item(client, "document", A).find(id="item-story")
    assert "earlier" not in doc.get_text(" ", strip=True).lower()
    assert doc.find("a", string="History") is None
