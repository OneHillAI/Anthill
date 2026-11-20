"""The Library: one list of the pages and original documents of a wiki (#312). Model-free. What the page's
markup says is tested here; how it looks is checked on a running copy."""

from anthill.web import library
from anthill.wiki.workspace import Workspace
from tests.test_remove_an_item import A, B, _personal, _soup, _world
from tests.test_upload_status import _login, _setup


def _rows(ws: Workspace) -> dict[str, dict]:
    raw = [
        {"name": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime, "has_text": True}
        for p in sorted(ws.raw.iterdir())
    ]
    return {r["title"]: r for r in library.build(ws, raw, scope="personal")}


# ── what a row says ───────────────────────────────────────────────────────────


def test_pages_and_documents_are_one_list_by_title(tmp_path):
    ws = _world(tmp_path / "user-1")
    rows = library.build(ws, [], scope="personal")
    assert [r["title"] for r in rows] == sorted(
        (r["title"] for r in rows), key=str.casefold
    )  # by title, pages and documents together
    by = _rows(ws)
    assert {t: r["kind"] for t, r in by.items()} == {
        "Ferry": "page",
        "Ferry checked": "page",
        "Walrus": "page",
        "Notes": "page",
        "ferry.pdf": "document",
        "walrus.pdf": "document",
    }


def test_an_ai_written_page_nobody_checked_says_so_and_a_checked_one_does_not(tmp_path):
    by = _rows(_world(tmp_path / "user-1"))
    assert "AI-written, unchecked" in by["Ferry"]["chips"] and by["Ferry"]["unchecked"]
    assert by["Ferry checked"]["chips"] == [] and not by["Ferry checked"]["unchecked"]
    assert by["Notes"]["chips"] == []  # a person's own page


def test_a_page_names_its_document_and_a_document_names_its_pages(tmp_path):
    by = _rows(_world(tmp_path / "user-1"))
    assert [l["title"] for l in by["Ferry"]["links"]] == ["ferry.pdf"]
    assert by["Ferry"]["links"][0]["href"] == f"/wiki/raw/{A}?scope=personal"
    assert sorted(l["title"] for l in by["ferry.pdf"]["links"]) == ["Ferry", "Ferry checked"]
    assert [l["title"] for l in by["walrus.pdf"]["links"]] == ["Walrus"]
    assert by["Notes"]["links"] == []


def test_a_page_with_no_record_or_a_damaged_one_is_listed_without_chips(tmp_path):
    ws = _world(tmp_path / "user-1")
    (ws.wiki / "loose.md").write_text("# Loose\n\nNo record.\n", encoding="utf-8")
    (ws.wiki / "damaged.md").write_text(
        "---\nx-anthill-item:\n  kind: nonsense\n---\n# Damaged\n\nBad record.\n", encoding="utf-8"
    )
    by = _rows(ws)
    assert by["Loose"]["chips"] == [] and by["Damaged"]["chips"] == []
    assert by["Loose"]["kind"] == by["Damaged"]["kind"] == "page"


def test_a_replaced_or_rejected_item_is_listed_with_its_state(tmp_path):
    from anthill.wiki import items

    ws = _world(tmp_path / "user-1")
    rec = items.read_source_record(ws, B)
    rec.review_state = "rejected"
    items.write_source_record(ws, rec)
    page = (ws.wiki / "notes.md").read_text(encoding="utf-8")
    (ws.wiki / "notes.md").write_text(
        page.replace("lifecycle: active", "lifecycle: superseded"), encoding="utf-8"
    )
    by = _rows(ws)
    assert "Rejected" in by["walrus.pdf"]["chips"]
    assert "Replaced" in by["Notes"]["chips"]


def test_the_filters_keep_what_matches(tmp_path):
    rows = list(_rows(_world(tmp_path / "user-1")).values())
    titles = lambda **kw: sorted(r["title"] for r in library.filtered(rows, **kw))  # noqa: E731
    assert titles(kind="documents") == ["ferry.pdf", "walrus.pdf"]
    assert titles(kind="pages") == ["Ferry", "Ferry checked", "Notes", "Walrus"]
    assert titles(unchecked=True) == ["Ferry", "Walrus"]
    assert titles(q=" FERRY ") == ["Ferry", "Ferry checked", "ferry.pdf"]
    assert titles(kind="pages", q="walrus") == ["Walrus"]
    assert len(titles(kind="nonsense")) == 6  # an unknown kind shows everything


# ── the page ────────────────────────────────────────────────────────────────


def test_the_wiki_menu_has_a_library_not_pages_and_files(tmp_path, monkeypatch):
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _personal(ids)
    _login(client, ids["owner"])
    soup = _soup(client, "/wiki")
    tabs = soup.select_one(".settings-tabs").get_text(" ", strip=True)
    assert tabs == "Library Principles"
    card = soup.find(id="wiki-pages-card")
    assert card.find("h2").get_text() == "Library"
    assert len(card.select("table.lib-table tr")) == 6


def test_the_old_pages_and_files_addresses_open_the_library(tmp_path, monkeypatch):
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _personal(ids)
    _login(client, ids["owner"])
    for url in ("/wiki?tab=pages", "/wiki?tab=files", "/wiki?tab=library"):
        assert len(_soup(client, url).select("table.lib-table tr")) == 6, url


def test_a_row_shows_its_chips_its_links_and_its_actions(tmp_path, monkeypatch):
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _personal(ids)
    _login(client, ids["owner"])
    soup = _soup(client, "/wiki")
    ferry = next(tr for tr in soup.select("table.lib-table tr") if tr.a.get_text() == "Ferry")
    assert "AI-written, unchecked" in ferry.get_text()
    assert ferry.find("a", string="ferry.pdf")["href"] == f"/wiki/raw/{A}?scope=personal"
    assert ferry.find("a", string="Edit")["href"] == "/wiki/page/ferry/edit?scope=personal"
    assert ferry.find("a", string="Remove")["href"].startswith("/wiki/remove?kind=page&name=ferry")
    doc = next(tr for tr in soup.select("table.lib-table tr") if tr.a.get_text() == "ferry.pdf")
    assert doc.find("a", string="text read from it")["href"].startswith("/wiki/text/")
    assert "Pages made from it" in doc.get_text() and doc.find("a", string="Edit") is None
    assert doc.find("a", string="Remove")["href"].startswith(
        "/wiki/remove?kind=original&name=ferry-"
    )


def test_filtering_by_the_address(tmp_path, monkeypatch):
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _personal(ids)
    _login(client, ids["owner"])

    def titles(query: str) -> list[str]:
        soup = _soup(client, f"/wiki?{query}")
        return [tr.a.get_text() for tr in soup.select("table.lib-table tr")]

    assert titles("kind=documents") == ["ferry.pdf", "walrus.pdf"]
    assert titles("unchecked=1") == ["Ferry", "Walrus"]
    assert titles("q=notes") == ["Notes"]
    page = _soup(client, "/wiki?q=notes&kind=pages")
    assert page.find("input", {"name": "q"})["value"] == "notes"  # the filters stay filled in
    assert "1 of 6 items" in page.get_text(" ", strip=True)
    assert "Nothing matches" in _soup(client, "/wiki?q=zzz").get_text()


def test_removing_and_restoring_still_work_from_the_library(tmp_path, monkeypatch):
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _login(client, ids["owner"])
    soup = _soup(client, "/wiki")
    link = soup.find(
        "a", href=lambda h: h and h.startswith("/wiki/remove?kind=original&name=ferry-")
    )
    confirm = client.get(link["href"])
    assert confirm.status_code == 200
    removed = client.post(
        "/wiki/remove",
        data={"kind": "original", "name": A, "scope": "personal"},
        follow_redirects=True,
    )
    assert removed.status_code == 200 and not (ws.raw / A).exists()
    after = _soup(client, "/wiki?removed=1")
    assert "ferry.pdf" not in [tr.a.get_text() for tr in after.select("table.lib-table tr")]
    assert after.find(id="wiki-removed") is not None  # listed under Removed, with Restore


def test_an_empty_wiki_says_so(tmp_path, monkeypatch):
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _login(client, ids["owner"])
    soup = _soup(client, "/wiki")
    assert soup.find(id="wiki-pages-card").find("form", class_="lib-filters") is None
    assert "Nothing here yet" in soup.find(id="wiki-pages-card").get_text()


def test_the_principles_tab_is_untouched(tmp_path, monkeypatch):
    _app, _eng, client, ids = _setup(tmp_path, monkeypatch)
    _login(client, ids["owner"])
    soup = _soup(client, "/wiki?tab=principles")
    assert soup.find(id="wiki-pages-card") is None
    assert soup.select_one(".settings-tabs a.active").get_text() == "Principles"


# ── speed: a page is read again only when its file changed ─────────────────────────


def test_a_page_is_read_again_only_when_its_file_changed(tmp_path, monkeypatch):
    ws = _world(tmp_path / "user-1")
    reads = []
    real = library._page_record
    monkeypatch.setattr(library, "_page_record", lambda text: reads.append(1) or real(text))
    library._PAGE_CACHE.clear()
    first = _rows(ws)
    assert len(reads) == 4 and "Notes" in first  # every page read once
    reads.clear()
    _rows(ws)
    assert reads == []  # nothing changed: nothing read again
    page = ws.wiki / "notes.md"
    page.write_text(page.read_text(encoding="utf-8").replace("# Notes", "# Notes, corrected"))
    again = _rows(ws)
    assert len(reads) == 1 and "Notes, corrected" in again and "Notes" not in again
