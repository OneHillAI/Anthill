"""A page an agent writes is a proposal for a person to review, and never reaches the wiki or an answer before
they approve it. Model-free: the review check is a stub."""

import hashlib
import json
import types
from pathlib import Path

import pytest

from anthill.agent.tools import _write_wiki_draft, make_tools
from anthill.web.agent_writes import wiki_proposer
from anthill.web.db import Team, TeamMembership, WikiReview
from anthill.wiki import items
from anthill.wiki.workspace import Workspace
from tests.test_upload_status import _login, _setup

PAGE = "# Q4 plan\n\nThe launch moves to March.\n"


def _tree(root: Path) -> dict:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _tool(tools, name):
    return next(t for t in tools if t.name == name)


# ── the tool itself ───────────────────────────────────────────────────────────


def test_without_a_reviewer_the_tool_writes_nothing(tmp_path):
    ws = Workspace(tmp_path / "w")
    ws.init()
    ws.write_page("existing", "# Existing\n\nKept.\n")
    before = _tree(ws.root)
    out = _tool(make_tools(workspace=str(ws.root)), "write_wiki_draft").fn("q4-plan", PAGE)
    assert "not available" in out and _tree(ws.root) == before


def test_with_a_reviewer_the_page_goes_to_it_and_not_to_the_wiki(tmp_path):
    ws = Workspace(tmp_path / "w")
    ws.init()
    before = _tree(ws.root)
    seen: list = []

    def propose(slug, content):
        seen.append((slug, content))
        return "queued"

    tools = make_tools(workspace=str(ws.root), propose_write=propose)
    assert _tool(tools, "write_wiki_draft").fn("Q4 Plan", PAGE) == "queued"
    assert seen[0][0] == "q4-plan" and "launch moves to March" in seen[0][1]
    assert _tree(ws.root) == before  # nothing was written here


@pytest.mark.parametrize("slug", ["../../etc/passwd", "a/b/../c", "../x"])
def test_a_slug_cannot_leave_the_wiki(slug):
    seen: list = []
    _write_wiki_draft(slug, PAGE, lambda s, c: seen.append(s) or "ok")
    assert seen and "/" not in seen[0] and ".." not in seen[0]


def test_the_tool_no_longer_claims_what_it_does_not_do():
    desc = _tool(make_tools(), "write_wiki_draft").description
    assert "A person reviews it" in desc and "pending review" not in desc


# ── through the web app ───────────────────────────────────────────────────────


def _reviews(app_mod):
    s = app_mod._SessionFactory()
    try:
        return s.query(WikiReview).order_by(WikiReview.id).all()
    finally:
        s.close()


def _propose(app_mod, ids, ws_root, text=PAGE, slug="q4-plan", who="owner"):
    uid, org_id, _role = ids[who]
    s = app_mod._SessionFactory()
    try:
        fn = wiki_proposer(
            s, org_id=org_id, user_id=uid, ws_path=str(ws_root), who="the chat agent"
        )
        return fn(slug, text)
    finally:
        s.close()


def test_an_agent_page_is_queued_for_review_and_the_wiki_is_untouched(tmp_path, monkeypatch):
    app_mod, _e, _c, ids = _setup(tmp_path, monkeypatch)
    ws = Workspace(tmp_path / "wikis" / f"user-{ids['owner'][0]}", scope="personal")
    ws.init()
    out = _propose(app_mod, ids, ws.root)
    assert "not in the wiki until a person approves it" in out
    assert not (ws.wiki / "q4-plan.md").exists()
    (review,) = _reviews(app_mod)
    assert review.status == "pending" and review.slug == "q4-plan"
    assert review.target_scope == "personal" and review.proposed_by == ids["owner"][0]
    assert "agent-written" in json.loads(review.flags)
    assert "proposed by the chat agent" in review.diff_summary
    assert "launch moves to March" in review.content and "model=agent" in review.content


def test_a_clean_agent_page_is_still_queued_in_every_wiki(tmp_path, monkeypatch):
    app_mod, _e, _c, ids = _setup(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    team = Team(org_id=ids["owner"][1], name="Bakers", slug="bakers", owner_id=ids["owner"][0])
    s.add(team)
    s.flush()
    s.add(TeamMembership(team_id=team.id, user_id=ids["owner"][0], role="owner", status="active"))
    s.commit()
    tid = team.id
    s.close()
    roots = {
        "personal": tmp_path / "wikis" / f"user-{ids['owner'][0]}",
        "team": tmp_path / "wikis" / f"team-{tid}",
        "org": tmp_path / "org",
    }
    for scope, root in roots.items():
        Workspace(root, scope=scope).init()
        _propose(app_mod, ids, root, slug=f"plan-{scope}")
        assert not (root / "wiki" / f"plan-{scope}.md").exists()
    got = {r.target_scope: r.team_id for r in _reviews(app_mod)}
    assert got == {"personal": None, "team": tid, "org": None}


def test_a_proposal_does_not_replace_an_existing_page_or_reach_answers(tmp_path, monkeypatch):
    app_mod, _e, _c, ids = _setup(tmp_path, monkeypatch)
    ws = Workspace(tmp_path / "wikis" / f"user-{ids['owner'][0]}", scope="personal")
    ws.init()
    ws.write_page("q4-plan", "# Q4 plan\n\nThe launch is in January.\n")
    _propose(app_mod, ids, ws.root, text="# Q4 plan\n\nEverything is cancelled.\n")
    assert "January" in (ws.wiki / "q4-plan.md").read_text()
    assert [p.stem for p in ws.retrievable_pages()] == ["q4-plan"]
    assert "cancelled" not in (ws.wiki / "q4-plan.md").read_text()


def test_approving_the_proposal_writes_it_as_an_ai_written_page_a_person_checked(
    tmp_path, monkeypatch
):
    app_mod, _e, client, ids = _setup(tmp_path, monkeypatch)
    ws = Workspace(tmp_path / "wikis" / f"user-{ids['owner'][0]}", scope="personal")
    ws.init()
    _propose(app_mod, ids, ws.root)
    (review,) = _reviews(app_mod)
    _login(client, ids["owner"])
    res = client.post(f"/wiki/review/{review.id}/approve", follow_redirects=False)
    assert res.status_code in (200, 302, 303)
    page = ws.wiki / "q4-plan.md"
    assert "launch moves to March" in page.read_text()
    rec = items.read_page_record(page.read_text())
    assert rec.origin == "model-written" and rec.review_state == "checked"


def test_an_agent_to_agent_run_has_nobody_to_review_so_it_cannot_write(tmp_path, monkeypatch):
    app_mod, _e, _c, ids = _setup(tmp_path, monkeypatch)
    ws = Workspace(tmp_path / "org", scope="org")
    ws.init()
    s = app_mod._SessionFactory()
    try:
        assert (
            wiki_proposer(s, org_id=ids["owner"][1], user_id=None, ws_path=str(ws.root), who="a2a")
            is None
        )
    finally:
        s.close()
    out = _tool(make_tools(workspace=str(ws.root), propose_write=None), "write_wiki_draft").fn(
        "x", PAGE
    )
    assert "not available" in out and not list(ws.wiki.glob("*.md"))


def test_a_persistent_agent_proposes_instead_of_writing(tmp_path, monkeypatch):
    from anthill.web.agents_run import _plane_tools

    app_mod, _e, _c, ids = _setup(tmp_path, monkeypatch)
    uid, org_id, _ = ids["admin"]
    root = tmp_path / "org"
    Workspace(root, scope="org").init()
    agent = types.SimpleNamespace(
        id=1,
        org_id=org_id,
        created_by=uid,
        team_id=None,
        governance="standard",
        connectors="",
        name="Scribe",
    )
    plane = types.SimpleNamespace(wiki_scope="org", use_personal_context=False)
    monkeypatch.setattr("anthill.web.agent_context.run_wiki_workspace", lambda *a, **k: str(root))
    s = app_mod._SessionFactory()
    try:
        tools, ws_path = _plane_tools(s, agent, plane)
        out = _tool(tools, "write_wiki_draft").fn("handbook", PAGE)
    finally:
        s.close()
    assert ws_path == str(root) and "not in the wiki until a person approves it" in out
    assert not (root / "wiki" / "handbook.md").exists()
    (review,) = _reviews(app_mod)
    assert review.target_scope == "org" and "agent Scribe" in review.diff_summary
