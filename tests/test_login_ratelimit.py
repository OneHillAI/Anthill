"""Login brute-force throttle: an IP is blocked after too many failed sign-ins."""

from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
import anthill.web.db as db_mod
from anthill.web.crypto import hash_password
from anthill.web.db import AuditLog, Organization, User


def _client(tmp_path, monkeypatch):
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
    s.add(
        User(
            org_id=org.id,
            email="u@x.com",
            hashed_password=hash_password("rightpassword12"),
            active=True,
            role="member",
        )
    )
    s.commit()
    return TestClient(app_mod.app)


def _seed_fails(n, ip="testclient"):
    s = app_mod._SessionFactory()
    for _ in range(n):
        s.add(
            AuditLog(
                event="user.login_fail",
                ip=ip,
                detail="email=u@x.com",
                created_at=datetime.now(timezone.utc),
            )
        )
    s.commit()


def test_login_blocked_after_too_many_fails(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _seed_fails(10)  # at the threshold
    # even the CORRECT password is rejected while throttled
    r = client.post(
        "/login",
        data={"email": "u@x.com", "password": "rightpassword12"},
        follow_redirects=False,
    )
    assert r.status_code == 429
    assert "Too many failed attempts" in r.text


def test_login_ok_below_threshold(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    _seed_fails(3)
    # a wrong password below the threshold gets the normal 401 (not throttled)...
    bad = client.post(
        "/login", data={"email": "u@x.com", "password": "wrongpass12345"}, follow_redirects=False
    )
    assert bad.status_code == 401
    # ...and the right password still signs in
    ok = client.post(
        "/login", data={"email": "u@x.com", "password": "rightpassword12"}, follow_redirects=False
    )
    assert ok.status_code == 302
