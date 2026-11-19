"""A person can correct a wiki page from its page view (#259). Model-free."""

import hashlib
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import anthill.wiki.ask as ask_mod
from anthill.lifecycle.renovate import renovate
from anthill.lifecycle.version import stale_pages, tag_page
from anthill.web.db import Team, TeamMembership
from anthill.wiki import items
from anthill.wiki.workspace import Workspace
from tests.test_answers_use_document_text import (  # noqa: F401
    HANDBOOK,
    MILL,
    RAW,
    SUMMARY,
    _Backend,
    embeds,
)
from tests.test_upload_status import _login, _setup

PAGE = "hollis-bakery-operations-handbook"
FIXED = SUMMARY.replace("Flurf", "the Brandt mill")


def _world(root: Path, scope="personal") -> Workspace:
    ws = Workspace(root, scope=scope)
    ws.init()
    ws.text.mkdir(exist_ok=True)
    (ws.raw / RAW).write_bytes(b"%PDF-1.4 x")
    (ws.text / f"{RAW}.txt").write_text(HANDBOOK, encoding="utf-8")
    items.ensure_source_record(ws, ws.raw / RAW)
    ws.write_page(PAGE, tag_page(SUMMARY, "qwen2.5:3b", on="2026-11-18", source=RAW))
    ws.write_page("notes", "# Notes\n\nA page of my own.\n")
    return ws


def _personal(ids, who="owner") -> Workspace:
    import os

    return _world(Path(os.environ["ANTHILL_WIKI_ROOT"]) / f"user-{ids[who][0]}")


def _edit(client, text, slug=PAGE, scope="personal", team_id=None, follow=False):
    data = {"content": text, "scope": scope}
    if team_id:
        data["team_id"] = team_id
    return client.post(f"/wiki/page/{slug}/edit", data=data, follow_redirects=follow)


def _soup(client, url):
    return BeautifulSoup(client.get(url).text, "html.parser")


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ── the correction ────────────────────────────────────────────────────────────


def test_the_form_shows_the_pages_own_text_and_saving_replaces_it(tmp_path, monkeypatch):
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _login(client, ids["owner"])
    box = _soup(client, f"/wiki/page/{PAGE}/edit").find("textarea")
    shown = box.get_text()
    assert "Flurf" in shown and "anthill:" not in shown and "x-anthill" not in shown  # text only
    res = _edit(client, shown.replace("Flurf", "the Brandt mill"))
    assert res.status_code == 303 and res.headers["location"].endswith("?scope=personal&saved=1")
    page = _soup(client, res.headers["location"])
    assert "Brandt mill" in page.get_text() and "Flurf" not in page.get_text()
    assert "Saved." in page.get_text()
    assert "Flurf" not in (ws.wiki / f"{PAGE}.md").read_text()
    assert "edit | " + PAGE in (ws.root / "log.md").read_text()
    assert f"{PAGE}.md" in (ws.root / "index.md").read_text()


def test_the_record_says_a_person_wrote_it_and_keeps_its_id_and_source(tmp_path, monkeypatch):
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    before = items.read_page_record((ws.wiki / f"{PAGE}.md").read_text())
    _login(client, ids["owner"])
    _edit(client, FIXED)
    text = (ws.wiki / f"{PAGE}.md").read_text()
    after = items.read_page_record(text)
    assert after.id == before.id and after.version == 2
    assert after.parent_hash == before.content_hash and after.content_hash != before.content_hash
    assert after.origin == "person-edited" and after.review_state == "checked"
    assert after.derived_from == before.derived_from
    assert items.is_person_edited(text)
    from anthill.lifecycle.version import read_provenance

    assert read_provenance(text)["source"] == RAW  # the page still knows where it came from


def test_the_page_line_says_it_was_edited_and_an_unedited_page_is_unchanged(tmp_path, monkeypatch):
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    _personal(ids)
    _login(client, ids["owner"])
    plain = " ".join(_soup(client, f"/wiki/page/{PAGE}").get_text().split())
    assert (
        "AI summary written by qwen2.5:3b on 2026-11-18" in plain
        and "edited by a person" not in plain
    )
    _edit(client, FIXED)
    edited = " ".join(_soup(client, f"/wiki/page/{PAGE}").get_text().split())
    assert "AI summary written by qwen2.5:3b on 2026-11-18" in edited
    assert ", then edited by a person" in edited


def test_a_page_with_no_record_can_be_edited_too(tmp_path, monkeypatch):
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    (ws.wiki / "legacy.md").write_text("# Legacy\n\nOld text.\n")
    _login(client, ids["owner"])
    _edit(client, "# Legacy\n\nNew text.\n", slug="legacy")
    rec = items.read_page_record((ws.wiki / "legacy.md").read_text())
    assert rec.origin == "person-edited" and rec.review_state == "checked"
    assert "New text" in (ws.wiki / "legacy.md").read_text()


def test_a_provenance_comment_typed_into_the_box_is_dropped(tmp_path, monkeypatch):
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _login(client, ids["owner"])
    _edit(client, FIXED + "\n<!-- anthill: model=made-up ingested=2020-01-01 -->\n")
    text = (ws.wiki / f"{PAGE}.md").read_text()
    assert text.count("<!-- anthill:") == 1 and "made-up" not in text and "qwen2.5:3b" in text


def test_the_correction_is_what_the_next_answer_is_given(tmp_path, monkeypatch, embeds):  # noqa: F811
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    # the document text is not used here, so the answer can only come from the page
    (ws.text / f"{RAW}.txt").unlink()
    from anthill.wiki import doc_index

    doc_index.sync_workspace(ws.root)
    backend = _Backend()
    list(ask_mod.ask_stream(ws, MILL, backend))
    assert "Flurf" in backend.prompts[0]
    _login(client, ids["owner"])
    _edit(client, FIXED)
    backend = _Backend()
    list(ask_mod.ask_stream(ws, MILL, backend))
    assert "Brandt mill" in backend.prompts[0] and "Flurf" not in backend.prompts[0]


# ── what it refuses ───────────────────────────────────────────────────────────


def test_an_empty_page_and_an_over_long_page_are_refused_with_the_text_given_back(
    tmp_path, monkeypatch
):
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    before = _hash(ws.wiki / f"{PAGE}.md")
    _login(client, ids["owner"])
    empty = _edit(client, "   \n ")
    assert empty.status_code == 400 and "cannot be empty" in empty.text
    from anthill.web.app import MAX_PAGE_EDIT_CHARS

    big = "x " * MAX_PAGE_EDIT_CHARS
    long = _edit(client, big)
    assert long.status_code == 400 and "too long" in long.text
    assert BeautifulSoup(long.text, "html.parser").find("textarea").get_text().startswith("x x")
    assert _hash(ws.wiki / f"{PAGE}.md") == before


def test_saving_text_that_did_not_change_writes_nothing(tmp_path, monkeypatch):
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    before = _hash(ws.wiki / f"{PAGE}.md")
    _login(client, ids["owner"])
    shown = _soup(client, f"/wiki/page/{PAGE}/edit").find("textarea").get_text()
    res = _edit(client, shown)
    assert res.status_code == 303 and "saved=1" not in res.headers["location"]
    assert _hash(ws.wiki / f"{PAGE}.md") == before
    assert not items.is_person_edited((ws.wiki / f"{PAGE}.md").read_text())


@pytest.mark.parametrize("slug", ["no-such-page", "..%2Findex", "..", "a%2F..%2Fb"])
def test_an_edit_never_creates_a_page_or_leaves_the_wiki(tmp_path, monkeypatch, slug):
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    _login(client, ids["owner"])
    before = sorted(p.name for p in ws.wiki.iterdir())
    assert client.get(f"/wiki/page/{slug}/edit").status_code in (404, 405)
    assert _edit(client, "# New\n\nText.\n", slug=slug).status_code in (404, 405)
    assert sorted(p.name for p in ws.wiki.iterdir()) == before


# ── who may edit ──────────────────────────────────────────────────────────────


def _team(app_mod, ids):
    s = app_mod._SessionFactory()
    team = Team(org_id=ids["owner"][1], name="Bakers", slug="bakers", owner_id=ids["owner"][0])
    s.add(team)
    s.flush()
    for who, role in (("owner", "owner"), ("mate", "member")):
        s.add(TeamMembership(team_id=team.id, user_id=ids[who][0], role=role, status="active"))
    s.commit()
    tid = team.id
    s.close()
    return tid


def test_only_the_people_who_may_edit_a_wiki_may_edit_its_pages(tmp_path, monkeypatch):
    import os

    app_mod, _e, client, ids = _setup(tmp_path, monkeypatch)
    tid = _team(app_mod, ids)
    team_ws = _world(Path(os.environ["ANTHILL_WIKI_ROOT"]) / f"team-{tid}", scope="team")
    org_ws = _world(Path(os.environ["ANTHILL_ORG_WIKI"]), scope="org")
    cases = [
        ("owner", {"scope": "team", "team_id": tid}, 303, team_ws),  # the team's owner
        ("mate", {"scope": "team", "team_id": tid}, 403, team_ws),  # a member who is not the owner
        ("admin", {"scope": "team", "team_id": tid}, 403, team_ws),  # not in the team
        ("owner", {"scope": "org"}, 403, org_ws),  # an org member
        ("admin", {"scope": "org"}, 303, org_ws),  # the org admin
    ]
    for who, where, expected, ws in cases:
        _login(client, ids[who])
        res = _edit(client, FIXED, **where)
        assert res.status_code == expected, (who, where, res.status_code)
        query = "&".join(f"{k}={v}" for k, v in where.items())
        form = client.get(f"/wiki/page/{PAGE}/edit?{query}")
        assert form.status_code == (200 if expected == 303 else 403), (who, where)
        view = (
            _soup(client, f"/wiki/page/{PAGE}?{query}")
            if who != "admin" or where["scope"] == "org"
            else None
        )
        if view is not None:
            link = view.find("a", string="Edit this page")
            assert (link is not None) == (expected == 303), (who, where)
        if expected != 303:
            assert "Flurf" in (ws.wiki / f"{PAGE}.md").read_text()
        else:
            ws.write_page(
                PAGE, tag_page(SUMMARY, "qwen2.5:3b", on="2026-11-18", source=RAW)
            )  # reset


def test_a_solo_account_has_no_organisation_wiki_to_edit(tmp_path, monkeypatch):
    from anthill.web.db import OrgSettings

    app_mod, _e, client, ids = _setup(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    s.query(OrgSettings).first().deployment_topology = "solo"
    s.commit()
    s.close()
    _login(client, ids["admin"])
    assert _edit(client, FIXED, scope="org").status_code == 404


# ── a model upgrade never writes over a person's text ─────────────────────────


def test_a_model_upgrade_leaves_an_edited_page_alone(tmp_path, monkeypatch):
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws = _personal(ids)
    assert PAGE in [p.stem for p, _ in stale_pages(ws.wiki, "qwen3:8b")]
    _login(client, ids["owner"])
    _edit(client, FIXED)
    assert PAGE not in [p.stem for p, _ in stale_pages(ws.wiki, "qwen3:8b")]
    report = renovate(ws, _Backend(), "qwen3:8b", only_stale=False, dry_run=True)
    assert PAGE in report.skipped and PAGE not in report.regenerated
    assert "edited by a person" in report.summary()


def test_nothing_here_calls_a_model(tmp_path, monkeypatch):
    from anthill.inference.ollama import OllamaBackend

    def never(*a, **k):
        raise AssertionError("editing called a model")

    for method in ("chat", "chat_stream", "chat_with_tools", "chat_with_confidence"):
        monkeypatch.setattr(OllamaBackend, method, never)
    _a, _e, client, ids = _setup(tmp_path, monkeypatch)
    _personal(ids)
    _login(client, ids["owner"])
    assert _edit(client, FIXED).status_code == 303
