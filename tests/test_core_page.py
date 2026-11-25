"""The core page: one small private page of a person's profile and preferences, read into the guidance of their own turns, never
retrieved as knowledge (build plan step 2.3, slice a; docs/specs/core-page.md). Model-free."""

import json

import pytest

import anthill.web.app as app_mod
from anthill.agent.executor import AgentExecutor
from anthill.agent.tools import make_tools
from anthill.web.db import ChatMessage, User
from anthill.wiki import answer_record, core_page, prompt_blocks
from anthill.wiki.workspace import Workspace, workspace_for
from tests.test_answers_use_document_text import MILL, chat, embeds  # noqa: F401

GUIDANCE = "About the user you are helping (their stated profile and preferences - honor these):\n"


def _owner(_chat=None):
    sess = app_mod._SessionFactory()
    try:
        return sess.query(User).one().id
    finally:
        sess.close()


def _set_legacy_profile(text):
    sess = app_mod._SessionFactory()
    sess.query(User).update({User.profile: text})
    sess.commit()
    sess.close()


def _manifest(rig, conv):
    stored = rig.row(conv).manifest
    return (
        json.loads(stored) if stored else {"items": []}
    )  # nothing was given, so no record was kept


# ---- the page and its parts ---------------------------------------------------------------------------------------------


def test_the_two_parts_survive_a_round_trip_and_a_page_with_only_preferences_reads_as_such():
    body = core_page.compose("I run a bakery.", "Answer in plain words.")
    assert core_page.split(body) == ("I run a bakery.", "Answer in plain words.")
    assert core_page.split("# Core page\n\n## Preferences\n\nShort answers.\n") == (
        "",
        "Short answers.",
    )
    assert core_page.split("Just some text") == ("Just some text", "")


def test_a_save_over_the_cap_is_refused_and_the_page_is_unchanged(tmp_path):
    ws = Workspace(tmp_path / "w", scope="personal")
    ws.init()
    core_page.save(ws, profile="Short.")
    before = core_page.path_of(ws).read_text()
    with pytest.raises(core_page.TooLong) as caught:
        core_page.save(ws, preferences="x" * (core_page.CAP_CHARS + 50))
    assert caught.value.over > 0 and "cut about" in str(caught.value)
    with pytest.raises(core_page.TooLong):
        ws.write_page(
            core_page.SLUG, "y" * (core_page.CAP_CHARS + 1), by_person=True
        )  # any route to the page
    assert core_page.path_of(ws).read_text() == before


def test_nothing_is_made_for_a_person_with_no_profile_and_no_page(tmp_path):
    ws = Workspace(tmp_path / "w", scope="personal")
    ws.init()
    assert core_page.guidance_for(ws, "") == ("", None) and core_page.guidance_for(ws, "   ") == (
        "",
        None,
    )
    assert not core_page.exists(ws)


def test_a_profile_too_long_for_the_page_is_given_as_it_was_and_nothing_is_lost(tmp_path):
    ws = Workspace(tmp_path / "w", scope="personal")
    ws.init()
    long = "z" * (core_page.CAP_CHARS + 10)
    assert core_page.guidance_for(ws, long) == (long, None) and not core_page.exists(ws)


def test_a_core_page_that_cannot_name_its_item_gives_nothing(tmp_path, monkeypatch, caplog):
    ws = Workspace(tmp_path / "w", scope="personal")
    ws.init()
    core_page.save(ws, profile="I am a baker.")
    monkeypatch.setattr(answer_record, "_page_item", lambda *a, **k: {"kind": "page"})
    with caplog.at_level("WARNING"):
        assert core_page.guidance_for(ws, "") == ("", None)
    assert "no item id" in caplog.text


# ---- never retrieved as knowledge ---------------------------------------------------------------------------------------


def test_the_core_page_is_not_a_page_an_answer_may_read_or_an_agent_may_find(tmp_path, embeds):  # noqa: F811
    ws = Workspace(tmp_path / "w", scope="personal")
    ws.init()
    ws.write_page("gardening", "# Gardening\n\nTomatoes need sun and the Zorp trowel.\n")
    core_page.save(
        ws, profile="I keep a Zorp quasar diary.", preferences="Zorp quasar answers please."
    )
    assert core_page.exists(ws) and core_page.path_of(ws) in ws.pages()  # it is a page of the wiki
    assert [p.stem for p in ws.retrievable_pages()] == ["gardening"]
    tools = make_tools(workspace=str(ws.root))
    read = next(t for t in tools if t.name == "read_wiki").call({"slug": core_page.SLUG})
    assert "not found" in read and "quasar" not in read
    found = next(t for t in tools if t.name == "search_wiki").call(
        {"query": "Zorp quasar diary", "k": 5}
    )
    assert "core-page" not in found and "quasar" not in found


# ---- the profile moves onto it, and the answer is the same --------------------------------------------------------------


def test_a_profile_from_before_becomes_the_core_page_and_the_prompt_text_is_what_it_was(chat):  # noqa: F811
    _set_legacy_profile("I run a bakery. Answer briefly.")
    conv = chat.ask(MILL)
    assert (
        GUIDANCE + "I run a bakery. Answer briefly." in chat.state["prompts"][-1]
    )  # the same text and label as before
    ws = workspace_for("personal", user_id=_owner(chat))
    assert core_page.read(ws) == ("I run a bakery. Answer briefly.", "")
    sess = app_mod._SessionFactory()
    assert sess.query(User).one().profile == "I run a bakery. Answer briefly."  # left where it was
    sess.close()
    items = _manifest(chat, conv)["items"]
    core = [i for i in items if i.get("slug") == core_page.SLUG]
    assert len(core) == 1 and core[0]["id"] and core[0]["version"] >= 1
    assert "profile" not in [
        b["type"] for b in _manifest(chat, conv).get("blocks", [])
    ]  # listed as an item, not by hash


def test_an_edit_applies_from_the_next_turn_and_the_record_names_the_new_version(chat):  # noqa: F811
    _set_legacy_profile("I run a bakery.")
    first = chat.ask(MILL)
    ws = workspace_for("personal", user_id=_owner(chat))
    core_page.save(ws, preferences="Always give the answer in one sentence.")
    second = chat.ask(MILL)
    assert "Always give the answer in one sentence." in chat.state["prompts"][-1]
    v1 = next(i for i in _manifest(chat, first)["items"] if i["slug"] == core_page.SLUG)["version"]
    v2 = next(i for i in _manifest(chat, second)["items"] if i["slug"] == core_page.SLUG)["version"]
    assert v2 > v1


def test_a_person_with_no_profile_gets_no_core_page_and_the_same_answer(chat):  # noqa: F811
    conv = chat.ask(MILL)
    ws = workspace_for("personal", user_id=_owner(chat))
    assert not core_page.exists(ws) and GUIDANCE not in chat.state["prompts"][-1]
    assert all(i.get("slug") != core_page.SLUG for i in _manifest(chat, conv)["items"])


def test_a_turn_that_may_not_use_personal_context_does_not_read_the_core_page(chat, monkeypatch):  # noqa: F811
    import dataclasses

    from anthill.web import plane_routing

    real = plane_routing.plane_inference
    monkeypatch.setattr(
        plane_routing,
        "plane_inference",
        lambda *a, **k: dataclasses.replace(real(*a, **k), use_personal_context=False),
    )
    _set_legacy_profile("I keep a Zorp quasar diary.")
    ws = workspace_for("personal", user_id=_owner(chat))
    core_page.save(ws, profile="I keep a Zorp quasar diary.")
    conv = chat.ask(MILL)
    assert "quasar" not in chat.state["prompts"][-1]
    assert all(i.get("slug") != core_page.SLUG for i in _manifest(chat, conv)["items"])


def test_a_turn_that_used_the_core_page_is_not_dropped_from_later_history(chat):  # noqa: F811
    _set_legacy_profile("I run a bakery.")
    conv = chat.ask(MILL)
    sess = app_mod._SessionFactory()
    msgs = (
        sess.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conv)
        .order_by(ChatMessage.id)
        .all()
    )
    assert (
        answer_record.stale_turns(msgs) == set()
    )  # the core page still stands, so its turn is kept
    sess.close()


# ---- "make it yours" writes it -------------------------------------------------------------------------------------------


def test_the_make_it_yours_form_edits_the_core_page_and_shows_it_back(chat):  # noqa: F811
    r = chat.client.post("/personalize", data={"profile": "I teach piano."}, follow_redirects=False)
    assert r.status_code == 302
    ws = workspace_for("personal", user_id=_owner(chat))
    assert core_page.read(ws)[0] == "I teach piano."
    sess = app_mod._SessionFactory()
    assert (sess.query(User).one().profile or "") == ""  # no longer written there
    sess.close()
    assert "I teach piano." in chat.client.get("/personalize").text
    too_long = chat.client.post(
        "/personalize", data={"profile": "p" * (core_page.CAP_CHARS + 5)}, follow_redirects=False
    )
    assert too_long.status_code == 400 and "cut about" in too_long.text
    assert core_page.read(ws)[0] == "I teach piano."  # unchanged by the refused save


# ---- the agent's record --------------------------------------------------------------------------------------------------


def test_an_agent_run_started_with_the_core_page_lists_it_as_an_item(tmp_path):
    ws = Workspace(tmp_path / "w", scope="personal")
    ws.init()
    core_page.save(ws, profile="I run a bakery.")
    text, item = core_page.guidance_for(ws, "")
    ex = AgentExecutor(None, [], model="m")
    ex._begin("About the user:\n" + text)
    ex.note_core_page(text, item)
    rec = ex.record()
    assert [i["slug"] for i in rec["items"]] == [core_page.SLUG] and rec["items"][0]["id"]
    assert "I run a bakery." not in answer_record.dumps(rec)
    assert prompt_blocks.core_page_block(text, {"kind": "page"}) is None
