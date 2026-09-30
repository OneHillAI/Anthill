"""Issue #451: pre-auth security events (failed logins, reset probes) must be visible to admins and
counted by the anomaly detector, not silently dropped because they were logged with org_id=NULL."""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
import anthill.web.db as db_mod
from anthill.web import audit
from anthill.web.crypto import hash_password
from anthill.web.db import AuditLog, Organization, User


def _session(tmp_path):
    from fk_seed import seed_org_and_users

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    s = sessionmaker(bind=eng, autoflush=False, autocommit=False)()
    seed_org_and_users(s, user_ids=(1, 2, 3))
    s.commit()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    return s, org.id


def test_attributed_login_fail_shows_in_its_org(tmp_path):
    """A wrong password for a real account is attributed to that account's org and shows there."""
    s, org_id = _session(tmp_path)
    audit.log(s, "user.login_fail", "email=u@x.com", org_id=org_id, user_id=1, ip="1.2.3.4")
    events = audit.recent_events(s, org_id)
    assert [e.event for e in events] == ["user.login_fail"]
    assert events[0].org_id == org_id and events[0].user_id == 1


def test_unknown_email_fails_are_surfaced_and_detected(tmp_path):
    """Unknown-email brute force is logged org_id=NULL; it must still appear in the admin Audit view
    and trip the brute-force rule (the bug: both used to filter it out)."""
    s, org_id = _session(tmp_path)
    for _ in range(6):  # rule fires above 5 in 10 min
        audit.log(
            s, "user.login_fail", "email=nobody@x.com", ip="9.9.9.9"
        )  # org_id defaults to NULL
    events = audit.recent_events(s, org_id)
    assert len(events) == 6 and all(e.org_id is None for e in events)
    alerts = audit.check_anomalies(s, org_id)
    assert any("Brute-force" in a for a in alerts), alerts


def test_reset_noop_and_throttle_and_denied_are_visible(tmp_path):
    """All four pre-auth event types surface even when unattributed."""
    s, org_id = _session(tmp_path)
    for ev in ("user.reset_noop", "user.login_throttled", "user.login_denied"):
        audit.log(s, ev, "email=x@y.com", ip="2.2.2.2")  # org_id NULL
    seen = {e.event for e in audit.recent_events(s, org_id)}
    assert seen == {"user.reset_noop", "user.login_throttled", "user.login_denied"}


def test_non_security_null_events_are_not_leaked_into_the_view(tmp_path):
    """Only pre-auth security events are surfaced from the NULL bucket - a stray NULL-org non-security
    event (or another org's row) must never appear in this org's Audit log."""
    s, org_id = _session(tmp_path)
    s.add(AuditLog(org_id=None, event="wiki.upload", detail="should stay hidden"))
    other = Organization(name="Other", slug="other")
    s.add(other)
    s.flush()
    audit.log(s, "user.login", "email=a@b.com", org_id=other.id, user_id=2)  # different org
    s.commit()
    assert audit.recent_events(s, org_id) == []


def _client(tmp_path, monkeypatch, *, active=True):
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
            active=active,
            role="member",
        )
    )
    s.commit()
    return TestClient(app_mod.app), org.id


def test_route_wrong_password_attributes_org(tmp_path, monkeypatch):
    """End-to-end: a wrong password for a real account logs login_fail WITH the account's org_id."""
    client, org_id = _client(tmp_path, monkeypatch)
    r = client.post(
        "/login", data={"email": "u@x.com", "password": "wrongpass12345"}, follow_redirects=False
    )
    assert r.status_code == 401
    s = app_mod._SessionFactory()
    row = s.query(AuditLog).filter(AuditLog.event == "user.login_fail").first()
    assert row is not None and row.org_id == org_id and row.user_id is not None


def test_route_unknown_email_stays_null(tmp_path, monkeypatch):
    """A login attempt for an unknown email can't be attributed - it stays org_id=NULL."""
    client, org_id = _client(tmp_path, monkeypatch)
    client.post(
        "/login", data={"email": "ghost@x.com", "password": "whatever12345"}, follow_redirects=False
    )
    s = app_mod._SessionFactory()
    row = s.query(AuditLog).filter(AuditLog.event == "user.login_fail").first()
    assert row is not None and row.org_id is None
    # ...but it is still visible to the org's admin and counts for detection (via recent_events).
    assert any(e.event == "user.login_fail" for e in audit.recent_events(s, org_id))
