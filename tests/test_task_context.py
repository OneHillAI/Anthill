"""Task attachments: form parsing, run-time context assembly (files via MCP + snippets),
and the create route storing the refs."""

import json
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db, task_context
from anthill.web.db import (
    MCPServer,
    Organization,
    ScheduledTask,
    Snippet,
    TaskOccurrence,
    TaskRun,
    User,
)


def test_refs_from_form_parses_and_skips_junk():
    out = task_context.refs_from_form(["3|folder/a.md", "bad", "|noid", "7|"], ["5", "9", "x"])
    assert json.loads(out) == {
        "files": [{"server_id": 3, "ref": "folder/a.md"}],
        "snippets": [5, 9],
    }
    assert task_context.refs_from_form([], []) == ""  # nothing attached -> empty


def _db_with(tmp_path):
    from fk_seed import seed_org_and_users

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    s = sessionmaker(bind=eng)()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    seed_org_and_users(s, org_id=o.id)  # users for the snippets/files below (org already created)
    s.commit()
    return s, o


def test_build_task_context_assembles_files_and_snippets(tmp_path, monkeypatch):
    s, o = _db_with(tmp_path)
    srv = MCPServer(org_id=o.id, name="Files", catalog_id="filesystem", status="approved")
    gone = MCPServer(org_id=o.id, name="Pending", catalog_id="filesystem", status="pending")
    s.add_all([srv, gone])
    snip = Snippet(
        org_id=o.id, user_id=1, content="the saved snippet body", question="why it matters"
    )
    s.add(snip)
    s.commit()

    monkeypatch.setattr(task_context, "read_document", lambda server, ref: f"BODY of {ref}")
    task = SimpleNamespace(
        org_id=o.id,
        context_refs=json.dumps(
            {
                "files": [
                    {"server_id": srv.id, "ref": "plan.md"},
                    {"server_id": gone.id, "ref": "skip.md"},  # not approved -> skipped
                    {"server_id": 9999, "ref": "missing.md"},  # no such server -> skipped
                ],
                "snippets": [snip.id, 12345],  # 12345 does not exist -> skipped
            }
        ),
    )
    ctx = task_context.build_task_context(s, task)
    assert "BODY of plan.md" in ctx and "from Files" in ctx
    assert "skip.md" not in ctx and "missing.md" not in ctx
    assert "the saved snippet body" in ctx and "why it matters" in ctx


def test_build_task_context_empty_when_no_refs(tmp_path):
    s, o = _db_with(tmp_path)
    assert task_context.build_task_context(s, SimpleNamespace(org_id=o.id, context_refs="")) == ""


def test_create_task_stores_attachments(tmp_path):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="u@a.com", role="member", active=True)
    s.add(u)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "member"))

    r = c.post(
        "/tasks/create",
        data={
            "title": "Digest",
            "goal": "summarise the attached file",
            "schedule": "once",
            "context_files": ["4|reports/q3.md"],
            "context_snippets": ["2"],
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    row = app_mod._SessionFactory().query(ScheduledTask).filter(ScheduledTask.org_id == o.id).one()
    assert json.loads(row.context_refs) == {
        "files": [{"server_id": 4, "ref": "reports/q3.md"}],
        "snippets": [2],
    }


def test_create_and_edit_task_store_timezone(tmp_path):
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="u@a.com", role="member", active=True)
    s.add(u)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "member"))

    invalid = c.post(
        "/tasks/create",
        data={"title": "Broken", "goal": "g", "schedule": "25:99"},
        follow_redirects=False,
    )
    assert invalid.status_code == 400
    assert app_mod._SessionFactory().query(ScheduledTask).count() == 0

    r = c.post(
        "/tasks/create",
        data={
            "title": "Digest",
            "goal": "summarise",
            "schedule": "09:00",
            "timezone": "Europe/Madrid",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    task = app_mod._SessionFactory().query(ScheduledTask).one()
    assert task.timezone == "Europe/Madrid"
    assert (
        task.next_run_at.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(task.timezone)).hour == 9
    )
    madrid_fixed_time_due = task.next_run_at

    r = c.post(
        f"/tasks/{task.id}/edit",
        data={
            "title": task.title,
            "goal": task.goal,
            "schedule": task.schedule,
            "timezone": "America/New_York",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    edited = app_mod._SessionFactory().query(ScheduledTask).one()
    assert edited.timezone == "America/New_York"
    assert edited.next_run_at != madrid_fixed_time_due
    new_york_fixed_time_due = edited.next_run_at
    fixed_time_local = new_york_fixed_time_due.replace(tzinfo=timezone.utc).astimezone(
        ZoneInfo(edited.timezone)
    )
    assert (fixed_time_local.hour, fixed_time_local.minute) == (9, 0)
    assert "America/New_York" in c.get("/tasks").text

    rejected_clear = c.post(
        f"/tasks/{edited.id}/edit",
        data={
            "title": edited.title,
            "goal": edited.goal,
            "schedule": edited.schedule,
            "timezone": "",
        },
        follow_redirects=False,
    )
    assert rejected_clear.status_code == 400
    after_rejected_clear = app_mod._SessionFactory().get(ScheduledTask, edited.id)
    assert after_rejected_clear.timezone == "America/New_York"
    assert after_rejected_clear.next_run_at == new_york_fixed_time_due

    unchanged_next_run = edited.next_run_at
    r = c.post(
        f"/tasks/{edited.id}/edit",
        data={"title": "Renamed", "goal": edited.goal, "schedule": edited.schedule},
        follow_redirects=False,
    )
    assert r.status_code == 302
    preserved = app_mod._SessionFactory().query(ScheduledTask).one()
    assert preserved.timezone == "America/New_York"
    assert preserved.next_run_at == unchanged_next_run

    legacy_next_run = datetime(2026, 8, 24, 9, 0)
    legacy = ScheduledTask(
        org_id=o.id,
        created_by=u.id,
        title="Legacy",
        goal="g",
        schedule=" 09:00  weekdays ",
        timezone="",
        status="pending",
        next_run_at=legacy_next_run,
    )
    legacy_session = app_mod._SessionFactory()
    legacy_session.add(legacy)
    legacy_session.commit()
    assert 'data-timezone=""' in c.get("/tasks").text
    r = c.post(
        f"/tasks/{legacy.id}/edit",
        data={
            "title": "Legacy renamed",
            "goal": legacy.goal,
            "schedule": legacy.schedule,
            "timezone": "",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    legacy_after = app_mod._SessionFactory().get(ScheduledTask, legacy.id)
    assert legacy_after.schedule == "09:00 weekdays"
    assert legacy_after.timezone == ""
    assert legacy_after.next_run_at == legacy_next_run

    rerun_cases = (
        ("Manual rerun", "09:00", "America/New_York", "run-now", {}),
        ("Queued rerun", "weekly", "Europe/Madrid", "queue", {"instruction": "again"}),
    )
    for title, edited_schedule, edited_timezone, trigger, trigger_data in rerun_cases:
        rerun = ScheduledTask(
            org_id=o.id,
            created_by=u.id,
            title=title,
            goal="g",
            schedule="09:00",
            timezone="Europe/Madrid",
            status="done",
            next_run_at=None,
        )
        legacy_session.add(rerun)
        legacy_session.commit()
        r = c.post(f"/tasks/{rerun.id}/{trigger}", data=trigger_data, follow_redirects=False)
        assert r.status_code == 302
        legacy_session.expire_all()
        rerun = legacy_session.get(ScheduledTask, rerun.id)
        rerun_due_at = rerun.next_run_at
        assert rerun.status == "pending"
        assert rerun_due_at is not None
        assert rerun_due_at <= datetime.now(timezone.utc).replace(tzinfo=None)

        r = c.post(
            f"/tasks/{rerun.id}/edit",
            data={
                "title": rerun.title,
                "goal": rerun.goal,
                "schedule": edited_schedule,
                "timezone": edited_timezone,
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        legacy_session.expire_all()
        edited_rerun = legacy_session.get(ScheduledTask, rerun.id)
        assert edited_rerun.status == "pending"
        assert edited_rerun.next_run_at == rerun_due_at

    calendar_due = (datetime.now(timezone.utc) + timedelta(days=8)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    for title, status, trigger, trigger_data in (
        ("Legacy manual calendar", "pending", "run-now", {}),
        ("Legacy queued calendar", "done", "queue", {"instruction": "again"}),
    ):
        legacy_calendar = ScheduledTask(
            org_id=o.id,
            created_by=u.id,
            title=title,
            goal="g",
            schedule="weekly",
            timezone="",
            schedule_anchor=None,
            status=status,
            next_run_at=calendar_due,
        )
        legacy_session.add(legacy_calendar)
        legacy_session.commit()

        r = c.post(
            f"/tasks/{legacy_calendar.id}/{trigger}",
            data=trigger_data,
            follow_redirects=False,
        )
        assert r.status_code == 302
        legacy_session.expire_all()
        legacy_calendar = legacy_session.get(ScheduledTask, legacy_calendar.id)
        expected_anchor = calendar_due.replace(tzinfo=None)
        rerun_due_at = legacy_calendar.next_run_at
        assert legacy_calendar.schedule_anchor == expected_anchor
        assert rerun_due_at is not None
        assert rerun_due_at <= datetime.now(timezone.utc).replace(tzinfo=None)

        r = c.post(
            f"/tasks/{legacy_calendar.id}/edit",
            data={
                "title": legacy_calendar.title,
                "goal": legacy_calendar.goal,
                "schedule": legacy_calendar.schedule,
                "timezone": "America/New_York",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        legacy_session.expire_all()
        edited_calendar = legacy_session.get(ScheduledTask, legacy_calendar.id)
        assert edited_calendar.timezone == "America/New_York"
        assert edited_calendar.schedule_anchor == expected_anchor
        assert edited_calendar.next_run_at == rerun_due_at

    cancelled_calendar = ScheduledTask(
        org_id=o.id,
        created_by=u.id,
        title="Cancelled legacy calendar",
        goal="g",
        schedule="weekly",
        timezone="",
        schedule_anchor=None,
        status="pending",
        next_run_at=calendar_due,
    )
    legacy_session.add(cancelled_calendar)
    legacy_session.commit()

    r = c.post(f"/tasks/{cancelled_calendar.id}/cancel", follow_redirects=False)
    assert r.status_code == 302
    legacy_session.expire_all()
    cancelled_calendar = legacy_session.get(ScheduledTask, cancelled_calendar.id)
    assert cancelled_calendar.status == "cancelled"
    assert cancelled_calendar.schedule_anchor == calendar_due.replace(tzinfo=None)
    assert cancelled_calendar.next_run_at is None

    r = c.post(f"/tasks/{cancelled_calendar.id}/run-now", follow_redirects=False)
    assert r.status_code == 302
    legacy_session.expire_all()
    cancelled_calendar = legacy_session.get(ScheduledTask, cancelled_calendar.id)
    assert cancelled_calendar.status == "pending"
    assert cancelled_calendar.schedule_anchor == calendar_due.replace(tzinfo=None)

    for action in ("run-now", "queue"):
        migrated_cancelled = ScheduledTask(
            org_id=o.id,
            created_by=u.id,
            title=f"Migrated cancelled {action}",
            goal="g",
            schedule="weekly",
            timezone="",
            schedule_anchor=None,
            status="cancelled",
            next_run_at=None,
        )
        legacy_session.add(migrated_cancelled)
        legacy_session.flush()
        legacy_session.add(
            TaskRun(
                org_id=o.id,
                task_id=migrated_cancelled.id,
                status="ok",
                scheduled_for=calendar_due,
            )
        )
        legacy_session.commit()
        r = c.post(
            f"/tasks/{migrated_cancelled.id}/edit",
            data={
                "title": migrated_cancelled.title,
                "goal": migrated_cancelled.goal,
                "schedule": migrated_cancelled.schedule,
                "timezone": "America/New_York",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        legacy_session.expire_all()
        migrated_cancelled = legacy_session.get(ScheduledTask, migrated_cancelled.id)
        assert migrated_cancelled.schedule_anchor == calendar_due.replace(tzinfo=None)
        if action == "queue":
            r = c.post(
                f"/tasks/{migrated_cancelled.id}/queue",
                data={"instruction": "rerun"},
                follow_redirects=False,
            )
        else:
            r = c.post(f"/tasks/{migrated_cancelled.id}/run-now", follow_redirects=False)
        assert r.status_code == 302
        legacy_session.expire_all()
        migrated_cancelled = legacy_session.get(ScheduledTask, migrated_cancelled.id)
        assert migrated_cancelled.schedule_anchor == calendar_due.replace(tzinfo=None)

    r = c.post(
        "/tasks/create",
        data={
            "title": "Weekday digest",
            "goal": "summarise",
            "schedule": "09:00 weekdays",
            "timezone": "Europe/Madrid",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    weekday_task = (
        legacy_session.query(ScheduledTask).filter(ScheduledTask.title == "Weekday digest").one()
    )
    madrid_weekday_due = weekday_task.next_run_at
    r = c.post(
        f"/tasks/{weekday_task.id}/edit",
        data={
            "title": weekday_task.title,
            "goal": weekday_task.goal,
            "schedule": weekday_task.schedule,
            "timezone": "America/New_York",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    legacy_session.expire_all()
    edited_weekday = legacy_session.get(ScheduledTask, weekday_task.id)
    weekday_local = edited_weekday.next_run_at.replace(tzinfo=timezone.utc).astimezone(
        ZoneInfo(edited_weekday.timezone)
    )
    assert edited_weekday.next_run_at != madrid_weekday_due
    assert (weekday_local.hour, weekday_local.minute) == (9, 0)
    assert weekday_local.weekday() < 5

    due_at = datetime(2026, 8, 24, 9, 30)
    for schedule in (" Hourly ", "ONCE"):
        unchanged = ScheduledTask(
            org_id=o.id,
            created_by=u.id,
            title=f"Unchanged {schedule}",
            goal="g",
            schedule=schedule,
            timezone="Europe/Madrid",
            status="pending",
            next_run_at=due_at,
        )
        legacy_session.add(unchanged)
        legacy_session.commit()
        r = c.post(
            f"/tasks/{unchanged.id}/edit",
            data={
                "title": unchanged.title,
                "goal": unchanged.goal,
                "schedule": schedule,
                "timezone": "America/New_York",
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        legacy_session.expire_all()
        unchanged_after = legacy_session.get(ScheduledTask, unchanged.id)
        assert unchanged_after.schedule == schedule.strip().lower()
        assert unchanged_after.timezone == "America/New_York"
        assert unchanged_after.next_run_at == due_at

    madrid_now = datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Madrid"))
    madrid_due = (madrid_now + timedelta(days=(7 - madrid_now.weekday()) % 7 or 7)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    weekly_anchor = madrid_due.replace(tzinfo=None)
    legacy_weekly = ScheduledTask(
        org_id=o.id,
        created_by=u.id,
        title="Legacy weekly",
        goal="g",
        schedule=" Weekly ",
        timezone="Europe/Madrid",
        schedule_anchor=weekly_anchor,
        status="pending",
        next_run_at=madrid_due.astimezone(timezone.utc).replace(tzinfo=None),
    )
    legacy_session.add(legacy_weekly)
    legacy_session.commit()
    r = c.post(
        f"/tasks/{legacy_weekly.id}/edit",
        data={
            "title": legacy_weekly.title,
            "goal": legacy_weekly.goal,
            "schedule": legacy_weekly.schedule,
            "timezone": "America/New_York",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    legacy_session.expire_all()
    legacy_weekly = legacy_session.get(ScheduledTask, legacy_weekly.id)
    weekly_local = legacy_weekly.next_run_at.replace(tzinfo=timezone.utc).astimezone(
        ZoneInfo(legacy_weekly.timezone)
    )
    assert legacy_weekly.schedule == "weekly"
    assert legacy_weekly.schedule_anchor == weekly_anchor
    assert (weekly_local.weekday(), weekly_local.hour, weekly_local.minute) == (0, 9, 0)

    active_legacy = ScheduledTask(
        org_id=o.id,
        created_by=u.id,
        title="Active legacy daily",
        goal="g",
        schedule="daily",
        timezone="",
        status="running",
        next_run_at=None,
    )
    legacy_session.add(active_legacy)
    legacy_session.flush()
    scheduled_for = datetime(2026, 8, 24, 9, 0)
    legacy_session.add(
        TaskRun(
            org_id=o.id,
            task_id=active_legacy.id,
            status="running",
            scheduled_for=scheduled_for,
            started_at=scheduled_for,
        )
    )
    legacy_session.commit()
    r = c.post(
        f"/tasks/{active_legacy.id}/edit",
        data={
            "title": active_legacy.title,
            "goal": active_legacy.goal,
            "schedule": active_legacy.schedule,
            "timezone": "America/Los_Angeles",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302
    legacy_session.expire_all()
    active_legacy = legacy_session.get(ScheduledTask, active_legacy.id)
    assert active_legacy.timezone == "America/Los_Angeles"
    assert active_legacy.schedule_anchor == scheduled_for
    assert active_legacy.next_run_at is None

    running = ScheduledTask(
        org_id=o.id,
        created_by=u.id,
        title="Running fixed task",
        goal="g",
        schedule="09:00",
        timezone="Europe/Madrid",
        status="running",
        next_run_at=None,
    )
    legacy_session.add(running)
    legacy_session.flush()
    running_occurrence = TaskOccurrence(
        org_id=o.id,
        task_id=running.id,
        kind="scheduled",
        status="claimed",
        due_at=scheduled_for,
        inputs="[]",
    )
    legacy_session.add(running_occurrence)
    legacy_session.flush()
    running_run = TaskRun(
        org_id=o.id,
        task_id=running.id,
        occurrence_id=running_occurrence.id,
        status="running",
        scheduled_for=scheduled_for,
    )
    legacy_session.add(running_run)
    legacy_session.flush()
    running_occurrence.claimed_run_id = running_run.id
    legacy_session.commit()
    for new_timezone in ("America/New_York", "America/Los_Angeles"):
        r = c.post(
            f"/tasks/{running.id}/edit",
            data={
                "title": running.title,
                "goal": running.goal,
                "schedule": running.schedule,
                "timezone": new_timezone,
            },
            follow_redirects=False,
        )
        assert r.status_code == 302
        legacy_session.expire_all()
        running = legacy_session.get(ScheduledTask, running.id)
        assert running.timezone == new_timezone
        assert running.next_run_at is None


def test_tasks_page_renders_attachment_pickers(tmp_path):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="u@a.com", role="member", active=True)
    s.add(u)
    s.flush()  # populate u.id before the snippet references it
    s.add(MCPServer(org_id=o.id, name="Files", catalog_id="filesystem", status="approved"))
    s.add(Snippet(org_id=o.id, user_id=u.id, content="body", question="Q3 numbers"))
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "member"))

    body = c.get("/tasks").text
    assert "Attach context" in body
    assert "Files from a connected service" in body  # doc-source picker rendered
    assert 'name="context_files"' in body and 'name="context_snippets"' in body
    assert "Q3 numbers" in body  # the snippet shows as an option
