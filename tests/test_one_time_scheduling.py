"""One-shot local scheduling uses the existing occurrence ledger, not a recurrence."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import scheduler, task_occurrences
from anthill.web.db import Organization, ScheduledTask, TaskOccurrence, User, create_tables


@pytest.fixture
def client(tmp_path, monkeypatch):
    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    engine = create_engine(
        f"sqlite:///{tmp_path / 'tasks.db'}", connect_args={"check_same_thread": False}
    )
    create_tables(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(app_mod, "_engine", engine)
    monkeypatch.setattr(app_mod, "_SessionFactory", factory)
    monkeypatch.setattr(app_mod, "_scheduler_started", True)
    with factory() as session:
        org = Organization(name="A", slug="a")
        session.add(org)
        session.flush()
        user = User(org_id=org.id, email="u@a.com", role="member", active=True)
        session.add(user)
        session.commit()
        token = make_token(user.id, org.id, "member")
    with TestClient(app_mod.app) as client:
        client.cookies.set("session_token", token)
        yield client, factory
    engine.dispose()


def _form(**changes):
    return {
        "title": "One-shot",
        "goal": "Summarize",
        "schedule": "once",
        "timezone": "Europe/Madrid",
        "once_mode": "at",
        "once_at": "2099-07-12T09:15:30",
        **changes,
    }


@pytest.mark.parametrize("zone", ["Europe/Madrid", "America/New_York", "Asia/Kathmandu", ""])
def test_create_edit_round_trip_and_completion(client, monkeypatch, zone):
    c, factory = client
    form = _form(timezone=zone)
    due = (
        datetime(2099, 7, 12, 9, 15, 30, tzinfo=ZoneInfo(zone or "UTC"))
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )
    assert c.post("/tasks/create", data=form, follow_redirects=False).status_code == 302
    with factory() as s:
        task = s.query(ScheduledTask).one()
        tid = task.id
        assert task.schedule == "once" and task.next_run_at == due
        assert s.query(TaskOccurrence).one().due_at == due
    assert 'data-once-at="2099-07-12T09:15:30"' in c.get("/tasks").text
    assert (
        c.post(
            f"/tasks/{tid}/edit", data=_form(timezone=zone, title="Renamed"), follow_redirects=False
        ).status_code
        == 302
    )
    with factory() as s:
        assert s.get(ScheduledTask, tid).next_run_at == due
        assert s.query(TaskOccurrence).count() == 1

    changed = _form(timezone=zone, once_at="2099-07-13T10:00:00")
    assert c.post(f"/tasks/{tid}/edit", data=changed, follow_redirects=False).status_code == 302
    with factory() as s:
        task = s.get(ScheduledTask, tid)
        assert task.next_run_at != due
        due = task.next_run_at
    calls = []
    monkeypatch.setattr(scheduler, "_run_task", lambda task, db: calls.append(task.id) or "Done")
    monkeypatch.setattr(scheduler, "_verify_task_result", lambda *args: None)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return due.replace(tzinfo=timezone.utc)

    monkeypatch.setattr(scheduler, "datetime", Clock)
    scheduler._tick(factory.kw["bind"])
    scheduler._tick(factory.kw["bind"])
    with factory() as s:
        task = s.get(ScheduledTask, tid)
        assert task.next_run_at is None and task.status == "done"
        assert task.run_count == 1 and calls == [tid]
        assert s.query(TaskOccurrence).count() == 1
        assert s.query(TaskOccurrence).one().status == "completed"
    assert (
        c.post(
            f"/tasks/{tid}/edit", data=_form(once_mode="now"), follow_redirects=False
        ).status_code
        == 302
    )
    with factory() as s:
        assert s.get(ScheduledTask, tid).next_run_at is None
        assert s.query(TaskOccurrence).count() == 1


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"once_at": ""}, "valid local"),
        ({"once_at": "garbage"}, "valid local"),
        ({"once_at": "2099-02-30T09:00"}, "valid local"),
        ({"once_at": "2000-01-01T09:00"}, "future"),
        ({"once_at": "2099-07-12T09:00+02:00"}, "valid local"),
        ({"timezone": "Not/AZone"}, "timezone"),
        ({"timezone": "America/New_York", "once_at": "2099-03-08T02:30"}, "does not exist"),
        ({"timezone": "America/New_York", "once_at": "2099-11-01T01:30"}, "ambiguous"),
        ({"once_mode": "invalid"}, "choice"),
        ({"schedule": "daily"}, "choice"),
        ({"run_now": "true"}, "either"),
    ],
)
def test_invalid_create_and_edit_leave_task_unchanged(client, changes, message):
    c, factory = client
    response = c.post("/tasks/create", data=_form(**changes), follow_redirects=False)
    assert response.status_code == 400 and message in response.json()["error"]
    with factory() as s:
        assert s.query(ScheduledTask).count() == 0
    assert c.post("/tasks/create", data=_form(), follow_redirects=False).status_code == 302
    with factory() as s:
        task = s.query(ScheduledTask).one()
        tid, due = task.id, task.next_run_at
    # run_now is a creation-only field; all remaining invalid inputs apply to edits too.
    if "run_now" not in changes:
        response = c.post(
            f"/tasks/{tid}/edit", data=_form(title="Broken", **changes), follow_redirects=False
        )
        assert response.status_code == 400 and message in response.json()["error"]
    with factory() as s:
        task = s.get(ScheduledTask, tid)
        assert task.title == "One-shot" and task.next_run_at == due
        assert s.query(TaskOccurrence).count() == 1


def test_explicit_now_creates_one_occurrence_and_switching_to_now(client):
    c, factory = client
    assert (
        c.post("/tasks/create", data=_form(once_mode="now"), follow_redirects=False).status_code
        == 302
    )
    with factory() as s:
        task = s.query(ScheduledTask).one()
        assert task.next_run_at <= datetime.now(timezone.utc).replace(tzinfo=None)
        assert s.query(TaskOccurrence).count() == 1
    assert (
        c.post("/tasks/create", data=_form(title="Future"), follow_redirects=False).status_code
        == 302
    )
    with factory() as s:
        tid = s.query(ScheduledTask).filter_by(title="Future").one().id
    assert (
        c.post(
            f"/tasks/{tid}/edit", data=_form(once_mode="now"), follow_redirects=False
        ).status_code
        == 302
    )
    with factory() as s:
        assert s.get(ScheduledTask, tid).next_run_at <= datetime.now(timezone.utc).replace(
            tzinfo=None
        )
        assert s.query(TaskOccurrence).filter_by(task_id=tid).count() == 1


def test_active_scheduled_run_cannot_be_rescheduled(client):
    c, factory = client
    c.post("/tasks/create", data=_form())
    with factory() as s:
        task = s.query(ScheduledTask).one()
        tid, due = task.id, task.next_run_at
        assert task_occurrences.claim(s, tid, due.replace(tzinfo=timezone.utc)) is not None
        s.commit()
    response = c.post(f"/tasks/{tid}/edit", data=_form(title="Changed"), follow_redirects=False)
    assert response.status_code == 409
    with factory() as s:
        assert s.get(ScheduledTask, tid).title == "One-shot"
        assert s.query(TaskOccurrence).one().status == "claimed"


def test_manual_occurrence_and_legacy_edit_preserved(client):
    c, factory = client
    c.post("/tasks/create", data=_form())
    with factory() as s:
        task = s.query(ScheduledTask).one()
        tid, due = task.id, task.next_run_at
    response = c.post(
        f"/tasks/{tid}/edit",
        data={
            "title": "Legacy rename",
            "goal": "g",
            "schedule": "once",
            "timezone": "America/New_York",
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    with factory() as s:
        assert s.get(ScheduledTask, tid).next_run_at == due
    c.post(f"/tasks/{tid}/run-now")
    with factory() as s:
        manual = s.query(TaskOccurrence).filter_by(kind="manual").one()
        manual_id, manual_due = manual.id, manual.due_at
    assert (
        c.post(
            f"/tasks/{tid}/edit", data=_form(once_at="2099-08-12T09:15:30"), follow_redirects=False
        ).status_code
        == 302
    )
    with factory() as s:
        assert s.get(TaskOccurrence, manual_id).due_at == manual_due
        assert s.query(TaskOccurrence).filter_by(kind="scheduled").one().due_at != due
        assert s.query(TaskOccurrence).count() == 2
