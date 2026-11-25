"""Remember this: one line added to the core page with a preview and a confirm, and the agent's remember as a proposal a person
reviews (build plan step 2.3, slice b; docs/specs/remember-this.md). Model-free."""

import hashlib

from anthill.agent.tools import make_tools
from anthill.web.agent_writes import core_line_proposer
from anthill.web.db import WikiReview
from anthill.wiki import core_page, items
from anthill.wiki.workspace import Workspace
from tests.test_answers_use_document_text import chat, embeds  # noqa: F401
from tests.test_upload_status import _login, _setup


def _ws(tmp_path, ids, who="owner"):
    ws = Workspace(tmp_path / "wikis" / f"user-{ids[who][0]}", scope="personal")
    ws.init()
    return ws


def _bytes(ws):
    p = core_page.path_of(ws)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else ""


def _signed_in(tmp_path, monkeypatch, who="owner"):
    app_mod, _e, client, ids = _setup(tmp_path, monkeypatch)
    _login(client, ids[who])
    return app_mod, client, ids


# ---- the line itself ----------------------------------------------------------------------------------------------------


def test_a_line_is_one_line_without_the_persons_bullet_and_cut_at_the_limit():
    assert core_page.one_line("  - Answer in\n plain   words.  ") == "Answer in plain words."
    assert core_page.one_line("* " + "w" * 500) == "w" * core_page.MAX_LINE
    assert core_page.one_line("   ") == ""


# ---- preview and confirm ------------------------------------------------------------------------------------------------


def test_a_preview_writes_nothing_and_a_confirm_adds_one_line_and_a_new_version(
    tmp_path, monkeypatch
):
    _app, client, ids = _signed_in(tmp_path, monkeypatch)
    ws = _ws(tmp_path, ids)
    core_page.save(ws, profile="I run a bakery.")
    before, v1 = _bytes(ws), items.read_page_record(core_page.path_of(ws).read_text()).version
    shown = client.post(
        "/core-page/remember/preview", data={"text": "- Answer in plain words."}
    ).json()
    assert (
        shown["line"] == "Answer in plain words."
        and shown["already"] is False
        and shown["over"] == 0
    )
    assert (
        "- Answer in plain words." in shown["page_after"]
        and shown["left"] > 0
        and shown["base_hash"]
    )
    assert _bytes(ws) == before  # nothing was written
    done = client.post(
        "/core-page/remember",
        data={"text": "Answer in plain words.", "base_hash": shown["base_hash"]},
    ).json()
    assert done["saved"] is True
    assert core_page.read(ws) == ("I run a bakery.", "- Answer in plain words.")
    assert items.read_page_record(core_page.path_of(ws).read_text()).version == v1 + 1


def test_a_line_that_is_already_there_is_not_added_again(tmp_path, monkeypatch):
    _app_mod, client, ids = _signed_in(tmp_path, monkeypatch)
    ws = _ws(tmp_path, ids)
    core_page.save(ws, preferences="- Answer in plain words.")
    before = _bytes(ws)
    shown = client.post(
        "/core-page/remember/preview", data={"text": "answer in PLAIN words."}
    ).json()
    assert shown["already"] is True
    done = client.post("/core-page/remember", data={"text": "answer in PLAIN words."}).json()
    assert done["saved"] is False and _bytes(ws) == before


def test_a_line_that_would_take_the_page_over_the_cap_is_refused_and_the_page_is_unchanged(
    tmp_path, monkeypatch
):
    _app_mod, client, ids = _signed_in(tmp_path, monkeypatch)
    ws = _ws(tmp_path, ids)
    core_page.save(ws, preferences="\n".join(f"- line {i:03d} " + "x" * 80 for i in range(33)))
    before = _bytes(ws)
    shown = client.post("/core-page/remember/preview", data={"text": "y" * 200}).json()
    assert shown["over"] > 0
    r = client.post("/core-page/remember", data={"text": "y" * 200})
    assert r.status_code == 400 and "cut about" in r.json()["error"] and _bytes(ws) == before


def test_a_page_changed_after_the_preview_is_not_overwritten(tmp_path, monkeypatch):
    _app_mod, client, ids = _signed_in(tmp_path, monkeypatch)
    ws = _ws(tmp_path, ids)
    core_page.save(ws, profile="I run a bakery.")
    shown = client.post("/core-page/remember/preview", data={"text": "Short answers."}).json()
    core_page.save(ws, profile="I run a bakery and teach piano.")  # edited after the preview
    r = client.post(
        "/core-page/remember", data={"text": "Short answers.", "base_hash": shown["base_hash"]}
    )
    assert r.status_code == 409 and "changed" in r.json()["error"]
    assert core_page.read(ws) == ("I run a bakery and teach piano.", "")


def test_a_sensitive_item_is_named_in_the_preview_and_the_person_decides(tmp_path, monkeypatch):
    _app_mod, client, ids = _signed_in(tmp_path, monkeypatch)
    ws = _ws(tmp_path, ids)
    shown = client.post(
        "/core-page/remember/preview", data={"text": "Reach me at sam@example.com"}
    ).json()
    assert shown["sensitive"] == ["an email address"] and not core_page.exists(ws)
    done = client.post("/core-page/remember", data={"text": "Reach me at sam@example.com"}).json()
    assert done["saved"] is True and "sam@example.com" in core_page.read(ws)[1]
    plain = client.post("/core-page/remember/preview", data={"text": "Prefer metric units"}).json()
    assert plain["sensitive"] == []


def test_an_empty_line_is_refused(tmp_path, monkeypatch):
    _app_mod, client, _ids = _signed_in(tmp_path, monkeypatch)
    for route in ("/core-page/remember/preview", "/core-page/remember"):
        r = client.post(route, data={"text": "  - "})
        assert r.status_code == 400 and "nothing to remember" in r.json()["error"]


def test_the_routes_act_only_on_the_signed_in_persons_own_page(tmp_path, monkeypatch):
    _app, client, ids = _signed_in(tmp_path, monkeypatch, "mate")  # the second person, not user 1
    mine, theirs = _ws(tmp_path, ids, "mate"), _ws(tmp_path, ids, "owner")
    core_page.save(theirs, preferences="- Their private line.")
    before = _bytes(theirs)
    client.post("/core-page/remember", data={"text": "My line."})
    assert core_page.read(mine)[1] == "- My line." and _bytes(theirs) == before
    assert (
        "Their private line"
        not in client.post("/core-page/remember/preview", data={"text": "x"}).text
    )


def test_the_next_owner_turn_is_given_the_line(chat):  # noqa: F811
    r = chat.client.post("/core-page/remember", data={"text": "Always answer in one sentence."})
    assert r.json()["saved"] is True
    chat.ask("Hello there, how are you today?")
    assert "Always answer in one sentence." in chat.state["prompts"][-1]


# ---- the agent's remember ----------------------------------------------------------------------------------------------


def _tool(tools, name):
    return next((t for t in tools if t.name == name), None)


def test_remember_is_offered_only_where_a_core_page_can_be_proposed():
    assert (
        _tool(make_tools(), "remember") is None
    )  # a task, a persistent agent, an organisation turn
    seen: list = []
    tools = make_tools(propose_core_line=lambda fact: seen.append(fact) or "queued")
    assert _tool(tools, "remember").fn("Prefers metric units") == "queued" and seen == [
        "Prefers metric units"
    ]
    assert "A person reviews it" in _tool(tools, "remember").description


def test_the_agent_proposes_a_line_and_nothing_reaches_the_page_until_it_is_approved(
    tmp_path, monkeypatch
):
    app_mod, client, ids = _signed_in(tmp_path, monkeypatch)
    ws = _ws(tmp_path, ids)
    core_page.save(ws, profile="I run a bakery.")
    before = _bytes(ws)
    uid, org_id, _role = ids["owner"]
    s = app_mod._SessionFactory()
    propose = core_line_proposer(s, org_id=org_id, user_id=uid, who="the chat agent")
    out = propose("Prefers metric units")
    s.close()
    assert "not on the page until a person approves it" in out and _bytes(ws) == before
    s = app_mod._SessionFactory()
    (review,) = s.query(WikiReview).all()
    assert (
        review.slug == core_page.SLUG
        and review.target_scope == "personal"
        and review.status == "pending"
    )
    rid = review.id
    s.close()
    assert client.post(f"/wiki/review/{rid}/approve", follow_redirects=False).status_code in (
        200,
        302,
        303,
    )
    assert core_page.read(ws) == ("I run a bakery.", "- Prefers metric units")


def test_the_agent_is_told_when_a_line_is_already_there_or_too_long_and_nothing_is_queued(
    tmp_path, monkeypatch
):
    app_mod, _client, ids = _signed_in(tmp_path, monkeypatch)
    ws = _ws(tmp_path, ids)
    core_page.save(ws, preferences="- Prefers metric units")
    uid, org_id, _role = ids["owner"]
    s = app_mod._SessionFactory()
    propose = core_line_proposer(s, org_id=org_id, user_id=uid, who="the chat agent")
    assert "already on the core page" in propose("prefers METRIC units")
    core_page.save(ws, preferences="\n".join(f"- line {i:03d} " + "x" * 80 for i in range(33)))
    assert "cut about" in propose("z" * 250)
    assert s.query(WikiReview).count() == 0
    s.close()
    assert (
        core_line_proposer(s, org_id=org_id, user_id=None, who="x") is None
    )  # nobody to review it
