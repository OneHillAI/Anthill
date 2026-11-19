"""Layout and wording of the answer panel, the Removed list, the remove confirmation and the edit page (#303).
Render tests: what the page's markup says. How it looks is checked on a running copy."""

import os
from pathlib import Path

from bs4 import BeautifulSoup

from anthill.web.db import Team
from anthill.wiki.workspace import Workspace
from tests.test_answers_use_document_text import MILL, chat, embeds  # noqa: F401
from tests.test_remove_an_item import A, _personal, _soup
from tests.test_upload_status import _login, _setup

# ── the answer panel is its own block ─────────────────────────────────────────


def test_the_panel_is_a_block_after_the_button_row_not_a_part_of_it(chat):  # noqa: F811
    conv = chat.ask(MILL)
    soup = BeautifulSoup(chat.client.get(f"/chat/{conv}").text, "html.parser")
    panel = soup.select_one("div.msg.assistant details.answer-record")
    assert panel is not None
    assert panel.find_parent(class_="meta") is None  # not squeezed into the row that does not wrap
    meta = soup.select_one("div.msg.assistant .meta")
    assert panel.find_previous_sibling(class_="meta") is meta and panel.parent is meta.parent
    assert "flex-basis" not in panel.get("style", "")


def test_an_older_answer_keeps_its_plain_lines_inside_the_row(chat):  # noqa: F811
    import anthill.web.app as app_mod
    from anthill.web.db import ChatMessage

    conv = chat.ask(MILL)
    sess = app_mod._SessionFactory()
    try:
        msg = (
            sess.query(ChatMessage)
            .filter(ChatMessage.conversation_id == conv, ChatMessage.role == "assistant")
            .one()
        )
        msg.manifest = ""
        sess.commit()
    finally:
        sess.close()
    soup = BeautifulSoup(chat.client.get(f"/chat/{conv}").text, "html.parser")
    assert soup.select_one("div.msg.assistant details.answer-record") is None
    assert "Pages in context" in soup.select_one("div.msg.assistant .meta").get_text()


def test_the_live_answer_puts_the_panel_in_its_own_slot(chat):  # noqa: F811
    conv = chat.ask(MILL)
    page = chat.client.get(f"/chat/{conv}").text
    assert "record-slot" in page and "recordHtml = d.meta.record_html" in page
    assert "metaHtml += d.meta.record_html" not in page  # no longer part of the row


# ── the Removed list and the Files list ───────────────────────────────────────


def test_delete_now_is_a_danger_button_with_the_apps_own_confirm(tmp_path, monkeypatch):
    from anthill.wiki import removal

    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    removal.remove_page(ws, "ferry")
    _login(client, ids["owner"])
    card = _soup(client, "/wiki?tab=files").find(id="wiki-removed")
    forms = {f["action"].rsplit("/", 1)[1]: f for f in card.find_all("form")}
    delete, restore = forms["delete"], forms["restore"]
    assert delete.find("button")["class"] == ["btn", "btn-sm", "btn-danger"]
    assert "Delete this for good" in delete["data-confirm"] and "onsubmit" not in delete.attrs
    assert (
        "btn-danger" not in restore.find("button")["class"] and "data-confirm" not in restore.attrs
    )


def test_the_message_is_above_the_files_list_with_a_gap(tmp_path, monkeypatch):
    from anthill.wiki import removal

    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    removal.remove_original(ws, A)
    _login(client, ids["owner"])
    for flag, text in (
        ("removed=1", "Removed."),
        ("restored=1", "Restored."),
        ("deleted=1", "Deleted for good."),
    ):
        soup = _soup(client, f"/wiki?tab=files&{flag}")
        alert = next(a for a in soup.find_all(class_="alert") if text in a.get_text())
        files = soup.find("h2", string="Files")
        assert alert is not None and files is not None
        assert alert.sourceline < files.sourceline, flag  # before the Files list, so it is in view
        assert "margin-bottom" in alert["style"]


def test_names_are_the_ones_the_person_uploaded_and_links_use_the_stored_name(
    tmp_path, monkeypatch
):
    from anthill.wiki import removal

    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _login(client, ids["owner"])
    files = _soup(client, "/wiki?tab=files")
    link = files.find("a", string="walrus.pdf")
    assert link is not None and "walrus-bbbbbbbbbbbb.pdf" in link["href"]
    assert "walrus-bbbbbbbbbbbb.pdf" not in files.get_text()
    page = _soup(client, f"/wiki/remove?kind=original&name={A}&scope=personal").find(
        id="remove-confirm"
    )
    assert "ferry.pdf" in page.get_text() and A not in page.get_text()
    removal.remove_original(ws, A)
    removed = _soup(client, "/wiki?tab=files").find(id="wiki-removed").get_text()
    assert "ferry.pdf" in removed and A not in removed


def test_the_confirmation_shows_titles_and_says_it_plainly(tmp_path, monkeypatch):
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    _personal(ids)
    _login(client, ids["owner"])
    text = " ".join(
        _soup(client, f"/wiki/remove?kind=original&name={A}&scope=personal")
        .find(id="remove-confirm")
        .get_text()
        .split()
    )
    assert "The page made only from it, that nobody has checked, goes too: Ferry" in text
    assert "The page made from it stays. You can remove it separately: Ferry checked" in text
    assert "may have other sources" not in text
    page = _soup(client, "/wiki/remove?kind=page&name=ferry&scope=personal").find(
        id="remove-confirm"
    )
    assert (
        "Remove Ferry?" in " ".join(page.get_text().split())
        and "Remove ferry?" not in page.get_text()
    )


# ── the edit page and the page view ───────────────────────────────────────────


def test_the_edit_page_has_a_primary_save_a_visible_notice_and_says_what_the_title_is(
    tmp_path, monkeypatch
):
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    _personal(ids)
    _login(client, ids["owner"])
    soup = _soup(client, "/wiki/page/ferry/edit")
    assert "btn-primary" in soup.find("button", string="Save")["class"]
    notice = soup.find(id="wiki-edit-notice")
    assert (
        notice is not None
        and "earlier text is not kept" in notice.get_text()
        and "alert" in notice["class"]
    )
    assert (
        "first heading" in soup.find("label").get_text()
        and "title" in soup.find("label").get_text()
    )


def test_the_page_view_has_one_row_of_actions_for_whoever_may_edit(tmp_path, monkeypatch):
    app_mod, _e, client, ids = _setup(tmp_path, monkeypatch)
    _personal(ids)
    _login(client, ids["owner"])
    row = _soup(client, "/wiki/page/ferry").find(id="page-actions")
    assert [a.get_text() for a in row.find_all("a")] == ["Edit this page", "Remove this page"]
    # an organisation member can read the organisation's pages and has no actions
    s = app_mod._SessionFactory()
    s.add(Team(org_id=ids["owner"][1], name="T", slug="t", owner_id=ids["admin"][0]))
    s.commit()
    s.close()
    org_ws = Workspace(Path(os.environ["ANTHILL_ORG_WIKI"]), scope="org")
    org_ws.init()
    org_ws.write_page("ferry", "# Ferry\n\nThe organisation's page.\n")
    view = _soup(client, "/wiki/page/ferry?scope=org")
    assert view.find(id="page-actions") is None and view.find("a", string="Edit this page") is None
