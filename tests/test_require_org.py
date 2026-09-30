"""A deleted org with a still-valid session must redirect to /login, not 500.

Before `_require_org`, every route did `org = _get_org(db, user)` then `org.id`, so a deleted org
behind a still-valid session JWT raised AttributeError on `None.id` -> 500. `_require_org` turns that
into a clean redirect to login.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod


def _app(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.db import Organization, User

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
    user = User(org_id=org.id, email="m@acme.com", role="member", active=True)
    s.add(user)
    s.commit()
    return TestClient(app_mod.app), app_mod, {"org": org.id, "user": user.id}


def _auth(client, uid, org_id, role="member"):
    from anthill.web.crypto import make_token

    client.cookies.set("session_token", make_token(uid, org_id, role))


def test_require_org_helper_redirects_when_org_missing():
    import anthill.web.app as app_mod

    class _NoOrgDB:
        def query(self, *a, **k):
            return self

        def filter(self, *a, **k):
            return self

        def first(self):
            return None

    with pytest.raises(HTTPException) as exc:
        app_mod._require_org(_NoOrgDB(), {"org": 999})
    assert exc.value.status_code == 303
    assert exc.value.headers.get("Location") == "/login"


def test_deleted_org_redirects_to_login_not_500(tmp_path, monkeypatch):
    from anthill.web.db import Organization

    client, app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["user"], ids["org"])

    # Delete the org out from under the still-valid session. Under strict foreign keys the users must
    # go first (they reference the org), simulating a nuked deployment.
    from anthill.web.db import User

    s = app_mod._SessionFactory()
    s.query(User).delete()
    s.query(Organization).delete()
    s.commit()

    r = client.get("/metrics", follow_redirects=False)
    assert r.status_code == 303  # graceful redirect, not a 500
    assert r.headers.get("location") == "/login"


def test_metrics_still_works_with_a_live_org(tmp_path, monkeypatch):
    client, _app_mod, ids = _app(tmp_path, monkeypatch)
    _auth(client, ids["user"], ids["org"])
    r = client.get("/metrics", follow_redirects=False)
    assert r.status_code == 200  # the happy path is unchanged
