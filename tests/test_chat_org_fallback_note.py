"""PR #661 Tier 3: a visible note for the already-automatic private-chat fallback.

plane_routing.py already falls back a Solo-plane chat borrowing the org model to the local model when
the org backend is unreachable (docs/specs/one-model-per-account-solo-in-org.md) - this only makes that
existing behavior visible in chat.html, via a new IS_ORG_MODE flag. A plain (non-org) Solo account never
sets this flag, so its chat is unaffected.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import Organization, OrgSettings, User


def _app(tmp_path, monkeypatch, *, org_provider="runpod", org_backend_status="provisioned"):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    user = User(org_id=org.id, email="u@acme.com", role="member", active=True)
    s.add(user)
    s.flush()
    s.add(
        OrgSettings(
            org_id=org.id,
            deployment_topology="org",
            org_provider=org_provider,
            org_backend_status=org_backend_status,
        )
    )
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "member"))
    return client


def test_org_account_chat_sets_is_org_mode_true(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    body = client.get("/chat", follow_redirects=True).text
    assert "const IS_ORG_MODE = true;" in body
    assert "IS_ORG_MODE) { setSoloOrgFallbackStatus(d); return; }" in body
    assert "Using your" in body and "local model" in body and "org model unreachable" in body


def test_plain_solo_account_chat_sets_is_org_mode_false(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis2"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org2"))
    eng = create_engine(
        f"sqlite:///{tmp_path / 'app2.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="Solo", slug="solo")
    s.add(org)
    s.flush()
    user = User(org_id=org.id, email="s@solo.com", role="admin", active=True)
    s.add(user)
    s.flush()
    s.add(OrgSettings(org_id=org.id, deployment_topology="solo"))
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(user.id, org.id, "admin"))

    body = client.get("/chat", follow_redirects=True).text
    assert "const IS_ORG_MODE = false;" in body


def test_team_org_plane_gating_is_unchanged(tmp_path, monkeypatch):
    # The existing "disabled, here's why" behavior for a TRUE team/org-plane chat (grounded in the
    # shared org wiki, which genuinely has no local fallback) must be untouched by this change.
    client = _app(tmp_path, monkeypatch)
    body = client.get("/chat", follow_redirects=True).text
    assert "setOrgChatEnabled" in body
    assert "Org backend is not responding" in body or "neocloud cold start" in body
