"""Prompt queue while busy - the server side (task instruction queue).

The chat-side queue is client-only (chat.html JS) and not unit-tested here; this
covers the task instruction queue: draining queued follow-ups into the goal and the
queue endpoint (append + re-arm a finished task).
"""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db, scheduler
from anthill.web.db import Organization, ScheduledTask, TaskOccurrence, TaskRun, User


def test_effective_goal_reads_only_the_claimed_occurrence_inputs():
    from anthill.web.scheduler import _effective_goal

    t = ScheduledTask(
        title="x",
        goal="Summarize the inbox",
        queued_inputs=json.dumps(["also CC me", "use bullet points"]),
    )
    g = _effective_goal(t)
    assert "Summarize the inbox" in g and "also CC me" in g and "use bullet points" in g
    assert json.loads(t.queued_inputs) == ["also CC me", "use bullet points"]

    t._claimed_queued_inputs = "[]"
    g2 = _effective_goal(t)  # the next occurrence owns no inputs
    assert g2 == "Summarize the inbox" and "queued" not in g2.lower()


def _client(tmp_path):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod

    eng = create_engine(
        f"sqlite:///{tmp_path / 'app.db'}", connect_args={"check_same_thread": False}
    )
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    user = User(org_id=org.id, email="u@acme.com", role="member", active=True)
    s.add(user)
    s.flush()
    task = ScheduledTask(
        org_id=org.id,
        created_by=user.id,
        title="Daily digest",
        goal="Summarize unread email",
        schedule="daily",
        status="done",
    )
    s.add(task)
    s.commit()
    from anthill.web.crypto import make_token

    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(user.id, org.id, "member"))
    return c, app_mod, task.id


def test_create_with_run_now_keeps_independent_future_cadence(tmp_path):
    c, app_mod, _tid = _client(tmp_path)
    try:
        response = c.post(
            "/tasks/create",
            data={
                "title": "Run now recurring",
                "goal": "g",
                "schedule": "daily",
                "timezone": "UTC",
                "run_now": "true",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        session = app_mod._SessionFactory()
        task = session.query(ScheduledTask).filter_by(title="Run now recurring").one()
        occurrences = (
            session.query(TaskOccurrence)
            .filter(
                TaskOccurrence.task_id == task.id,
                TaskOccurrence.status == "pending",
            )
            .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
            .all()
        )
        assert [occurrence.kind for occurrence in occurrences] == ["manual", "scheduled"]
        assert occurrences[0].due_at < occurrences[1].due_at
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_queue_route_appends_and_rearms(tmp_path):
    c, app_mod, tid = _client(tmp_path)
    try:
        r = c.post(
            f"/tasks/{tid}/queue", data={"instruction": "include a TL;DR"}, follow_redirects=False
        )
        assert r.status_code == 302
        s = app_mod._SessionFactory()
        t = s.get(ScheduledTask, tid)
        assert json.loads(t.queued_inputs) == ["include a TL;DR"]
        assert t.status == "pending" and t.next_run_at is not None  # finished task re-armed

        c.post(f"/tasks/{tid}/queue", data={"instruction": "and keep it short"})
        s = app_mod._SessionFactory()
        assert json.loads(s.get(ScheduledTask, tid).queued_inputs) == [
            "include a TL;DR",
            "and keep it short",
        ]
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_queue_route_returns_retryable_503_after_persistent_lock(tmp_path, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from anthill.web import task_occurrences

    c, app_mod, tid = _client(tmp_path)
    attempts = []
    try:

        def locked(*_args, **_kwargs):
            attempts.append(1)
            raise OperationalError(
                "UPDATE task_occurrences", {}, RuntimeError("database is locked")
            )

        monkeypatch.setattr(task_occurrences, "queue_input", locked)
        response = c.post(
            f"/tasks/{tid}/queue",
            data={"instruction": "include a TL;DR"},
            follow_redirects=False,
        )

        assert response.status_code == 503
        assert response.headers["Retry-After"] == "1"
        assert response.json()["detail"] == "Task queue is busy; retry shortly"
        assert len(attempts) == app_mod._TASK_QUEUE_RETRY_LIMIT
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_edit_route_returns_retryable_503_after_persistent_lock(tmp_path, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from anthill.web import task_occurrences

    c, app_mod, tid = _client(tmp_path)
    attempts = []
    try:

        def locked(*_args, **_kwargs):
            attempts.append(1)
            raise OperationalError(
                "UPDATE task_occurrences", {}, RuntimeError("database is locked")
            )

        monkeypatch.setattr(task_occurrences, "replace_scheduled", locked)
        response = c.post(
            f"/tasks/{tid}/edit",
            data={
                "title": "Edited",
                "goal": "Edited goal",
                "schedule": "hourly",
                "timezone": "UTC",
            },
            follow_redirects=False,
        )

        assert response.status_code == 503
        assert response.headers["Retry-After"] == "1"
        assert response.json()["detail"] == "Task edit is busy; retry shortly"
        assert len(attempts) == app_mod._TASK_QUEUE_RETRY_LIMIT
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_queue_keeps_due_work_immediate_instead_of_appending_to_future_cadence(tmp_path):
    c, app_mod, tid = _client(tmp_path)
    try:
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        now = datetime.now(timezone.utc)
        task.status = "pending"
        session.add_all(
            [
                TaskOccurrence(
                    org_id=task.org_id,
                    task_id=task.id,
                    kind="manual",
                    status="pending",
                    due_at=now - timedelta(minutes=1),
                    inputs="[]",
                ),
                TaskOccurrence(
                    org_id=task.org_id,
                    task_id=task.id,
                    kind="scheduled",
                    status="pending",
                    due_at=now + timedelta(days=1),
                    inputs="[]",
                ),
            ]
        )
        session.commit()

        response = c.post(
            f"/tasks/{tid}/queue",
            data={"instruction": "new immediate work"},
            follow_redirects=False,
        )

        assert response.status_code == 302
        occurrences = (
            app_mod._SessionFactory()
            .query(TaskOccurrence)
            .filter(TaskOccurrence.task_id == tid, TaskOccurrence.status == "pending")
            .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
            .all()
        )
        assert [(row.kind, json.loads(row.inputs)) for row in occurrences] == [
            ("manual", []),
            ("queued", ["new immediate work"]),
            ("scheduled", []),
        ]
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_queue_orders_new_input_after_interrupted_work_before_future_cadence(tmp_path):
    c, app_mod, tid = _client(tmp_path)
    try:
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        now = datetime.now(timezone.utc)
        interrupted = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind="scheduled",
            status="interrupted",
            due_at=now - timedelta(hours=1),
            inputs=json.dumps(["retry input"]),
        )
        cadence = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind="scheduled",
            status="pending",
            due_at=now + timedelta(days=1),
            inputs="[]",
        )
        task.schedule = "daily"
        task.status = "pending"
        session.add_all([interrupted, cadence])
        session.commit()

        response = c.post(
            f"/tasks/{tid}/queue",
            data={"instruction": "new queued input"},
            follow_redirects=False,
        )

        assert response.status_code == 302
        occurrences = (
            app_mod._SessionFactory()
            .query(TaskOccurrence)
            .filter(
                TaskOccurrence.task_id == tid,
                TaskOccurrence.status.in_(("pending", "interrupted")),
            )
            .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
            .all()
        )
        assert [(row.kind, row.status, json.loads(row.inputs)) for row in occurrences] == [
            ("scheduled", "interrupted", ["retry input"]),
            ("queued", "pending", ["new queued input"]),
            ("scheduled", "pending", []),
        ]
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_edit_refreshes_cancelled_status_before_replacing_cadence(tmp_path):
    from sqlalchemy import event

    from anthill.web import task_occurrences

    c, app_mod, tid = _client(tmp_path)
    engine = app_mod._engine
    try:
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        due_at = datetime.now(timezone.utc) + timedelta(days=1)
        task.schedule = "daily"
        task.timezone = "UTC"
        task.status = "pending"
        task.next_run_at = due_at
        session.add(
            TaskOccurrence(
                org_id=task.org_id,
                task_id=task.id,
                kind="scheduled",
                status="pending",
                due_at=due_at,
                inputs="[]",
            )
        )
        session.commit()
        raced = False

        def cancel_after_task_load(_conn, _cursor, statement, _parameters, _context, _many):
            nonlocal raced
            if raced or not statement.lstrip().startswith("SELECT scheduled_tasks.id AS"):
                return
            raced = True
            other = app_mod._SessionFactory()
            try:
                task_occurrences.cancel(other, other.get(ScheduledTask, tid))
                other.commit()
            finally:
                other.close()

        event.listen(engine, "after_cursor_execute", cancel_after_task_load)
        try:
            response = c.post(
                f"/tasks/{tid}/edit",
                data={
                    "title": "Edited",
                    "goal": "Edited goal",
                    "schedule": "weekly",
                    "timezone": "UTC",
                },
                follow_redirects=False,
            )
        finally:
            event.remove(engine, "after_cursor_execute", cancel_after_task_load)

        assert response.status_code == 302 and raced
        saved = app_mod._SessionFactory()
        task = saved.get(ScheduledTask, tid)
        cadence = (
            saved.query(TaskOccurrence)
            .filter(
                TaskOccurrence.task_id == tid,
                TaskOccurrence.kind == "scheduled",
                TaskOccurrence.status.in_(("pending", "paused")),
            )
            .one()
        )
        assert task.status == "cancelled"
        assert task.next_run_at is None
        assert cadence.status == "paused"
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_cadence_review_is_visible_and_cleared_by_schedule_review(tmp_path):
    c, app_mod, tid = _client(tmp_path)
    try:
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        task.cadence_needs_review = True
        task.cadence_review_reason = "Verify the migrated weekday and time"
        session.commit()

        page = c.get("/tasks")
        assert "schedule needs review" in page.text
        assert "Verify the migrated weekday and time" in page.text

        response = c.post(
            f"/tasks/{tid}/edit",
            data={
                "title": task.title,
                "goal": task.goal,
                "schedule": "weekly",
                "timezone": "UTC",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        saved_session = app_mod._SessionFactory()
        saved = saved_session.get(ScheduledTask, tid)
        assert saved.cadence_needs_review is False
        assert saved.cadence_review_reason == ""

        saved.cadence_needs_review = True
        saved.cadence_review_reason = "Review again"
        saved_session.commit()
        response = c.post(f"/tasks/{tid}/review-cadence", follow_redirects=False)
        assert response.status_code == 302
        reviewed = app_mod._SessionFactory().get(ScheduledTask, tid)
        assert reviewed.cadence_needs_review is False
        assert reviewed.cadence_review_reason == ""
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_edit_to_once_during_manual_run_removes_future_cadence(tmp_path):
    c, app_mod, tid = _client(tmp_path)
    try:
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        task.status = "pending"
        task.timezone = "UTC"
        task.next_run_at = datetime.now(timezone.utc) + timedelta(days=1)
        session.add(
            TaskOccurrence(
                org_id=task.org_id,
                task_id=tid,
                kind="scheduled",
                status="pending",
                due_at=task.next_run_at,
                inputs="[]",
            )
        )
        session.commit()

        assert c.post(f"/tasks/{tid}/run-now", follow_redirects=False).status_code == 302
        response = c.post(
            f"/tasks/{tid}/edit",
            data={
                "title": task.title,
                "goal": task.goal,
                "schedule": "once",
                "timezone": "UTC",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        remaining = (
            app_mod._SessionFactory()
            .query(TaskOccurrence)
            .filter(
                TaskOccurrence.task_id == tid,
                TaskOccurrence.status.in_(("pending", "interrupted", "paused", "claimed")),
            )
            .all()
        )
        assert [occurrence.kind for occurrence in remaining] == ["manual"]
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_edit_cancelled_task_to_once_removes_paused_cadence(tmp_path):
    c, app_mod, tid = _client(tmp_path)
    try:
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        task.status = "pending"
        task.timezone = "UTC"
        task.next_run_at = datetime.now(timezone.utc) + timedelta(days=1)
        session.add(
            TaskOccurrence(
                org_id=task.org_id,
                task_id=tid,
                kind="scheduled",
                status="pending",
                due_at=task.next_run_at,
                inputs="[]",
            )
        )
        session.commit()

        assert c.post(f"/tasks/{tid}/cancel", follow_redirects=False).status_code == 302
        response = c.post(
            f"/tasks/{tid}/edit",
            data={
                "title": task.title,
                "goal": task.goal,
                "schedule": "once",
                "timezone": "UTC",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert c.post(f"/tasks/{tid}/run-now", follow_redirects=False).status_code == 302
        remaining = (
            app_mod._SessionFactory()
            .query(TaskOccurrence)
            .filter(
                TaskOccurrence.task_id == tid,
                TaskOccurrence.status.in_(("pending", "interrupted", "paused", "claimed")),
            )
            .all()
        )
        assert [occurrence.kind for occurrence in remaining] == ["manual"]
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


@pytest.mark.parametrize(("new_schedule", "cadence_count"), [("once", 0), ("hourly", 1)])
def test_edit_preserves_interrupted_occurrence(tmp_path, new_schedule, cadence_count):
    c, app_mod, tid = _client(tmp_path)
    try:
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        due_at = datetime.now(timezone.utc) - timedelta(hours=1)
        task.schedule = "daily"
        task.timezone = "UTC"
        task.status = "pending"
        task.next_run_at = due_at
        task.interrupted_run_at = due_at
        task.interrupted_inputs = "[]"
        occurrence = TaskOccurrence(
            org_id=task.org_id,
            task_id=tid,
            kind="scheduled",
            status="interrupted",
            due_at=due_at,
            inputs="[]",
            cadence_deferred=True,
        )
        session.add(occurrence)
        session.commit()
        occurrence_id = occurrence.id

        response = c.post(
            f"/tasks/{tid}/edit",
            data={
                "title": task.title,
                "goal": task.goal,
                "schedule": new_schedule,
                "timezone": "UTC",
            },
            follow_redirects=False,
        )

        assert response.status_code == 302
        saved_session = app_mod._SessionFactory()
        saved = saved_session.get(TaskOccurrence, occurrence_id)
        assert (
            saved.kind,
            saved.status,
            saved.due_at,
            saved.inputs,
            saved.cadence_deferred,
        ) == (
            "scheduled",
            "interrupted",
            due_at.replace(tzinfo=None),
            "[]",
            False,
        )
        cadence = (
            saved_session.query(TaskOccurrence)
            .filter(
                TaskOccurrence.task_id == tid,
                TaskOccurrence.id != occurrence_id,
                TaskOccurrence.kind == "scheduled",
                TaskOccurrence.status.in_(("pending", "paused")),
            )
            .all()
        )
        assert len(cadence) == cadence_count
        assert saved_session.get(ScheduledTask, tid).interrupted_run_at == due_at.replace(
            tzinfo=None
        )
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


@pytest.mark.parametrize(("new_schedule", "cadence_count"), [("once", 0), ("hourly", 1)])
def test_edit_preserves_scheduled_occurrence_inputs(tmp_path, new_schedule, cadence_count):
    c, app_mod, tid = _client(tmp_path)
    try:
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        due_at = datetime.now(timezone.utc) + timedelta(days=1)
        task.schedule = "daily"
        task.timezone = "UTC"
        task.status = "pending"
        task.next_run_at = due_at
        occurrence = TaskOccurrence(
            org_id=task.org_id,
            task_id=tid,
            kind="scheduled",
            status="pending",
            due_at=due_at,
            inputs=json.dumps(["keep this input"]),
        )
        session.add(occurrence)
        session.commit()
        occurrence_id = occurrence.id

        response = c.post(
            f"/tasks/{tid}/edit",
            data={
                "title": task.title,
                "goal": task.goal,
                "schedule": new_schedule,
                "timezone": "UTC",
            },
            follow_redirects=False,
        )

        assert response.status_code == 302
        saved_session = app_mod._SessionFactory()
        saved = saved_session.get(TaskOccurrence, occurrence_id)
        assert saved.kind == "queued"
        assert saved.status == "pending"
        assert saved.due_at == due_at.replace(tzinfo=None)
        assert json.loads(saved.inputs) == ["keep this input"]
        cadence = (
            saved_session.query(TaskOccurrence)
            .filter(
                TaskOccurrence.task_id == tid,
                TaskOccurrence.kind == "scheduled",
                TaskOccurrence.status.in_(("pending", "interrupted", "paused")),
            )
            .all()
        )
        assert len(cadence) == cadence_count
        assert all(json.loads(row.inputs) == [] for row in cadence)
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_edit_replaces_an_unclaimed_overdue_cadence(tmp_path):
    c, app_mod, tid = _client(tmp_path)
    try:
        before = datetime.now(timezone.utc)
        old_due = before - timedelta(hours=1)
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        task.schedule = "daily"
        task.timezone = "UTC"
        task.status = "pending"
        task.next_run_at = old_due
        occurrence = TaskOccurrence(
            org_id=task.org_id,
            task_id=tid,
            kind="scheduled",
            status="pending",
            due_at=old_due,
            inputs="[]",
        )
        session.add(occurrence)
        session.commit()
        occurrence_id = occurrence.id

        response = c.post(
            f"/tasks/{tid}/edit",
            data={
                "title": task.title,
                "goal": task.goal,
                "schedule": "hourly",
                "timezone": "UTC",
            },
            follow_redirects=False,
        )

        assert response.status_code == 302
        saved = app_mod._SessionFactory().get(TaskOccurrence, occurrence_id)
        assert saved.kind == "scheduled" and saved.status == "pending"
        assert saved.due_at > before.replace(tzinfo=None)
        assert saved.due_at != old_due.replace(tzinfo=None)
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_schedule_edit_defers_for_the_claimed_scheduled_occurrence(tmp_path):
    c, app_mod, tid = _client(tmp_path)
    try:
        now = datetime.now(timezone.utc)
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        task.schedule = "daily"
        task.timezone = "UTC"
        task.status = "running"
        task.next_run_at = None
        occurrence = TaskOccurrence(
            org_id=task.org_id,
            task_id=tid,
            kind="scheduled",
            status="claimed",
            due_at=now - timedelta(minutes=1),
            inputs="[]",
        )
        session.add(occurrence)
        session.flush()
        run = TaskRun(
            org_id=task.org_id,
            task_id=tid,
            occurrence_id=occurrence.id,
            status="running",
            scheduled_for=occurrence.due_at,
        )
        session.add(run)
        session.flush()
        occurrence.claimed_run_id = run.id
        session.commit()

        response = c.post(
            f"/tasks/{tid}/edit",
            data={
                "title": task.title,
                "goal": task.goal,
                "schedule": "hourly",
                "timezone": "UTC",
            },
            follow_redirects=False,
        )

        assert response.status_code == 302
        rows = (
            app_mod._SessionFactory()
            .query(TaskOccurrence)
            .filter(
                TaskOccurrence.task_id == tid,
                TaskOccurrence.kind == "scheduled",
                TaskOccurrence.status.in_(("pending", "claimed", "interrupted", "paused")),
            )
            .all()
        )
        assert [(row.id, row.status) for row in rows] == [(occurrence.id, "claimed")]
        assert rows[0].cadence_deferred is True
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


@pytest.mark.parametrize(("next_due", "cadence_count"), [(False, 0), (True, 1)])
def test_scheduled_finalization_reconciles_deferred_cadence(tmp_path, next_due, cadence_count):
    from anthill.web import task_occurrences

    _c, app_mod, tid = _client(tmp_path)
    try:
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        now = datetime.now(timezone.utc)
        task.schedule = "hourly" if next_due else "once"
        task.status = "running"
        claimed = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind="scheduled",
            status="claimed",
            due_at=now - timedelta(hours=1),
            inputs="[]",
            cadence_deferred=True,
        )
        cadence = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind="scheduled",
            status="pending",
            due_at=now + timedelta(hours=1),
            inputs="[]",
        )
        extra = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind="scheduled",
            status="paused",
            due_at=now + timedelta(hours=2),
            inputs="[]",
        )
        owned = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind="scheduled",
            status="pending",
            due_at=now + timedelta(hours=3),
            inputs=json.dumps(["keep owned input"]),
        )
        session.add_all([claimed, cadence, extra, owned])
        session.flush()
        run = TaskRun(
            org_id=task.org_id,
            task_id=task.id,
            occurrence_id=claimed.id,
            status="running",
            scheduled_for=claimed.due_at,
        )
        session.add(run)
        session.flush()
        claimed.claimed_run_id = run.id
        session.commit()
        replacement = now + timedelta(hours=4) if next_due else None

        assert task_occurrences.finalize(
            session,
            claimed.id,
            run.id,
            outcome="done",
            next_due_at=replacement,
        )
        session.commit()

        rows = (
            session.query(TaskOccurrence)
            .filter(
                TaskOccurrence.task_id == tid,
                TaskOccurrence.status.in_(("pending", "paused", "interrupted", "claimed")),
            )
            .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
            .all()
        )
        preserved = session.get(TaskOccurrence, owned.id)
        assert (preserved.kind, preserved.status, json.loads(preserved.inputs)) == (
            "queued",
            "pending",
            ["keep owned input"],
        )
        scheduled = [row for row in rows if row.kind == "scheduled"]
        assert len(scheduled) == cadence_count
        if replacement is not None:
            assert scheduled[0].due_at == replacement.replace(tzinfo=None)
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_interrupted_retry_keeps_cadence_installed_by_later_edit(tmp_path):
    from anthill.web import task_occurrences

    _c, app_mod, tid = _client(tmp_path)
    try:
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        now = datetime.now(timezone.utc)
        interrupted = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind="scheduled",
            status="interrupted",
            due_at=now - timedelta(hours=2),
            inputs="[]",
        )
        edited_cadence = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind="scheduled",
            status="pending",
            due_at=now + timedelta(minutes=30),
            inputs="[]",
        )
        task.schedule = "hourly"
        task.status = "pending"
        session.add_all([interrupted, edited_cadence])
        session.commit()
        edited_due = edited_cadence.due_at

        claimed = task_occurrences.claim(session, task.id, now)
        assert claimed.occurrence.id == interrupted.id
        assert task_occurrences.finalize(
            session,
            interrupted.id,
            claimed.run.id,
            outcome="done",
            next_due_at=now + timedelta(hours=1),
        )
        session.commit()

        saved = session.get(TaskOccurrence, edited_cadence.id)
        assert saved.kind == "scheduled" and saved.status == "pending"
        assert saved.due_at == edited_due
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


@pytest.mark.parametrize("action", ["run-now", "queue", "cancel"])
def test_task_action_does_not_overwrite_concurrent_schedule_edit(tmp_path, action):
    from sqlalchemy import event

    c, app_mod, tid = _client(tmp_path)
    engine = app_mod._engine
    try:
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        now = datetime.now(timezone.utc)
        cadence = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind="scheduled",
            status="pending",
            due_at=now + timedelta(days=1),
            inputs="[]",
        )
        task.schedule = "daily"
        task.timezone = "UTC"
        task.schedule_anchor = None
        task.status = "pending"
        session.add(cadence)
        session.commit()
        cadence_id = cadence.id
        edited_anchor = datetime(2030, 6, 7, 16, 45)
        edited_due = now + timedelta(days=4)
        raced = False

        def edit_before_action_cas(_conn, _cursor, statement, _parameters, _context, _many):
            nonlocal raced
            if raced or not statement.lstrip().startswith("UPDATE scheduled_tasks SET"):
                return
            raced = True
            other = app_mod._SessionFactory()
            try:
                other.query(ScheduledTask).filter(ScheduledTask.id == tid).update(
                    {
                        ScheduledTask.schedule: "weekly",
                        ScheduledTask.timezone: "Europe/Madrid",
                        ScheduledTask.schedule_anchor: edited_anchor,
                    },
                    synchronize_session=False,
                )
                other.query(TaskOccurrence).filter(TaskOccurrence.id == cadence_id).update(
                    {TaskOccurrence.due_at: edited_due}, synchronize_session=False
                )
                other.commit()
            finally:
                other.close()

        event.listen(engine, "before_cursor_execute", edit_before_action_cas)
        try:
            response = (
                c.post(
                    f"/tasks/{tid}/queue",
                    data={"instruction": "new work"},
                    follow_redirects=False,
                )
                if action == "queue"
                else c.post(f"/tasks/{tid}/{action}", follow_redirects=False)
            )
        finally:
            event.remove(engine, "before_cursor_execute", edit_before_action_cas)

        assert response.status_code == 302 and raced
        saved = app_mod._SessionFactory()
        task = saved.get(ScheduledTask, tid)
        cadence = saved.get(TaskOccurrence, cadence_id)
        assert (task.schedule, task.timezone, task.schedule_anchor) == (
            "weekly",
            "Europe/Madrid",
            edited_anchor,
        )
        assert cadence.due_at == edited_due.replace(tzinfo=None)
        assert cadence.status == ("paused" if action == "cancel" else "pending")
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_cancel_uses_timezone_committed_by_concurrent_edit(tmp_path):
    from sqlalchemy import event

    c, app_mod, tid = _client(tmp_path)
    engine = app_mod._engine
    try:
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        now = datetime.now(timezone.utc)
        active_due = now - timedelta(hours=2)
        edited_anchor = active_due.replace(tzinfo=None)
        active = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind="scheduled",
            status="claimed",
            due_at=active_due,
            inputs="[]",
        )
        task.schedule = "daily"
        task.timezone = "UTC"
        task.schedule_anchor = None
        task.status = "running"
        session.add(active)
        session.flush()
        run = TaskRun(
            org_id=task.org_id,
            task_id=task.id,
            occurrence_id=active.id,
            status="running",
            scheduled_for=active_due,
            started_at=now,
        )
        session.add(run)
        session.flush()
        active.claimed_run_id = run.id
        session.commit()
        raced = False

        def edit_timezone_before_cancel_cas(
            _conn, _cursor, statement, _parameters, _context, _many
        ):
            nonlocal raced
            if raced or not statement.lstrip().startswith("UPDATE scheduled_tasks SET"):
                return
            raced = True
            other = app_mod._SessionFactory()
            try:
                other.query(ScheduledTask).filter(ScheduledTask.id == tid).update(
                    {
                        ScheduledTask.timezone: "America/New_York",
                        ScheduledTask.schedule_anchor: edited_anchor,
                    },
                    synchronize_session=False,
                )
                other.commit()
            finally:
                other.close()

        event.listen(engine, "before_cursor_execute", edit_timezone_before_cancel_cas)
        try:
            response = c.post(f"/tasks/{tid}/cancel", follow_redirects=False)
        finally:
            event.remove(engine, "before_cursor_execute", edit_timezone_before_cancel_cas)

        assert response.status_code == 302 and raced
        expected_due = scheduler._next_run(
            "daily",
            from_dt=active_due,
            timezone_name="America/New_York",
            schedule_anchor=edited_anchor,
        )
        while expected_due <= now:
            expected_due = scheduler._next_run(
                "daily",
                from_dt=expected_due,
                timezone_name="America/New_York",
                schedule_anchor=edited_anchor,
            )
        saved = app_mod._SessionFactory()
        task = saved.get(ScheduledTask, tid)
        cadence = (
            saved.query(TaskOccurrence)
            .filter(
                TaskOccurrence.task_id == tid,
                TaskOccurrence.kind == "scheduled",
                TaskOccurrence.status == "paused",
            )
            .one()
        )
        assert task.timezone == "America/New_York"
        assert cadence.due_at == expected_due.replace(tzinfo=None)
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_run_now_retries_when_anchor_source_occurrence_moves(tmp_path):
    from sqlalchemy import event

    c, app_mod, tid = _client(tmp_path)
    engine = app_mod._engine
    try:
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        now = datetime.now(timezone.utc)
        cadence = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind="scheduled",
            status="pending",
            due_at=(now + timedelta(days=1)).replace(hour=9),
            inputs="[]",
        )
        task.schedule = "daily"
        task.timezone = "UTC"
        task.schedule_anchor = None
        task.status = "pending"
        session.add(cadence)
        session.commit()
        edited_due = cadence.due_at.replace(hour=16)
        raced = False

        def move_occurrence_before_cas(_conn, _cursor, statement, _parameters, _context, _many):
            nonlocal raced
            if raced or not statement.lstrip().startswith("UPDATE scheduled_tasks SET"):
                return
            raced = True
            other = app_mod._SessionFactory()
            try:
                other.query(TaskOccurrence).filter(TaskOccurrence.id == cadence.id).update(
                    {TaskOccurrence.due_at: edited_due}, synchronize_session=False
                )
                other.commit()
            finally:
                other.close()

        event.listen(engine, "before_cursor_execute", move_occurrence_before_cas)
        try:
            response = c.post(f"/tasks/{tid}/run-now", follow_redirects=False)
        finally:
            event.remove(engine, "before_cursor_execute", move_occurrence_before_cas)

        assert response.status_code == 302 and raced
        saved = app_mod._SessionFactory()
        task = saved.get(ScheduledTask, tid)
        assert task.schedule_anchor == edited_due
        assert saved.get(TaskOccurrence, cadence.id).due_at == edited_due
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_queue_serializes_after_concurrent_cancellation(tmp_path):
    from sqlalchemy import event

    from anthill.web import task_occurrences

    c, app_mod, tid = _client(tmp_path)
    engine = app_mod._engine
    try:
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        due_at = datetime.now(timezone.utc) + timedelta(days=1)
        task.status = "pending"
        task.next_run_at = due_at
        task.occurrences_materialized = True
        session.add(
            TaskOccurrence(
                org_id=task.org_id,
                task_id=tid,
                kind="scheduled",
                status="pending",
                due_at=due_at,
                inputs="[]",
            )
        )
        session.commit()
        raced = False

        def cancel_after_status_read(_conn, _cursor, statement, _parameters, _context, _many):
            nonlocal raced
            if raced or not statement.lstrip().startswith("SELECT scheduled_tasks.status"):
                return
            raced = True
            other = app_mod._SessionFactory()
            try:
                task_occurrences.cancel(other, other.get(ScheduledTask, tid))
                other.commit()
            finally:
                other.close()

        event.listen(engine, "after_cursor_execute", cancel_after_status_read)
        try:
            response = c.post(
                f"/tasks/{tid}/queue",
                data={"instruction": "new work"},
                follow_redirects=False,
            )
        finally:
            event.remove(engine, "after_cursor_execute", cancel_after_status_read)

        assert response.status_code == 302 and raced
        saved = app_mod._SessionFactory()
        task = saved.get(ScheduledTask, tid)
        occurrences = (
            saved.query(TaskOccurrence)
            .filter(
                TaskOccurrence.task_id == tid,
                TaskOccurrence.status.in_(("pending", "interrupted", "paused", "claimed")),
            )
            .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
            .all()
        )
        assert task.status == "pending"
        state = [(row.kind, json.loads(row.inputs)) for row in occurrences]
        assert state in (
            [("scheduled", ["new work"])],  # Queue serialized before cancellation.
            [("queued", ["new work"]), ("scheduled", [])],  # Cancellation serialized first.
        )
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_run_now_serializes_after_concurrent_cancellation(tmp_path):
    from sqlalchemy import event

    from anthill.web import task_occurrences

    c, app_mod, tid = _client(tmp_path)
    engine = app_mod._engine
    try:
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        due_at = datetime.now(timezone.utc) + timedelta(days=1)
        task.status = "pending"
        task.next_run_at = due_at
        task.occurrences_materialized = True
        session.add(
            TaskOccurrence(
                org_id=task.org_id,
                task_id=tid,
                kind="scheduled",
                status="pending",
                due_at=due_at,
                inputs="[]",
            )
        )
        session.commit()
        raced = False

        def cancel_after_status_read(_conn, _cursor, statement, _parameters, _context, _many):
            nonlocal raced
            if raced or not statement.lstrip().startswith("SELECT scheduled_tasks.status"):
                return
            raced = True
            other = app_mod._SessionFactory()
            try:
                task_occurrences.cancel(other, other.get(ScheduledTask, tid))
                other.commit()
            finally:
                other.close()

        event.listen(engine, "after_cursor_execute", cancel_after_status_read)
        try:
            response = c.post(f"/tasks/{tid}/run-now", follow_redirects=False)
        finally:
            event.remove(engine, "after_cursor_execute", cancel_after_status_read)

        assert response.status_code == 302 and raced
        saved = app_mod._SessionFactory()
        task = saved.get(ScheduledTask, tid)
        occurrences = (
            saved.query(TaskOccurrence)
            .filter(
                TaskOccurrence.task_id == tid,
                TaskOccurrence.status.in_(("pending", "interrupted", "paused", "claimed")),
            )
            .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
            .all()
        )
        assert task.status == "pending"
        assert {row.kind for row in occurrences} == {"manual", "scheduled"}
        assert all(row.status == "pending" for row in occurrences)
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_run_now_preserves_an_overdue_occurrence_and_its_inputs(tmp_path, monkeypatch):
    c, app_mod, tid = _client(tmp_path)
    try:
        due_at = datetime.now(timezone.utc) - timedelta(hours=1)
        setup = app_mod._SessionFactory()
        task = setup.get(ScheduledTask, tid)
        task.schedule = "once"
        task.status = "pending"
        task.next_run_at = due_at
        task.queued_inputs = json.dumps(["for the overdue run"])
        setup.commit()

        assert c.post(f"/tasks/{tid}/run-now", follow_redirects=False).status_code == 302
        reserved = app_mod._SessionFactory().get(ScheduledTask, tid)
        occurrences = (
            app_mod._SessionFactory()
            .query(TaskOccurrence)
            .filter(TaskOccurrence.task_id == tid)
            .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
            .all()
        )
        manual_at = max((o for o in occurrences if o.kind == "manual"), key=lambda o: o.id).due_at
        assert reserved.next_run_at == due_at.replace(tzinfo=None)
        assert reserved.interrupted_run_at is None
        assert manual_at > reserved.next_run_at

        seen = []

        def _run(task, db):
            seen.append((task._scheduled_for, scheduler._effective_goal(task)))
            return "done"

        monkeypatch.setattr(scheduler, "_run_task", _run)
        monkeypatch.setattr(scheduler, "_verify_task_result", lambda task, result, db: None)
        scheduler._tick(setup.get_bind())
        scheduler._tick(setup.get_bind())

        assert [occurrence for occurrence, _ in seen] == [
            due_at,
            manual_at.replace(tzinfo=timezone.utc),
        ]
        assert "for the overdue run" in seen[0][1]
        assert "for the overdue run" not in seen[1][1]
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_run_now_still_runs_before_a_future_scheduled_occurrence(tmp_path):
    c, app_mod, tid = _client(tmp_path)
    try:
        setup = app_mod._SessionFactory()
        task = setup.get(ScheduledTask, tid)
        future_at = datetime.now(timezone.utc) + timedelta(days=1)
        task.status = "pending"
        task.next_run_at = future_at
        setup.commit()

        before = datetime.now(timezone.utc)
        assert c.post(f"/tasks/{tid}/run-now", follow_redirects=False).status_code == 302
        after = datetime.now(timezone.utc)

        fresh = app_mod._SessionFactory().get(ScheduledTask, tid)
        manual_at = fresh.next_run_at.replace(tzinfo=timezone.utc)
        assert before <= manual_at <= after
        assert fresh.interrupted_run_at is None
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_queue_behind_overdue_occurrence_reserves_its_own_inputs(tmp_path, monkeypatch):
    c, app_mod, tid = _client(tmp_path)
    try:
        due_at = datetime.now(timezone.utc) - timedelta(hours=1)
        setup = app_mod._SessionFactory()
        task = setup.get(ScheduledTask, tid)
        task.schedule = "once"
        task.status = "pending"
        task.next_run_at = due_at
        task.queued_inputs = json.dumps(["for the overdue run"])
        setup.commit()

        response = c.post(
            f"/tasks/{tid}/queue",
            data={"instruction": "for the later run"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        reserved = app_mod._SessionFactory().get(ScheduledTask, tid)
        occurrences = (
            app_mod._SessionFactory()
            .query(TaskOccurrence)
            .filter(TaskOccurrence.task_id == tid)
            .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
            .all()
        )
        queued_at = max((o for o in occurrences if o.kind == "queued"), key=lambda o: o.id).due_at
        assert reserved.next_run_at == due_at.replace(tzinfo=None)
        assert reserved.interrupted_run_at is None
        assert queued_at > reserved.next_run_at

        seen = []

        def _run(task, db):
            seen.append((task._scheduled_for, scheduler._effective_goal(task)))
            return "done"

        monkeypatch.setattr(scheduler, "_run_task", _run)
        monkeypatch.setattr(scheduler, "_verify_task_result", lambda task, result, db: None)
        scheduler._tick(setup.get_bind())
        scheduler._tick(setup.get_bind())

        assert [occurrence for occurrence, _ in seen] == [
            due_at,
            queued_at.replace(tzinfo=timezone.utc),
        ]
        assert "for the overdue run" in seen[0][1]
        assert "for the later run" not in seen[0][1]
        assert "for the overdue run" not in seen[1][1]
        assert "for the later run" in seen[1][1]
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_queue_behind_interrupted_one_shot_reserves_a_later_occurrence(tmp_path):
    c, app_mod, tid = _client(tmp_path)
    try:
        scheduled_for = datetime.now(timezone.utc) - timedelta(hours=1)
        s = app_mod._SessionFactory()
        task = s.get(ScheduledTask, tid)
        task.schedule = "once"
        task.status = "pending"
        task.interrupted_run_at = scheduled_for
        task.next_run_at = None
        task.queued_inputs = json.dumps(["claimed before interruption"])
        s.add(
            TaskRun(
                org_id=task.org_id,
                task_id=task.id,
                status="error",
                scheduled_for=scheduled_for,
                claimed_inputs=json.dumps(["claimed before interruption"]),
            )
        )
        s.commit()

        r = c.post(
            f"/tasks/{tid}/queue",
            data={"instruction": "queued after recovery"},
            follow_redirects=False,
        )

        assert r.status_code == 302
        fresh = app_mod._SessionFactory().get(ScheduledTask, tid)
        assert fresh.status == "pending"
        assert fresh.next_run_at == fresh.interrupted_run_at
        queued_occurrence = (
            app_mod._SessionFactory()
            .query(TaskOccurrence)
            .filter(TaskOccurrence.task_id == tid, TaskOccurrence.kind == "queued")
            .one()
        )
        assert queued_occurrence.due_at > fresh.interrupted_run_at
        assert json.loads(fresh.queued_inputs) == [
            "claimed before interruption",
            "queued after recovery",
        ]
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_cancelling_active_recurring_run_preserves_reactivatable_cadence(tmp_path):
    c, app_mod, tid = _client(tmp_path)
    try:
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        now = datetime.now(timezone.utc)
        active = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind="scheduled",
            status="claimed",
            due_at=now - timedelta(hours=1),
            inputs="[]",
        )
        task.schedule = "daily"
        task.timezone = "UTC"
        task.schedule_anchor = active.due_at.replace(tzinfo=None)
        task.status = "running"
        session.add(active)
        session.flush()
        run = TaskRun(
            org_id=task.org_id,
            task_id=task.id,
            occurrence_id=active.id,
            status="running",
            scheduled_for=active.due_at,
            started_at=now,
        )
        session.add(run)
        session.flush()
        active.claimed_run_id = run.id
        session.commit()

        assert c.post(f"/tasks/{tid}/cancel", follow_redirects=False).status_code == 302
        cancelled = app_mod._SessionFactory()
        cadence = (
            cancelled.query(TaskOccurrence)
            .filter(
                TaskOccurrence.task_id == tid,
                TaskOccurrence.kind == "scheduled",
                TaskOccurrence.status == "paused",
            )
            .one()
        )
        cadence_id = cadence.id
        assert cadence.due_at > now.replace(tzinfo=None)

        assert c.post(f"/tasks/{tid}/run-now", follow_redirects=False).status_code == 302
        reactivated = app_mod._SessionFactory()
        cadence = reactivated.get(TaskOccurrence, cadence_id)
        scheduled = (
            reactivated.query(TaskOccurrence)
            .filter(
                TaskOccurrence.task_id == tid,
                TaskOccurrence.kind == "scheduled",
                TaskOccurrence.status.in_(("pending", "paused")),
            )
            .all()
        )
        assert cadence.status == "pending"
        assert [row.id for row in scheduled] == [cadence_id]
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_cancelling_active_run_keeps_newer_independent_cadence(tmp_path):
    c, app_mod, tid = _client(tmp_path)
    try:
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        now = datetime.now(timezone.utc)
        active = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind="scheduled",
            status="claimed",
            due_at=now - timedelta(days=2),
            inputs="[]",
        )
        cadence = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind="scheduled",
            status="pending",
            due_at=now + timedelta(days=3),
            inputs="[]",
        )
        task.schedule = "daily"
        task.timezone = "UTC"
        task.schedule_anchor = active.due_at.replace(tzinfo=None)
        task.status = "running"
        session.add_all([active, cadence])
        session.flush()
        run = TaskRun(
            org_id=task.org_id,
            task_id=task.id,
            occurrence_id=active.id,
            status="running",
            scheduled_for=active.due_at,
            started_at=now,
        )
        session.add(run)
        session.flush()
        active.claimed_run_id = run.id
        session.commit()
        cadence_id = cadence.id
        edited_due = cadence.due_at

        assert c.post(f"/tasks/{tid}/cancel", follow_redirects=False).status_code == 302

        saved = app_mod._SessionFactory()
        preserved = saved.get(TaskOccurrence, cadence_id)
        scheduled = (
            saved.query(TaskOccurrence)
            .filter(
                TaskOccurrence.task_id == tid,
                TaskOccurrence.kind == "scheduled",
                TaskOccurrence.status.in_(("pending", "paused")),
            )
            .all()
        )
        assert (preserved.status, preserved.due_at) == ("paused", edited_due)
        assert [row.id for row in scheduled] == [cadence_id]
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_cancelling_deferred_claim_replaces_superseded_cadence(tmp_path):
    c, app_mod, tid = _client(tmp_path)
    try:
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        now = datetime.now(timezone.utc)
        claimed_due = now - timedelta(days=1)
        edited_anchor = now.replace(hour=15, minute=30, second=0, microsecond=0).replace(
            tzinfo=None
        )
        claimed = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind="scheduled",
            status="claimed",
            due_at=claimed_due,
            inputs="[]",
            cadence_deferred=True,
        )
        stale_cadence = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind="scheduled",
            status="pending",
            due_at=now + timedelta(hours=1),
            inputs="[]",
        )
        input_work = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind="scheduled",
            status="pending",
            due_at=now + timedelta(hours=2),
            inputs=json.dumps(["preserve me"]),
        )
        task.schedule = "weekly"
        task.timezone = "UTC"
        task.schedule_anchor = edited_anchor
        task.status = "running"
        session.add_all([claimed, stale_cadence, input_work])
        session.flush()
        run = TaskRun(
            org_id=task.org_id,
            task_id=task.id,
            occurrence_id=claimed.id,
            status="running",
            scheduled_for=claimed_due,
            started_at=now,
        )
        session.add(run)
        session.flush()
        claimed.claimed_run_id = run.id
        session.commit()
        expected_due = scheduler._next_run(
            "weekly",
            from_dt=claimed_due,
            timezone_name="UTC",
            schedule_anchor=edited_anchor,
        )
        while expected_due <= now:
            expected_due = scheduler._next_run(
                "weekly",
                from_dt=expected_due,
                timezone_name="UTC",
                schedule_anchor=edited_anchor,
            )

        assert c.post(f"/tasks/{tid}/cancel", follow_redirects=False).status_code == 302

        saved = app_mod._SessionFactory()
        cadence = saved.get(TaskOccurrence, stale_cadence.id)
        queued = saved.get(TaskOccurrence, input_work.id)
        assert (cadence.status, cadence.due_at) == (
            "paused",
            expected_due.replace(tzinfo=None),
        )
        assert (queued.kind, queued.status, json.loads(queued.inputs)) == (
            "queued",
            "paused",
            ["preserve me"],
        )
        scheduled = (
            saved.query(TaskOccurrence)
            .filter(
                TaskOccurrence.task_id == tid,
                TaskOccurrence.kind == "scheduled",
                TaskOccurrence.status.in_(("pending", "paused")),
            )
            .all()
        )
        assert [row.id for row in scheduled] == [stale_cadence.id]
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_cancelling_interrupted_recurring_run_synthesizes_missing_cadence(tmp_path):
    c, app_mod, tid = _client(tmp_path)
    try:
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        now = datetime.now(timezone.utc)
        interrupted = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind="scheduled",
            status="interrupted",
            due_at=now - timedelta(hours=2),
            inputs="[]",
        )
        task.schedule = "daily"
        task.timezone = "UTC"
        task.schedule_anchor = interrupted.due_at.replace(tzinfo=None)
        task.status = "pending"
        session.add(interrupted)
        session.commit()

        assert c.post(f"/tasks/{tid}/cancel", follow_redirects=False).status_code == 302

        cancelled = app_mod._SessionFactory()
        assert cancelled.get(TaskOccurrence, interrupted.id).status == "cancelled"
        cadence = (
            cancelled.query(TaskOccurrence)
            .filter(
                TaskOccurrence.task_id == tid,
                TaskOccurrence.kind == "scheduled",
                TaskOccurrence.status == "paused",
            )
            .one()
        )
        assert cadence.due_at > now.replace(tzinfo=None)

        assert c.post(f"/tasks/{tid}/run-now", follow_redirects=False).status_code == 302
        assert app_mod._SessionFactory().get(TaskOccurrence, cadence.id).status == "pending"
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_cancel_then_run_again_survives_a_crash_without_replay(tmp_path, monkeypatch):
    c, app_mod, tid = _client(tmp_path)
    try:
        s = app_mod._SessionFactory()
        task = s.get(ScheduledTask, tid)
        scheduled_for = datetime.now(timezone.utc) - timedelta(hours=1)
        task.schedule = "once"
        task.status = "running"
        task.next_run_at = None
        task.queued_inputs = json.dumps(["claimed", "queued later"])
        s.add(
            TaskRun(
                org_id=task.org_id,
                task_id=task.id,
                status="running",
                scheduled_for=scheduled_for,
                started_at=scheduled_for,
                claimed_inputs=json.dumps(["claimed"]),
            )
        )
        s.commit()

        c.post(f"/tasks/{tid}/cancel", follow_redirects=False)
        c.post(f"/tasks/{tid}/run-now", follow_redirects=False)

        before_crash = app_mod._SessionFactory()
        fresh = before_crash.get(ScheduledTask, tid)
        active_run = before_crash.query(TaskRun).filter(TaskRun.task_id == tid).one()
        manual = (
            before_crash.query(TaskOccurrence)
            .filter(TaskOccurrence.task_id == tid, TaskOccurrence.kind == "manual")
            .one()
        )
        manual_at = manual.due_at
        assert fresh.status == "cancelled"
        assert fresh.next_run_at is None
        assert active_run.cancel_requested is True
        assert json.loads(fresh.queued_inputs) == ["queued later"]

        scheduler._sweep_stale_running_runs(s.get_bind())
        recovered = app_mod._SessionFactory().get(ScheduledTask, tid)
        assert recovered.status == "pending"
        assert recovered.interrupted_run_at is None
        assert recovered.next_run_at <= manual_at

        seen = []

        def _run(task, db):
            seen.append((task._scheduled_for, scheduler._effective_goal(task)))
            return "done"

        monkeypatch.setattr(scheduler, "_run_task", _run)
        monkeypatch.setattr(scheduler, "_verify_task_result", lambda task, result, db: None)
        scheduler._tick(s.get_bind())
        scheduler._tick(s.get_bind())

        assert len(seen) == 2
        assert seen[-1][0] == manual_at.replace(tzinfo=timezone.utc)
        assert all("claimed" not in goal for _, goal in seen)
        assert "queued later" in seen[0][1]
        assert "queued later" not in seen[1][1]
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_cancelled_interrupted_input_is_not_replayed_when_repurposed(tmp_path):
    c, app_mod, tid = _client(tmp_path)
    try:
        setup = app_mod._SessionFactory()
        original = setup.get(ScheduledTask, tid)
        scheduled_for = datetime.now(timezone.utc) - timedelta(hours=1)
        task_ids = []
        for action in ("run-now", "queue"):
            task = ScheduledTask(
                org_id=original.org_id,
                created_by=original.created_by,
                title=f"Interrupted {action}",
                goal="Summarize unread email",
                schedule="once",
                status="pending",
                interrupted_run_at=scheduled_for,
                queued_inputs=json.dumps(["claimed", "queued later"]),
            )
            setup.add(task)
            setup.flush()
            setup.add_all(
                [
                    TaskOccurrence(
                        org_id=task.org_id,
                        task_id=task.id,
                        kind="scheduled",
                        status="interrupted",
                        due_at=scheduled_for,
                        inputs=json.dumps(["claimed"]),
                    ),
                    TaskOccurrence(
                        org_id=task.org_id,
                        task_id=task.id,
                        kind="queued",
                        status="pending",
                        due_at=scheduled_for + timedelta(microseconds=1),
                        inputs=json.dumps(["queued later"]),
                    ),
                ]
            )
            task_ids.append((task.id, action))
        setup.commit()

        for task_id, action in task_ids:
            assert c.post(f"/tasks/{task_id}/cancel", follow_redirects=False).status_code == 302
            cancelled = app_mod._SessionFactory().get(ScheduledTask, task_id)
            assert cancelled.status == "cancelled"
            assert cancelled.interrupted_run_at is None
            assert json.loads(cancelled.queued_inputs) == ["queued later"]

            if action == "queue":
                response = c.post(
                    f"/tasks/{task_id}/queue",
                    data={"instruction": "new input"},
                    follow_redirects=False,
                )
                expected_inputs = ["queued later", "new input"]
            else:
                response = c.post(f"/tasks/{task_id}/run-now", follow_redirects=False)
                expected_inputs = ["queued later"]
            assert response.status_code == 302
            repurposed = app_mod._SessionFactory().get(ScheduledTask, task_id)
            assert repurposed.status == "pending"
            assert repurposed.next_run_at is not None
            assert json.loads(repurposed.queued_inputs) == expected_inputs
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_interrupted_queue_and_run_now_keep_three_owned_occurrences(tmp_path, monkeypatch):
    c, app_mod, tid = _client(tmp_path)
    try:
        due = datetime.now(timezone.utc) - timedelta(hours=1)
        s = app_mod._SessionFactory()
        task = s.get(ScheduledTask, tid)
        task.schedule = "once"
        task.status = "pending"
        task.interrupted_run_at = due
        task.interrupted_inputs = json.dumps(["A"])
        task.queued_inputs = json.dumps(["A", "Q"])
        task.next_run_at = due + timedelta(minutes=1)
        s.commit()

        assert c.post(f"/tasks/{tid}/run-now", follow_redirects=False).status_code == 302
        occurrences = (
            app_mod._SessionFactory()
            .query(TaskOccurrence)
            .filter(TaskOccurrence.task_id == tid)
            .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
            .all()
        )
        assert [(o.kind, json.loads(o.inputs)) for o in occurrences] == [
            ("scheduled", ["A"]),
            ("queued", ["Q"]),
            ("manual", []),
        ]

        seen = []
        monkeypatch.setattr(
            scheduler,
            "_run_task",
            lambda task, db: seen.append(scheduler._effective_goal(task)) or "done",
        )
        monkeypatch.setattr(scheduler, "_verify_task_result", lambda task, result, db: None)
        scheduler._tick(s.get_bind())
        scheduler._tick(s.get_bind())
        scheduler._tick(s.get_bind())
        assert ["- A" in goal for goal in seen] == [True, False, False]
        assert ["- Q" in goal for goal in seen] == [False, True, False]
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_duplicate_input_ownership_is_not_double_consumed_after_cancel(tmp_path, monkeypatch):
    c, app_mod, tid = _client(tmp_path)
    try:
        now = datetime.now(timezone.utc)
        s = app_mod._SessionFactory()
        task = s.get(ScheduledTask, tid)
        task.schedule = "once"
        task.status = "running"
        s.flush()
        active = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind="scheduled",
            status="claimed",
            due_at=now - timedelta(hours=1),
            inputs=json.dumps(["same"]),
        )
        later = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind="queued",
            status="pending",
            due_at=now,
            inputs=json.dumps(["same"]),
        )
        s.add_all([active, later])
        s.flush()
        run = TaskRun(
            org_id=task.org_id,
            task_id=task.id,
            occurrence_id=active.id,
            status="running",
            claimed_inputs=active.inputs,
            scheduled_for=active.due_at,
            started_at=now,
        )
        s.add(run)
        s.flush()
        active.claimed_run_id = run.id
        s.commit()

        c.post(f"/tasks/{tid}/cancel", follow_redirects=False)
        c.post(f"/tasks/{tid}/queue", data={"instruction": "new"}, follow_redirects=False)
        scheduler._sweep_stale_running_runs(s.get_bind())
        seen = []
        monkeypatch.setattr(
            scheduler,
            "_run_task",
            lambda task, db: seen.append(scheduler._effective_goal(task)) or "done",
        )
        monkeypatch.setattr(scheduler, "_verify_task_result", lambda task, result, db: None)
        scheduler._tick(s.get_bind())
        scheduler._tick(s.get_bind())

        assert len(seen) == 2
        assert sum(goal.count("- same") for goal in seen) == 1
        assert sum("- new" in goal for goal in seen) == 1
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_run_now_repairs_stale_running_task(tmp_path):
    c, app_mod, tid = _client(tmp_path)
    try:
        s = app_mod._SessionFactory()
        task = s.get(ScheduledTask, tid)
        task.status = "running"
        task.next_run_at = None
        s.commit()

        c.post(f"/tasks/{tid}/run-now", follow_redirects=False)

        fresh = app_mod._SessionFactory().get(ScheduledTask, tid)
        assert fresh.status == "pending"
        assert fresh.next_run_at is not None
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_queue_repairs_stale_running_task(tmp_path):
    c, app_mod, tid = _client(tmp_path)
    try:
        s = app_mod._SessionFactory()
        task = s.get(ScheduledTask, tid)
        task.status = "running"
        task.next_run_at = None
        s.commit()

        c.post(
            f"/tasks/{tid}/queue",
            data={"instruction": "include a TL;DR"},
            follow_redirects=False,
        )

        fresh = app_mod._SessionFactory().get(ScheduledTask, tid)
        assert fresh.status == "pending"
        assert fresh.next_run_at is not None
        assert json.loads(fresh.queued_inputs) == ["include a TL;DR"]
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


@pytest.mark.parametrize(
    ("occupied_state", "occupied_status"),
    [("active", "claimed"), ("interrupted", "interrupted")],
)
def test_legacy_equal_time_request_remains_a_distinct_occurrence(
    tmp_path, occupied_state, occupied_status
):
    from anthill.web import task_occurrences

    _c, app_mod, tid = _client(tmp_path)
    try:
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        occupied_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        task.schedule = "once"
        task.status = "running" if occupied_state == "active" else "pending"
        task.next_run_at = occupied_at
        if occupied_state == "active":
            task.last_run_at = occupied_at
            session.add(
                TaskRun(
                    org_id=task.org_id,
                    task_id=task.id,
                    trigger="scheduled",
                    status="running",
                    scheduled_for=occupied_at,
                )
            )
        else:
            task.interrupted_run_at = occupied_at
            task.interrupted_inputs = "[]"
        session.commit()

        task_occurrences.ensure_task(session, task)
        session.commit()

        occurrences = (
            session.query(TaskOccurrence)
            .filter(TaskOccurrence.task_id == tid)
            .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
            .all()
        )
        assert [(row.kind, row.status) for row in occurrences] == [
            ("scheduled", occupied_status),
            ("manual", "pending"),
        ]
        assert occurrences[0].due_at == occurrences[1].due_at
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


@pytest.mark.parametrize("occupied_state", ["active", "interrupted"])
def test_cancelled_legacy_occupied_work_stays_cancelled_with_duplicate_queue(
    tmp_path, occupied_state
):
    from anthill.web import task_occurrences

    _c, app_mod, tid = _client(tmp_path)
    try:
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        occupied_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        task.schedule = "once"
        task.status = "cancelled"
        task.queued_inputs = json.dumps(["same"])
        if occupied_state == "active":
            task.last_run_at = occupied_at
            session.add(
                TaskRun(
                    org_id=task.org_id,
                    task_id=task.id,
                    trigger="manual",
                    status="running",
                    claimed_inputs=json.dumps(["same"]),
                    scheduled_for=occupied_at,
                )
            )
        else:
            task.interrupted_run_at = occupied_at
            task.interrupted_inputs = json.dumps(["same"])
        session.commit()

        task_occurrences.ensure_task(session, task)
        session.commit()

        occurrences = (
            session.query(TaskOccurrence)
            .filter(TaskOccurrence.task_id == tid)
            .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
            .all()
        )
        assert [(row.status, row.kind, json.loads(row.inputs)) for row in occurrences] == [
            ("cancelled", "manual" if occupied_state == "active" else "scheduled", ["same"]),
            ("paused", "queued", ["same"]),
        ]
        if occupied_state == "interrupted":
            task_occurrences.run_now(session, task, datetime.now(timezone.utc))
            session.commit()
            session.expire_all()
            occupied = session.get(TaskOccurrence, occurrences[0].id)
            pending = (
                session.query(TaskOccurrence)
                .filter(
                    TaskOccurrence.task_id == tid,
                    TaskOccurrence.status == "pending",
                )
                .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
                .all()
            )
            assert occupied.status == "cancelled"
            assert [(row.kind, json.loads(row.inputs)) for row in pending] == [
                ("queued", ["same"]),
                ("manual", []),
            ]
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_legacy_cancelled_claim_keeps_duplicate_later_input(tmp_path):
    from anthill.web import task_occurrences

    _c, app_mod, tid = _client(tmp_path)
    try:
        s = app_mod._SessionFactory()
        task = s.get(ScheduledTask, tid)
        claimed_at = datetime.now(timezone.utc) - timedelta(hours=1)
        later_at = datetime.now(timezone.utc) + timedelta(hours=1)
        task.status = "cancelled"
        task.next_run_at = later_at
        task.queued_inputs = json.dumps(["same"])
        s.add(
            TaskRun(
                org_id=task.org_id,
                task_id=task.id,
                status="running",
                cancel_requested=True,
                claimed_inputs=json.dumps(["same"]),
                scheduled_for=claimed_at,
            )
        )
        s.commit()

        task_occurrences.ensure_task(s, task)
        s.commit()

        occurrences = (
            s.query(TaskOccurrence)
            .filter(TaskOccurrence.task_id == tid)
            .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
            .all()
        )
        assert [(row.status, row.kind, json.loads(row.inputs)) for row in occurrences] == [
            ("cancelled", "scheduled", ["same"]),
            ("paused", "queued", ["same"]),
        ]
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


@pytest.mark.parametrize(
    ("active_kind", "new_schedule", "scheduled_survives"),
    [("manual", "once", False), ("queued", "hourly", True)],
)
def test_schedule_edit_replaces_future_cadence_during_independent_run(
    tmp_path, active_kind, new_schedule, scheduled_survives
):
    c, app_mod, tid = _client(tmp_path)
    try:
        s = app_mod._SessionFactory()
        task = s.get(ScheduledTask, tid)
        now = datetime.now(timezone.utc)
        old_due = now + timedelta(days=2)
        task.schedule = "daily"
        task.status = "running"
        task.next_run_at = old_due
        active = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind=active_kind,
            status="claimed",
            due_at=now - timedelta(minutes=1),
            inputs="[]",
        )
        scheduled = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind="scheduled",
            status="pending",
            due_at=old_due,
            inputs="[]",
        )
        s.add_all([active, scheduled])
        s.flush()
        run = TaskRun(
            org_id=task.org_id,
            task_id=task.id,
            occurrence_id=active.id,
            status="running",
            scheduled_for=active.due_at,
        )
        s.add(run)
        s.flush()
        active.claimed_run_id = run.id
        s.commit()

        response = c.post(
            f"/tasks/{tid}/edit",
            data={"title": task.title, "goal": task.goal, "schedule": new_schedule},
            follow_redirects=False,
        )

        assert response.status_code == 302
        fresh = app_mod._SessionFactory()
        current = fresh.get(ScheduledTask, tid)
        future = (
            fresh.query(TaskOccurrence)
            .filter(
                TaskOccurrence.task_id == tid,
                TaskOccurrence.kind == "scheduled",
                TaskOccurrence.status.in_(("pending", "paused")),
            )
            .all()
        )
        assert current.schedule == new_schedule
        assert bool(future) is scheduled_survives
        if future:
            assert future[0].due_at != old_due.replace(tzinfo=None)
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_scheduler_rechecks_global_order_after_each_completion(tmp_path, monkeypatch):
    from anthill.web import task_occurrences

    _c, app_mod, first_id = _client(tmp_path)
    try:
        session = app_mod._SessionFactory()
        first = session.get(ScheduledTask, first_id)
        first.schedule = "once"
        first.status = "pending"
        now = datetime.now(timezone.utc)
        second = ScheduledTask(
            org_id=first.org_id,
            created_by=first.created_by,
            title="Second",
            goal="second",
            schedule="once",
            status="pending",
        )
        reactivated = ScheduledTask(
            org_id=first.org_id,
            created_by=first.created_by,
            title="Reactivated",
            goal="reactivated",
            schedule="once",
            status="cancelled",
        )
        session.add_all([second, reactivated])
        session.flush()
        session.add_all(
            [
                TaskOccurrence(
                    org_id=first.org_id,
                    task_id=first.id,
                    kind="scheduled",
                    status="pending",
                    due_at=now - timedelta(minutes=3),
                    inputs="[]",
                ),
                TaskOccurrence(
                    org_id=first.org_id,
                    task_id=second.id,
                    kind="scheduled",
                    status="pending",
                    due_at=now - timedelta(minutes=1),
                    inputs="[]",
                ),
                TaskOccurrence(
                    org_id=first.org_id,
                    task_id=reactivated.id,
                    kind="scheduled",
                    status="paused",
                    due_at=now - timedelta(minutes=2),
                    inputs="[]",
                ),
            ]
        )
        session.commit()
        second_id = second.id
        reactivated_id = reactivated.id
        seen = []

        def run(task, _db):
            seen.append(task.id)
            if task.id == first_id:
                other = app_mod._SessionFactory()
                try:
                    task_occurrences.run_now(
                        other,
                        other.get(ScheduledTask, reactivated_id),
                        datetime.now(timezone.utc),
                    )
                    other.commit()
                finally:
                    other.close()
            return "done"

        monkeypatch.setattr(scheduler, "_run_task", run)
        monkeypatch.setattr(scheduler, "_verify_task_result", lambda task, result, db: None)
        scheduler._tick(session.get_bind())

        assert seen == [first_id, reactivated_id, second_id]
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_compatibility_materialization_cas_prevents_duplicate_occurrences(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy import event

    from anthill.web import task_occurrences

    _c, app_mod, tid = _client(tmp_path)
    engine = app_mod._engine
    try:
        setup = app_mod._SessionFactory()
        task = setup.get(ScheduledTask, tid)
        task.status = "pending"
        task.schedule = "daily"
        task.next_run_at = datetime.now(timezone.utc) + timedelta(days=1)
        task.occurrences_materialized = False
        setup.commit()
        readers = Barrier(2)

        def align_legacy_readers(_conn, _cursor, statement, _parameters, _context, _many):
            if statement.lstrip().startswith("SELECT task_occurrences.id AS"):
                readers.wait(timeout=2)

        def materialize():
            session = app_mod._SessionFactory()
            try:
                task_occurrences.ensure_task(session, session.get(ScheduledTask, tid))
                session.commit()
            finally:
                session.close()

        event.listen(engine, "after_cursor_execute", align_legacy_readers)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(materialize) for _ in range(2)]
                for future in futures:
                    future.result(timeout=5)
        finally:
            event.remove(engine, "after_cursor_execute", align_legacy_readers)

        saved = app_mod._SessionFactory()
        occurrences = saved.query(TaskOccurrence).filter(TaskOccurrence.task_id == tid).all()
        assert len(occurrences) == 1
        assert occurrences[0].due_at == task.next_run_at
        assert saved.get(ScheduledTask, tid).occurrences_materialized is True
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_interrupted_materialization_ignores_unrelated_history(tmp_path):
    from anthill.web import task_occurrences

    _c, app_mod, tid = _client(tmp_path)
    try:
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        interrupted_at = datetime.now(timezone.utc) - timedelta(hours=1)
        unrelated = TaskRun(
            org_id=task.org_id,
            task_id=task.id,
            trigger="manual",
            status="ok",
            claimed_inputs=json.dumps(["unrelated input"]),
            scheduled_for=interrupted_at - timedelta(days=1),
        )
        task.schedule = "once"
        task.status = "pending"
        task.interrupted_run_at = interrupted_at
        task.interrupted_inputs = None
        task.occurrences_materialized = False
        session.add(unrelated)
        session.commit()

        task_occurrences.ensure_task(session, task)
        session.commit()

        occurrence = session.query(TaskOccurrence).filter(TaskOccurrence.task_id == tid).one()
        assert (occurrence.kind, json.loads(occurrence.inputs), occurrence.claimed_run_id) == (
            "scheduled",
            [],
            None,
        )
        assert session.get(TaskRun, unrelated.id).occurrence_id is None
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_tick_marks_dormant_tasks_without_inventing_occurrences(tmp_path, monkeypatch):
    _c, app_mod, dormant_id = _client(tmp_path)
    try:
        session = app_mod._SessionFactory()
        dormant = session.get(ScheduledTask, dormant_id)
        now = datetime.now(timezone.utc)
        for index in range(2):
            task = ScheduledTask(
                org_id=dormant.org_id,
                created_by=dormant.created_by,
                title=f"Due {index}",
                goal="g",
                schedule="once",
                status="pending",
            )
            session.add(task)
            session.flush()
            session.add(
                TaskOccurrence(
                    org_id=task.org_id,
                    task_id=task.id,
                    kind="manual",
                    status="pending",
                    due_at=now - timedelta(minutes=2 - index),
                    inputs="[]",
                )
            )
        session.commit()
        monkeypatch.setattr(scheduler, "_run_task", lambda task, db: "done")
        monkeypatch.setattr(scheduler, "_verify_task_result", lambda task, result, db: None)

        scheduler._tick(session.get_bind())
        scheduler._tick(session.get_bind())

        saved = app_mod._SessionFactory()
        markers = saved.query(TaskOccurrence).filter(TaskOccurrence.task_id == dormant_id).all()
        assert markers == []
        assert saved.get(ScheduledTask, dormant_id).occurrences_materialized is True
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_claim_retries_when_edit_moves_selected_occurrence_to_future(tmp_path):
    from sqlalchemy import event

    from anthill.web import task_occurrences

    _c, app_mod, tid = _client(tmp_path)
    engine = app_mod._engine
    try:
        session = app_mod._SessionFactory()
        task = session.get(ScheduledTask, tid)
        cutoff = datetime.now(timezone.utc)
        occurrence = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind="scheduled",
            status="pending",
            due_at=cutoff - timedelta(minutes=1),
            inputs=json.dumps(["preserve input"]),
        )
        task.status = "pending"
        session.add(occurrence)
        session.commit()
        future_due = cutoff + timedelta(days=1)
        raced = False

        def move_before_claim_cas(_conn, _cursor, statement, _parameters, _context, _many):
            nonlocal raced
            if raced or not statement.lstrip().startswith("UPDATE task_occurrences SET status"):
                return
            raced = True
            other = app_mod._SessionFactory()
            try:
                other.query(TaskOccurrence).filter(TaskOccurrence.id == occurrence.id).update(
                    {TaskOccurrence.due_at: future_due}, synchronize_session=False
                )
                other.commit()
            finally:
                other.close()

        event.listen(engine, "before_cursor_execute", move_before_claim_cas)
        try:
            claimed = task_occurrences.claim_next(session, cutoff)
        finally:
            event.remove(engine, "before_cursor_execute", move_before_claim_cas)

        assert raced and claimed is None
        session.expire_all()
        saved = session.get(TaskOccurrence, occurrence.id)
        assert (saved.status, saved.due_at, json.loads(saved.inputs)) == (
            "pending",
            future_due.replace(tzinfo=None),
            ["preserve input"],
        )
        assert session.query(TaskRun).filter(TaskRun.task_id == tid).count() == 0
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_global_claim_records_fresh_start_time_for_each_occurrence(tmp_path, monkeypatch):
    from anthill.web import task_occurrences

    _c, app_mod, first_id = _client(tmp_path)
    try:
        session = app_mod._SessionFactory()
        first = session.get(ScheduledTask, first_id)
        first.schedule = "once"
        first.status = "pending"
        cutoff = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
        second = ScheduledTask(
            org_id=first.org_id,
            created_by=first.created_by,
            title="Later claim",
            goal="later",
            schedule="once",
            status="pending",
        )
        session.add(second)
        session.flush()
        session.add_all(
            [
                TaskOccurrence(
                    org_id=first.org_id,
                    task_id=first.id,
                    kind="scheduled",
                    status="pending",
                    due_at=cutoff - timedelta(minutes=2),
                    inputs="[]",
                ),
                TaskOccurrence(
                    org_id=first.org_id,
                    task_id=second.id,
                    kind="scheduled",
                    status="pending",
                    due_at=cutoff - timedelta(minutes=1),
                    inputs="[]",
                ),
            ]
        )
        session.commit()
        starts = [cutoff + timedelta(seconds=1), cutoff + timedelta(minutes=20)]

        class Clock:
            @classmethod
            def now(cls, tz=None):
                return starts.pop(0)

        monkeypatch.setattr(task_occurrences, "datetime", Clock)
        first_claim = task_occurrences.claim_next(session, cutoff)
        second_claim = task_occurrences.claim_next(session, cutoff)
        task_ids = [first_claim.task.id, second_claim.task.id]
        session.commit()
        session.expire_all()
        runs = session.query(TaskRun).order_by(TaskRun.scheduled_for, TaskRun.id).all()

        assert task_ids == [first.id, second.id]
        assert [run.started_at for run in runs] == [
            (cutoff + timedelta(seconds=1)).replace(tzinfo=None),
            (cutoff + timedelta(minutes=20)).replace(tzinfo=None),
        ]
        assert [session.get(ScheduledTask, task_id).last_run_at for task_id in task_ids] == [
            (cutoff + timedelta(seconds=1)).replace(tzinfo=None),
            (cutoff + timedelta(minutes=20)).replace(tzinfo=None),
        ]
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_create_with_run_now_keeps_independent_recurring_cadence(tmp_path, monkeypatch):
    c, app_mod, _tid = _client(tmp_path)
    try:
        before = datetime.now(timezone.utc)
        response = c.post(
            "/tasks/create",
            data={
                "title": "Run now daily",
                "goal": "summarise",
                "schedule": "daily",
                "run_now": "true",
            },
            follow_redirects=False,
        )
        assert response.status_code == 302

        s = app_mod._SessionFactory()
        task = s.query(ScheduledTask).filter(ScheduledTask.title == "Run now daily").one()
        occurrences = (
            s.query(TaskOccurrence)
            .filter(TaskOccurrence.task_id == task.id)
            .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
            .all()
        )
        assert [row.kind for row in occurrences] == ["manual", "scheduled"]
        assert before <= occurrences[0].due_at.replace(tzinfo=timezone.utc)
        scheduled_due = occurrences[1].due_at

        monkeypatch.setattr(scheduler, "_run_task", lambda task, db: "done")
        monkeypatch.setattr(scheduler, "_verify_task_result", lambda task, result, db: None)
        scheduler._tick(s.get_bind())

        fresh = app_mod._SessionFactory()
        task = fresh.get(ScheduledTask, task.id)
        scheduled = (
            fresh.query(TaskOccurrence)
            .filter(
                TaskOccurrence.task_id == task.id,
                TaskOccurrence.kind == "scheduled",
                TaskOccurrence.status == "pending",
            )
            .one()
        )
        assert task.status == "pending"
        assert scheduled.due_at == scheduled_due
        assert task.next_run_at == scheduled_due
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_queued_recurring_rerun_keeps_future_cadence(tmp_path, monkeypatch):
    c, app_mod, tid = _client(tmp_path)
    try:
        s = app_mod._SessionFactory()
        task = s.get(ScheduledTask, tid)
        future_due = datetime.now(timezone.utc) + timedelta(days=1)
        task.schedule = "daily"
        task.status = "done"
        task.next_run_at = future_due
        s.add(
            TaskOccurrence(
                org_id=task.org_id,
                task_id=task.id,
                kind="scheduled",
                status="pending",
                due_at=future_due,
                inputs="[]",
            )
        )
        s.commit()

        response = c.post(
            f"/tasks/{tid}/queue",
            data={"instruction": "run this separately"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        seen = []
        monkeypatch.setattr(
            scheduler,
            "_run_task",
            lambda task, db: seen.append(scheduler._effective_goal(task)) or "done",
        )
        monkeypatch.setattr(scheduler, "_verify_task_result", lambda task, result, db: None)
        scheduler._tick(s.get_bind())

        fresh = app_mod._SessionFactory()
        task = fresh.get(ScheduledTask, tid)
        scheduled = (
            fresh.query(TaskOccurrence)
            .filter(
                TaskOccurrence.task_id == tid,
                TaskOccurrence.kind == "scheduled",
                TaskOccurrence.status == "pending",
            )
            .one()
        )
        assert "run this separately" in seen[0]
        assert scheduled.due_at == future_due.replace(tzinfo=None)
        assert task.status == "pending"
        assert task.next_run_at == scheduled.due_at
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None


def test_running_task_controls_preserve_execution_lease(tmp_path):
    c, app_mod, tid = _client(tmp_path)
    try:
        s = app_mod._SessionFactory()
        task = s.get(ScheduledTask, tid)
        task.status = "running"
        task.schedule = "once"
        task.next_run_at = None
        s.add(TaskRun(org_id=task.org_id, task_id=task.id, status="running"))
        s.commit()

        c.post(f"/tasks/{tid}/run-now", follow_redirects=False)
        c.post(
            f"/tasks/{tid}/queue",
            data={"instruction": "include a TL;DR"},
            follow_redirects=False,
        )
        c.post(
            f"/tasks/{tid}/edit",
            data={"title": "Digest", "goal": "Summarize unread email", "schedule": "hourly"},
            follow_redirects=False,
        )

        fresh = app_mod._SessionFactory().get(ScheduledTask, tid)
        assert fresh.status == "running"
        assert fresh.next_run_at is not None
        assert fresh.schedule == "hourly"
        assert json.loads(fresh.queued_inputs) == ["include a TL;DR"]
    finally:
        app_mod._engine = None
        app_mod._SessionFactory = None
