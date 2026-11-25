"""A person can see a page's earlier versions and restore one, and a refused save says where to look (build plan step 3.3).
Model-free."""

import os
import re
from pathlib import Path

from bs4 import BeautifulSoup

from anthill.lifecycle.version import tag_page
from anthill.web.db import Team, TeamMembership
from anthill.wiki import items, page_store
from anthill.wiki.workspace import Workspace
from tests.test_upload_status import _login, _setup

V1 = "# Ferry\n\nThe ferry leaves at nine.\n"
V2 = "# Ferry\n\nThe ferry leaves at ten.\n"
V3 = "# Ferry\n\nThe ferry leaves at eleven.\n"


def _personal(ids, who="owner") -> Workspace:
    ws = Workspace(Path(os.environ["ANTHILL_WIKI_ROOT"]) / f"user-{ids[who][0]}", scope="personal")
    ws.init()
    ws.write_page("ferry", tag_page(V1, "qwen2.5:3b", on="2026-11-01"))
    return ws


def _soup(client, url):
    return BeautifulSoup(client.get(url).text, "html.parser")


def _links(soup) -> list[str]:
    return [a.get_text(" ", strip=True) for a in soup.find_all("a")]


def _hash_in(soup) -> str:
    return soup.find("input", {"name": "base_hash"})["value"]


def _edit_as_person(ws: Workspace, text: str) -> None:
    from anthill.common.text import strip_frontmatter

    current = strip_frontmatter((ws.wiki / "ferry.md").read_text())
    comment = current[current.index("<!-- anthill:") :]
    ws.write_page("ferry", f"{text.strip()}\n\n{comment}", by_person=True)


# ── the history and its link ──────────────────────────────────────────────────


def test_a_page_with_no_earlier_version_has_no_history_link(tmp_path, monkeypatch):
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    _personal(ids)
    _login(client, ids["owner"])
    assert "History" not in _links(_soup(client, "/wiki/page/ferry"))


def test_after_an_edit_the_page_links_to_its_history_which_lists_each_version(
    tmp_path, monkeypatch
):
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _edit_as_person(ws, V2)
    _edit_as_person(ws, V3)
    _login(client, ids["owner"])
    assert "History" in _links(_soup(client, "/wiki/page/ferry"))
    page = _soup(client, "/wiki/page/ferry/history")
    rows = [li.get_text(" ", strip=True) for li in page.find(id="history-list").find_all("li")]
    assert rows[0].startswith("Version 3 (now)") and "by a person" in rows[0]  # newest first
    assert rows[1].startswith("Version 2") and "by a person" in rows[1]
    assert rows[2].startswith("Version 1") and "as an AI summary" in rows[2]
    assert all(re.search(r"\d{4}-\d\d-\d\d \d\d:\d\d", r) for r in rows)  # a day and a time each
    assert "1 line added, 1 line removed" in rows[0]  # what each version changed
    assert "first kept version" in rows[2]


def test_an_earlier_version_opens_with_its_text_and_a_restore_button(tmp_path, monkeypatch):
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _edit_as_person(ws, V2)
    _login(client, ids["owner"])
    page = _soup(client, "/wiki/page/ferry/history/1")
    shown = page.find(id="history-version")
    text = shown.find("pre").get_text()
    assert "leaves at nine" in text and "leaves at ten" not in text
    assert "anthill:" not in shown.get_text() and "x-anthill" not in shown.get_text()
    assert shown.find("button", string="Restore this version") is not None
    assert client.get("/wiki/page/ferry/history/9").status_code == 404
    # the row being looked at is not a link, and the page now is
    rows = page.find(id="history-list").find_all("li")
    assert rows[1].find("a") is None and "shown above" in rows[1].get_text()
    assert rows[0].find("a") is not None


def test_a_version_page_shows_how_the_version_differs_from_the_page_now(tmp_path, monkeypatch):
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _edit_as_person(ws, V2)
    _login(client, ids["owner"])
    page = _soup(client, "/wiki/page/ferry/history/1")
    table = page.find(id="history-compare").find_next("table")
    assert [m.get_text() for m in table.find_all("mark")] == [
        "ten.",
        "nine.",
    ]  # now, then the version
    assert "The page now (version 2)" in table.get_text() and "Version 1" in table.get_text()
    _edit_as_person(ws, V1)  # the page now says what version 1 says
    same = _soup(client, "/wiki/page/ferry/history/1")
    assert "same as the page now" in same.find(id="history-version").get_text()


# ── restore ───────────────────────────────────────────────────────────────────


def test_restoring_makes_the_old_text_the_page_again_and_keeps_what_it_replaced(
    tmp_path, monkeypatch
):
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _edit_as_person(ws, V2)
    before = items.read_page_record((ws.wiki / "ferry.md").read_text())
    _login(client, ids["owner"])
    base = _hash_in(_soup(client, "/wiki/page/ferry/history/1"))
    res = client.post(
        "/wiki/page/ferry/history/1/restore",
        data={"scope": "personal", "base_hash": base},
        follow_redirects=False,
    )
    assert res.status_code == 303 and res.headers["location"].endswith("saved=restored&version=1")
    done = _soup(client, res.headers["location"])
    assert "Restored version 1. The page as it was before is kept in History." in " ".join(
        done.find(id="page-restored").get_text().split()
    )
    assert "Saved. The page now shows your text." not in done.get_text()
    listed = [
        li.get_text(" ", strip=True)
        for li in _soup(client, "/wiki/page/ferry/history").find(id="history-list").find_all("li")
    ]
    assert "restored from version 1" in listed[0]  # not told apart from a hand edit
    assert "by a person" in listed[1]
    text = (ws.wiki / "ferry.md").read_text()
    assert "leaves at nine" in text and "leaves at ten" not in text
    assert "anthill: model=qwen2.5:3b" in text  # the page keeps its provenance line
    rec = items.read_page_record(text)
    assert rec.id == before.id and rec.version == before.version + 1
    assert rec.origin == "person-edited" and rec.review_state == "checked"
    kept = [p.read_text() for _n, p in page_store.versions(ws, "ferry")]
    assert any("leaves at ten" in k for k in kept)  # nothing was erased
    assert "restore-version | ferry" in (ws.root / "log.md").read_text()


def test_a_restore_made_after_the_page_changed_is_refused(tmp_path, monkeypatch):
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _edit_as_person(ws, V2)
    _login(client, ids["owner"])
    base = _hash_in(_soup(client, "/wiki/page/ferry/history/1"))
    _edit_as_person(ws, V3)  # someone saves while the history page is open
    res = client.post(
        "/wiki/page/ferry/history/1/restore",
        data={"scope": "personal", "base_hash": base},
        follow_redirects=False,
    )
    assert res.status_code == 409
    assert "the earlier version was not restored" in " ".join(res.text.split())
    assert "leaves at eleven" in (ws.wiki / "ferry.md").read_text()


def test_restoring_the_text_the_page_already_has_changes_nothing(tmp_path, monkeypatch):
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _edit_as_person(ws, V2)
    _edit_as_person(ws, V1)  # back to the first words by hand
    before = (ws.wiki / "ferry.md").read_text()
    _login(client, ids["owner"])
    base = _hash_in(_soup(client, "/wiki/page/ferry/history/1"))
    res = client.post(
        "/wiki/page/ferry/history/1/restore",
        data={"scope": "personal", "base_hash": base},
        follow_redirects=False,
    )
    assert res.status_code == 303 and (ws.wiki / "ferry.md").read_text() == before


# ── who may ───────────────────────────────────────────────────────────────────


def _team_world(app_mod, ids, tmp_path):
    s = app_mod._SessionFactory()
    team = Team(org_id=ids["owner"][1], name="Ferry crew", slug="crew", owner_id=ids["owner"][0])
    s.add(team)
    s.flush()
    s.add(TeamMembership(team_id=team.id, user_id=ids["owner"][0], role="owner", status="active"))
    s.add(TeamMembership(team_id=team.id, user_id=ids["mate"][0], role="member", status="active"))
    s.commit()
    tid = team.id
    s.close()
    ws = Workspace(Path(os.environ["ANTHILL_WIKI_ROOT"]) / f"team-{tid}", scope="team")
    ws.init()
    ws.write_page("ferry", tag_page(V1, "qwen2.5:3b", on="2026-11-01"))
    _edit_as_person(ws, V2)
    return ws, tid


def test_a_team_member_who_cannot_edit_sees_the_history_but_cannot_restore(tmp_path, monkeypatch):
    app_mod, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws, tid = _team_world(app_mod, ids, tmp_path)
    _login(client, ids["mate"])
    page = _soup(client, f"/wiki/page/ferry/history/1?scope=team&team_id={tid}")
    assert "leaves at nine" in page.find(id="history-version").get_text()
    assert page.find("button", string="Restore this version") is None
    res = client.post(
        "/wiki/page/ferry/history/1/restore",
        data={
            "scope": "team",
            "team_id": tid,
            "base_hash": _hash_in(page) if page.find("input", {"name": "base_hash"}) else "",
        },
        follow_redirects=False,
    )
    assert res.status_code == 403 and "leaves at ten" in (ws.wiki / "ferry.md").read_text()


def test_the_history_of_a_page_in_a_wiki_you_cannot_read_is_not_there(tmp_path, monkeypatch):
    app_mod, _e, client, ids = _setup(tmp_path, monkeypatch)
    _ws, tid = _team_world(app_mod, ids, tmp_path)
    _login(client, ids["admin"])  # in the organisation, not in the team
    assert client.get(f"/wiki/page/ferry/history?scope=team&team_id={tid}").status_code == 403
    assert (
        client.get("/wiki/page/ferry/history").status_code == 404
    )  # and not in the admin's own wiki


# ── the refused save in the edit screen ───────────────────────────────────────


def test_a_refused_save_links_to_the_page_as_it_is_now_and_its_history(tmp_path, monkeypatch):
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _login(client, ids["owner"])
    form = _soup(client, "/wiki/page/ferry/edit")
    opened = _hash_in(form)
    _edit_as_person(ws, V2)  # another tab saves first
    res = client.post(
        "/wiki/page/ferry/edit",
        data={"content": V3, "scope": "personal", "base_hash": opened},
        follow_redirects=False,
    )
    assert res.status_code == 409
    page = BeautifulSoup(res.text, "html.parser")
    box = page.find(id="wiki-edit-conflict")
    hrefs = [a["href"] for a in box.find_all("a")]
    assert hrefs == ["/wiki/page/ferry?scope=personal", "/wiki/page/ferry/history?scope=personal"]
    assert "leaves at eleven" in page.find("textarea").get_text()  # their text is still there
    assert page.find(id="wiki-edit-notice") is None  # one alert, not two
    assert (
        page.find("button", string="Save anyway") is not None
        and page.find("button", string="Save") is None
    )
    assert "someone else" not in page.get_text()
    assert _soup(client, "/wiki/page/ferry").find(id="wiki-edit-conflict") is None
    assert _soup(client, "/wiki/page/ferry/edit").find("button", string="Save") is not None


def test_the_edit_screen_of_an_unrefused_save_has_no_conflict_note(tmp_path, monkeypatch):
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    _personal(ids)
    _login(client, ids["owner"])
    assert _soup(client, "/wiki/page/ferry/edit").find(id="wiki-edit-conflict") is None
