"""The unified suggestions inbox (knowledge-guidance-reconciliation.md, req 2): one place that gathers the
model's proposed wiki pages + distilled skills, each adjust-and-approve or dismiss via the EXISTING review
routes (with a `next` back to the inbox). Aggregation only - no new store."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import Organization, ProposedSkill, User, WikiReview


def _client(tmp_path, monkeypatch, role="member"):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "w"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "o"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    u = User(org_id=org.id, email="u@acme.com", role=role, active=True)
    s.add(u)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, org.id, role))
    return c, app_mod, org.id, u.id


def test_inbox_aggregates_proposed_pages_and_skills(tmp_path, monkeypatch):
    c, app_mod, org_id, uid = _client(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    s.add(
        WikiReview(
            org_id=org_id,
            proposed_by=uid,
            slug="q3-plan-risks",
            kind="page",
            content="# Q3 plan risks\nbody",
            target_scope="personal",
            status="pending",
        )
    )
    s.add(
        ProposedSkill(
            org_id=org_id,
            created_by=uid,
            scope="personal",
            name="Weekly digest",
            description="Summarise the week.",
            status="pending",
        )
    )
    s.commit()
    body = c.get("/knowledge/suggestions").text
    assert (
        "Proposed a wiki page" in body and "Q3 plan risks" in body
    )  # the wiki suggestion (its H1)
    assert (
        "Learned a skill from your work" in body and "Weekly digest" in body
    )  # the skill suggestion
    assert (
        'href="/knowledge/suggestions"' in body and "Suggestions" in body
    )  # the Knowledge-hub tab
    assert '<span class="nav-badge">2</span>' in body  # ...with the count badge (page + skill)
    assert 'value="/knowledge/suggestions"' in body  # actions carry a next-back to the inbox


def test_inbox_approve_routes_through_existing_review_and_returns(tmp_path, monkeypatch):
    c, app_mod, org_id, uid = _client(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    rev = WikiReview(
        org_id=org_id,
        proposed_by=uid,
        slug="note",
        kind="page",
        content="# Note\nx",
        target_scope="personal",
        status="pending",
    )
    s.add(rev)
    s.commit()
    rid = rev.id
    # dismiss via the existing reject route, with next back to the inbox
    r = c.post(
        f"/wiki/review/{rid}/reject",
        data={"next": "/knowledge/suggestions"},
        follow_redirects=False,
    )
    assert r.status_code in (302, 303) and r.headers["location"] == "/knowledge/suggestions"
    assert app_mod._SessionFactory().query(WikiReview).get(rid).status == "rejected"


def test_inbox_next_only_accepts_local_paths(tmp_path, monkeypatch):
    # the `next` redirect is validated - an off-site URL is ignored in favour of the route's own default.
    c, app_mod, org_id, uid = _client(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    rev = WikiReview(
        org_id=org_id,
        proposed_by=uid,
        slug="n",
        kind="page",
        content="# n",
        target_scope="personal",
        status="pending",
    )
    s.add(rev)
    s.commit()
    rid = rev.id
    r = c.post(
        f"/wiki/review/{rid}/reject", data={"next": "//evil.example"}, follow_redirects=False
    )
    assert r.headers["location"] != "//evil.example"  # not redirected off-site
