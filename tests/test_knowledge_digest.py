"""Phase 7 of #683: the knowledge digest (spec requirement 5's digest half) - `build_digest()`'s
structured summary, `_digest_due()`'s cadence gate, and `_digest_tick()`'s end-to-end send + stamp."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web.db import AuditLog, Notification, Organization, OrgSettings, User
from anthill.web.digest import build_digest
from anthill.web.scheduler import _digest_due, _digest_tick

# ── build_digest: a simple structured summary from a set of audit rows ──────────


def _session(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    s = sessionmaker(bind=eng)()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    s.add(User(org_id=org.id, email="a@acme.com", role="admin", active=True))
    s.commit()
    return s, org.id


def _row(s, org_id, event, detail="", *, age_days=0):
    row = AuditLog(
        org_id=org_id,
        event=event,
        detail=detail,
        created_at=datetime.now(timezone.utc) - timedelta(days=age_days),
    )
    s.add(row)
    return row


def test_build_digest_buckets_events_by_name(tmp_path):
    s, org_id = _session(tmp_path)
    other_org = Organization(name="Other", slug="other")
    s.add(other_org)
    s.flush()
    _row(s, org_id, "wiki.approved", "slug=a scope=org")
    _row(s, org_id, "wiki.upload", "name=x.pdf scope=org slug=a applied=True")
    _row(s, org_id, "wiki.upload", "name=y.pdf scope=org error=ValueError")  # failed upload
    _row(s, org_id, "wiki.rejected", "slug=b")
    _row(s, org_id, "skill.created", "scope=personal name=Foo")
    _row(s, org_id, "skill.approved", "slug=bar scope=org")
    _row(s, org_id, "skill.proposal_accepted", "id=1 scope=personal")
    _row(s, org_id, "skill.deleted", "scope=org slug=old")
    _row(s, org_id, "skill.rejected", "slug=baz")
    _row(s, org_id, "skill.proposal_rejected", "id=2 scope=personal")
    _row(s, org_id, "principles.approved", "scope=org")
    _row(s, org_id, "principles.rejected", "scope=org")
    _row(s, org_id, "snippet.saved", "tags=x source=chat scope=personal")
    _row(s, org_id, "snippet.to_wiki", "slug=c scope=org applied=True")
    _row(s, org_id, "memory.to_wiki", "id=1 scope=org")
    _row(s, org_id, "memory.promote_team", "id=2 team=1")
    # noise that must NOT be counted: a different org, a non-knowledge event, and an out-of-window row
    _row(s, org_id, "memory.add", "remember")  # plain memory CRUD, not a knowledge-digest event
    _row(s, other_org.id, "wiki.approved", "slug=other-org")
    _row(s, org_id, "wiki.approved", "slug=too-old", age_days=40)
    s.commit()

    since = datetime.now(timezone.utc) - timedelta(days=7)
    summary = build_digest(s, org_id, since)

    assert (
        summary.pages_changed == 2
    )  # wiki.approved + the successful wiki.upload (not the failed one)
    assert summary.pages_rejected == 1
    assert summary.skills_learned == 2  # skill.created + skill.approved
    assert summary.skills_adopted == 1
    assert summary.skills_deleted == 1
    assert summary.skills_rejected == 2  # skill.rejected + skill.proposal_rejected
    assert summary.principles_changed == 1
    assert summary.principles_rejected == 1
    assert summary.snippets_captured == 1
    assert summary.promotions == 3  # snippet.to_wiki + memory.to_wiki + memory.promote_team
    assert summary.total_events == 16  # every row above except memory.add / other-org / too-old
    assert not summary.is_empty


def test_build_digest_is_empty_for_a_quiet_window(tmp_path):
    s, org_id = _session(tmp_path)
    since = datetime.now(timezone.utc) - timedelta(days=1)
    summary = build_digest(s, org_id, since)
    assert summary.is_empty and summary.total_events == 0


# ── _digest_due: respects the configured cadence ─────────────────────────────────


def test_digest_due_off_schedule_is_never_due():
    now = datetime.now(timezone.utc)
    assert _digest_due("off", None, now) is False
    assert _digest_due("off", now - timedelta(days=30), now) is False


def test_digest_due_never_sent_is_due_immediately():
    now = datetime.now(timezone.utc)
    assert _digest_due("daily", None, now) is True
    assert _digest_due("weekly", None, now) is True


def test_digest_due_daily_respects_the_window():
    now = datetime.now(timezone.utc)
    assert _digest_due("daily", now - timedelta(hours=1), now) is False  # too soon
    assert _digest_due("daily", now - timedelta(days=1, minutes=1), now) is True  # a day elapsed


def test_digest_due_weekly_respects_the_window():
    now = datetime.now(timezone.utc)
    assert _digest_due("weekly", now - timedelta(days=3), now) is False
    assert _digest_due("weekly", now - timedelta(weeks=1, minutes=1), now) is True


def test_digest_due_handles_naive_datetime_from_sqlite_roundtrip():
    """SQLite round-trips DateTime columns naive; _digest_due must not raise comparing that against
    an aware `now` (mirrors events.should_drain's same defensive handling)."""
    now = datetime.now(timezone.utc)
    naive_last_sent = (now - timedelta(days=2)).replace(tzinfo=None)
    assert _digest_due("daily", naive_last_sent, now) is True


# ── _digest_tick: end-to-end send + stamp, and no double-send ───────────────────


def _tick_engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    s = sessionmaker(bind=eng)()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    admin = User(org_id=org.id, email="admin@acme.com", role="admin", active=True)
    s.add(admin)
    s.add(OrgSettings(org_id=org.id, digest_schedule="daily"))
    s.add(AuditLog(org_id=org.id, event="wiki.approved", detail="slug=a scope=org"))
    s.commit()
    return eng, org.id, admin.id


def test_digest_tick_sends_when_due_and_stamps_last_sent(tmp_path):
    eng, org_id, admin_id = _tick_engine(tmp_path)
    _digest_tick(eng)

    s = sessionmaker(bind=eng)()
    cfg = s.query(OrgSettings).filter_by(org_id=org_id).first()
    assert cfg.digest_last_sent_at is not None  # stamped even though never sent before -> was "due"

    notes = s.query(Notification).filter_by(user_id=admin_id, kind="digest").all()
    assert len(notes) == 1
    assert "1 page(s) changed" in notes[0].body


def test_digest_tick_does_not_resend_before_due(tmp_path):
    eng, _org_id, admin_id = _tick_engine(tmp_path)
    _digest_tick(eng)  # first tick: due (never sent) -> sends + stamps
    _digest_tick(eng)  # immediately again: NOT due (daily, just stamped) -> no second notification

    s = sessionmaker(bind=eng)()
    notes = s.query(Notification).filter_by(user_id=admin_id, kind="digest").all()
    assert len(notes) == 1


def test_digest_tick_is_a_noop_when_schedule_is_off(tmp_path):
    eng = create_engine(
        f"sqlite:///{tmp_path / 'off.db'}", connect_args={"check_same_thread": False}
    )
    db_mod.create_tables(eng)
    s = sessionmaker(bind=eng)()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    s.add(User(org_id=org.id, email="a@acme.com", role="admin", active=True))
    s.add(OrgSettings(org_id=org.id, digest_schedule="off"))
    s.add(AuditLog(org_id=org.id, event="wiki.approved", detail="slug=a"))
    s.commit()

    _digest_tick(eng)
    s2 = sessionmaker(bind=eng)()
    assert s2.query(Notification).count() == 0
    assert s2.query(OrgSettings).first().digest_last_sent_at is None


# ── the admin UI: the Audit page's digest card + POST /settings/digest ──────────


def _admin_client(tmp_path, monkeypatch):
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
    admin = User(org_id=org.id, email="admin@acme.com", role="admin", active=True)
    s.add(admin)
    s.commit()
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(admin.id, org.id, "admin"))
    return app_mod, client, org.id


def test_audit_page_hides_the_digest_card_with_no_org_settings_row_yet(tmp_path, monkeypatch):
    _app_mod, client, _org_id = _admin_client(tmp_path, monkeypatch)
    r = client.get("/audit")
    assert r.status_code == 200
    assert "Knowledge digest" not in r.text


def test_audit_page_shows_the_current_cadence(tmp_path, monkeypatch):
    app_mod, client, org_id = _admin_client(tmp_path, monkeypatch)
    s = app_mod._SessionFactory()
    s.add(OrgSettings(org_id=org_id, digest_schedule="weekly"))
    s.commit()
    r = client.get("/audit")
    assert r.status_code == 200
    assert "Knowledge digest" in r.text
    assert 'value="weekly" selected' in r.text


def test_settings_digest_route_saves_the_cadence(tmp_path, monkeypatch):
    app_mod, client, org_id = _admin_client(tmp_path, monkeypatch)
    r = client.post("/settings/digest", data={"digest_schedule": "daily"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/audit"
    cfg = app_mod._SessionFactory().query(OrgSettings).filter_by(org_id=org_id).first()
    assert cfg.digest_schedule == "daily"


def test_settings_digest_route_rejects_an_invalid_value(tmp_path, monkeypatch):
    app_mod, client, org_id = _admin_client(tmp_path, monkeypatch)
    client.post("/settings/digest", data={"digest_schedule": "hourly"}, follow_redirects=False)
    cfg = app_mod._SessionFactory().query(OrgSettings).filter_by(org_id=org_id).first()
    assert cfg.digest_schedule == "off"  # invalid value falls back to the safe default
