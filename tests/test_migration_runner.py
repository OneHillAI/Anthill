"""The lightweight versioned migration runner (anthill/web/migrate.py::run_migrations).

`ensure_columns` handles additive column adds; this runner handles ordered NON-additive migrations
(renames, backfills, table rebuilds) keyed to SQLite's PRAGMA user_version. Tests exercise both
injected migrations and the production registry through the runner's public behavior.
"""

from __future__ import annotations

import pytest
from sqlalchemy import inspect, text

from anthill.web import migrate
from anthill.web.db import create_tables, get_engine
from anthill.web.migrate import run_migrations


def _uv(engine) -> int:
    with engine.begin() as c:
        return migrate._user_version(c)


def test_fresh_db_is_stamped_to_head_without_running_migrations(tmp_path):
    ran: list[int] = []
    migs = [(1, "a", lambda c: ran.append(1)), (2, "b", lambda c: ran.append(2))]
    eng = get_engine(tmp_path / "fresh.db")  # empty db
    applied = run_migrations(eng, fresh=True, migrations=migs)
    assert applied == [] and ran == []  # end-state already built by create_all; not re-run
    assert _uv(eng) == 2  # ...but stamped at head so they never run later


def test_existing_db_runs_pending_in_order_and_snapshots_first(tmp_path):
    order: list = []
    migs = [(1, "a", lambda c: order.append(1)), (2, "b", lambda c: order.append(2))]
    eng = get_engine(tmp_path / "e.db")
    create_tables(eng)  # builds the schema; also stamps user_version to the PRODUCTION registry's
    # head (fresh db) - reset to 0 so this test's own injected list starts from a clean slate
    # regardless of how many real migrations have shipped.
    with eng.begin() as c:
        migrate._set_user_version(c, 0)
    applied = run_migrations(
        eng, fresh=False, migrations=migs, before_change=lambda names: order.append("snapshot")
    )
    assert applied == [1, 2]
    assert order == ["snapshot", 1, 2]  # snapshot happens before any migration
    assert _uv(eng) == 2
    # re-running is a no-op
    assert run_migrations(eng, fresh=False, migrations=migs) == []


def test_only_migrations_above_current_version_run(tmp_path):
    eng = get_engine(tmp_path / "p.db")
    create_tables(eng)
    with eng.begin() as c:
        migrate._set_user_version(c, 1)
    ran: list[int] = []
    migs = [(1, "a", lambda c: ran.append(1)), (2, "b", lambda c: ran.append(2))]
    assert run_migrations(eng, fresh=False, migrations=migs) == [2]
    assert ran == [2]  # migration 1 already applied, not re-run


def test_a_failing_migration_rolls_back_and_leaves_version_at_the_last_good_one(tmp_path):
    eng = get_engine(tmp_path / "x.db")
    create_tables(eng)
    with eng.begin() as c:  # reset past the production registry's fresh-db stamp; see above
        migrate._set_user_version(c, 0)

    def boom(c):
        raise RuntimeError("migration 2 failed")

    migs = [
        (1, "make t1", lambda c: c.exec_driver_sql("CREATE TABLE t1 (x INTEGER)")),
        (2, "boom", boom),
    ]
    with pytest.raises(RuntimeError, match="migration 2 failed"):
        run_migrations(eng, fresh=False, migrations=migs, before_change=lambda n: None)
    # migration 1 committed (table + version bump), migration 2 rolled back atomically
    assert _uv(eng) == 1
    with eng.begin() as c:
        assert (
            c.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='t1'")
            ).fetchone()
            is not None
        )


def test_default_snapshot_hook_is_invoked_when_none_passed(tmp_path, monkeypatch):
    calls: list = []
    import anthill.backup as backup

    monkeypatch.setattr(backup, "snapshot_db", lambda **kw: calls.append(kw))
    eng = get_engine(tmp_path / "s.db")
    create_tables(eng)
    with eng.begin() as c:  # reset past the production registry's fresh-db stamp; see above
        migrate._set_user_version(c, 0)
    run_migrations(eng, fresh=False, migrations=[(1, "noop", lambda c: None)])
    assert calls and calls[0].get("reason") == "pre-migration"


def test_occurrence_backfill_upgrades_pre_timezone_schema(tmp_path):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy.orm import sessionmaker

    from anthill.web.db import Organization, ScheduledTask, TaskOccurrence

    eng = get_engine(tmp_path / "legacy-tasks.db")
    create_tables(eng)
    db = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    db.add(org)
    db.flush()
    task = ScheduledTask(
        org_id=org.id,
        title="Legacy",
        goal="g",
        schedule="daily",
        status="pending",
        next_run_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    db.add(task)
    db.commit()
    task_id = task.id
    db.close()

    with eng.begin() as c:
        c.execute(text("DROP TABLE task_occurrences"))
        for column in (
            "timezone",
            "schedule_anchor",
            "cadence_needs_review",
            "cadence_review_reason",
            "occurrences_materialized",
            "interrupted_run_at",
            "interrupted_inputs",
            "queued_inputs",
            "verify_needs_review",
            "verify_reason",
            "verify_confidence",
        ):
            c.execute(text(f"ALTER TABLE scheduled_tasks DROP COLUMN {column}"))
        for column in ("claimed_inputs", "cancel_requested", "scheduled_for"):
            c.execute(text(f"ALTER TABLE task_runs DROP COLUMN {column}"))
        migrate._set_user_version(c, 2)

    create_tables(eng)

    assert _uv(eng) == migrate.MIGRATIONS[-1][0]  # the head, whatever it is
    assert {
        "timezone",
        "schedule_anchor",
        "cadence_needs_review",
        "cadence_review_reason",
        "occurrences_materialized",
        "interrupted_run_at",
        "interrupted_inputs",
        "queued_inputs",
        "verify_needs_review",
        "verify_reason",
        "verify_confidence",
    } <= {column["name"] for column in inspect(eng).get_columns("scheduled_tasks")}
    assert {"claimed_inputs", "cancel_requested", "scheduled_for"} <= {
        column["name"] for column in inspect(eng).get_columns("task_runs")
    }
    assert "cadence_deferred" in {
        column["name"] for column in inspect(eng).get_columns("task_occurrences")
    }
    session = sessionmaker(bind=eng)()
    occurrences = (
        session.query(TaskOccurrence)
        .filter_by(task_id=task_id)
        .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
        .all()
    )
    assert [(row.status, row.kind) for row in occurrences] == [("pending", "scheduled")]


def test_occurrence_backfill_preserves_unknown_active_queue_ownership(tmp_path):
    import json
    from datetime import datetime

    from sqlalchemy.orm import sessionmaker

    from anthill.web.db import Organization, ScheduledTask, TaskOccurrence, TaskRun

    eng = get_engine(tmp_path / "unknown-active-ownership.db")
    create_tables(eng)
    session = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    session.add(org)
    session.flush()
    started = datetime(2026, 1, 4, 9, 15)
    task = ScheduledTask(
        org_id=org.id,
        title="Legacy active",
        goal="g",
        schedule="once",
        status="running",
        last_run_at=started,
        queued_inputs=json.dumps(["first", "duplicate"]),
    )
    session.add(task)
    session.flush()
    session.add(TaskRun(org_id=org.id, task_id=task.id, status="running", started_at=started))
    session.commit()
    task_id = task.id
    session.close()

    with eng.begin() as conn:
        conn.execute(text("DROP TABLE task_occurrences"))
        for column in ("claimed_inputs", "cancel_requested"):
            conn.execute(text(f"ALTER TABLE task_runs DROP COLUMN {column}"))
        migrate._set_user_version(conn, 2)

    create_tables(eng)

    session = sessionmaker(bind=eng)()
    occurrences = (
        session.query(TaskOccurrence)
        .filter(TaskOccurrence.task_id == task_id)
        .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
        .all()
    )
    assert [(row.status, row.kind, json.loads(row.inputs)) for row in occurrences] == [
        ("claimed", "scheduled", []),
        ("pending", "queued", ["first", "duplicate"]),
    ]


@pytest.mark.parametrize(("inputs", "kind"), [([], "manual"), (["later"], "queued")])
def test_occurrence_backfill_classifies_user_one_shot_work(tmp_path, inputs, kind):
    import json
    from datetime import datetime, timedelta, timezone

    from sqlalchemy.orm import sessionmaker

    from anthill.web.db import Organization, ScheduledTask, TaskOccurrence, User

    eng = get_engine(tmp_path / f"user-once-{kind}.db")
    create_tables(eng)
    session = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    session.add(org)
    session.flush()
    user = User(org_id=org.id, email=f"{kind}@example.com", display_name="User")
    session.add(user)
    session.flush()
    due_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    task = ScheduledTask(
        org_id=org.id,
        created_by=user.id,
        title="Legacy user one-shot",
        goal="g",
        schedule="once",
        status="pending",
        next_run_at=due_at,
        queued_inputs=json.dumps(inputs),
    )
    session.add(task)
    session.commit()

    with eng.begin() as conn:
        migrate._mig_0003_task_occurrence_ledger(conn)

    occurrence = session.query(TaskOccurrence).filter(TaskOccurrence.task_id == task.id).one()
    assert (occurrence.kind, json.loads(occurrence.inputs)) == (kind, inputs)


@pytest.mark.parametrize(
    ("schedule", "kind", "use_last_run"),
    [("once", "manual", False), ("daily", "scheduled", True)],
)
def test_occurrence_backfill_recovers_running_task_without_history(
    tmp_path, schedule, kind, use_last_run
):
    import json
    from datetime import datetime, timedelta, timezone

    from sqlalchemy.orm import sessionmaker

    from anthill.web.db import Organization, ScheduledTask, TaskOccurrence, User

    eng = get_engine(tmp_path / f"running-without-run-{schedule}.db")
    create_tables(eng)
    session = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    session.add(org)
    session.flush()
    user = User(org_id=org.id, email=f"{schedule}@example.com", display_name="User")
    session.add(user)
    session.flush()
    created_at = datetime.now(timezone.utc) - timedelta(hours=2)
    last_run_at = created_at + timedelta(hours=1) if use_last_run else None
    task = ScheduledTask(
        org_id=org.id,
        created_by=user.id,
        title="Legacy running task",
        goal="g",
        schedule=schedule,
        status="running",
        last_run_at=last_run_at,
        queued_inputs=json.dumps(["separate input"]),
        created_at=created_at,
    )
    session.add(task)
    session.commit()

    with eng.begin() as conn:
        migrate._mig_0003_task_occurrence_ledger(conn)

    session.expire_all()
    interrupted = (
        session.query(TaskOccurrence)
        .filter(TaskOccurrence.task_id == task.id, TaskOccurrence.status == "interrupted")
        .one()
    )
    queued = (
        session.query(TaskOccurrence)
        .filter(TaskOccurrence.task_id == task.id, TaskOccurrence.kind == "queued")
        .one()
    )
    expected_due = (last_run_at or created_at).replace(tzinfo=None)
    assert (interrupted.kind, interrupted.due_at, json.loads(interrupted.inputs)) == (
        kind,
        expected_due,
        [],
    )
    assert json.loads(queued.inputs) == ["separate input"]
    assert session.get(ScheduledTask, task.id).status == "pending"


def test_occurrence_backfill_preserves_manual_active_attempt_kind(tmp_path):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy.orm import sessionmaker

    from anthill.web.db import Organization, ScheduledTask, TaskOccurrence, TaskRun

    eng = get_engine(tmp_path / "manual-active-attempt.db")
    create_tables(eng)
    session = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    session.add(org)
    session.flush()
    claimed_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    task = ScheduledTask(
        org_id=org.id,
        title="Legacy manual attempt",
        goal="g",
        schedule="once",
        status="running",
        last_run_at=claimed_at,
    )
    session.add(task)
    session.flush()
    run = TaskRun(
        org_id=org.id,
        task_id=task.id,
        trigger="manual",
        status="running",
        scheduled_for=claimed_at,
    )
    session.add(run)
    session.commit()

    with eng.begin() as conn:
        migrate._mig_0003_task_occurrence_ledger(conn)

    session.expire_all()
    occurrence = session.query(TaskOccurrence).filter(TaskOccurrence.task_id == task.id).one()
    assert (occurrence.kind, occurrence.status) == ("manual", "claimed")
    assert session.get(TaskRun, run.id).occurrence_id == occurrence.id


def test_occurrence_backfill_makes_legacy_task_cancellation_win(tmp_path):
    import json
    from datetime import datetime

    from sqlalchemy.orm import sessionmaker

    from anthill.web.db import Organization, ScheduledTask, TaskOccurrence, TaskRun

    eng = get_engine(tmp_path / "legacy-active-cancelled.db")
    create_tables(eng)
    session = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    session.add(org)
    session.flush()
    started = datetime(2026, 1, 4, 9, 15)
    task = ScheduledTask(
        org_id=org.id,
        title="Legacy cancelled active",
        goal="g",
        schedule="daily",
        status="cancelled",
        last_run_at=started,
        next_run_at=None,
        queued_inputs=json.dumps(["preserve all"]),
    )
    session.add(task)
    session.flush()
    session.add(TaskRun(org_id=org.id, task_id=task.id, status="running", started_at=started))
    session.commit()
    task_id = task.id
    session.close()

    with eng.begin() as conn:
        conn.execute(text("DROP TABLE task_occurrences"))
        for column in ("claimed_inputs", "cancel_requested"):
            conn.execute(text(f"ALTER TABLE task_runs DROP COLUMN {column}"))
        migrate._set_user_version(conn, 2)

    create_tables(eng)

    session = sessionmaker(bind=eng)()
    run = session.query(TaskRun).filter(TaskRun.task_id == task_id).one()
    occurrences = (
        session.query(TaskOccurrence)
        .filter(TaskOccurrence.task_id == task_id)
        .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
        .all()
    )
    assert run.cancel_requested is True
    assert [(row.status, row.kind, json.loads(row.inputs)) for row in occurrences] == [
        ("cancelled", "scheduled", []),
        ("paused", "queued", ["preserve all"]),
        ("paused", "scheduled", []),
    ]
    assert session.get(ScheduledTask, task_id).next_run_at is None


def test_cancelled_backfill_reprojects_legacy_occurrence_fields(tmp_path):
    import json
    from datetime import datetime, timedelta, timezone

    from sqlalchemy.orm import sessionmaker

    from anthill.web.db import Organization, ScheduledTask

    eng = get_engine(tmp_path / "cancelled-projections.db")
    create_tables(eng)
    session = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    session.add(org)
    session.flush()
    now = datetime.now(timezone.utc)
    task = ScheduledTask(
        org_id=org.id,
        title="Legacy cancelled projections",
        goal="g",
        schedule="once",
        status="cancelled",
        next_run_at=now + timedelta(hours=1),
        interrupted_run_at=now - timedelta(hours=1),
        interrupted_inputs="[]",
        queued_inputs=json.dumps(["later"]),
    )
    session.add(task)
    session.commit()

    with eng.begin() as conn:
        migrate._mig_0003_task_occurrence_ledger(conn)

    session.expire_all()
    migrated = session.get(ScheduledTask, task.id)
    assert migrated.status == "cancelled"
    assert migrated.next_run_at is None
    assert migrated.interrupted_run_at is None
    assert migrated.interrupted_inputs is None
    assert json.loads(migrated.queued_inputs) == ["later"]


def test_cancelled_recurring_backfill_reactivates_cadence_with_run_now(tmp_path):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy.orm import sessionmaker

    from anthill.web import task_occurrences
    from anthill.web.db import Organization, ScheduledTask, TaskOccurrence

    eng = get_engine(tmp_path / "cancelled-recurring-no-due.db")
    create_tables(eng)
    session = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    session.add(org)
    session.flush()
    task = ScheduledTask(
        org_id=org.id,
        title="Legacy cancelled recurring",
        goal="g",
        schedule="daily",
        status="cancelled",
        next_run_at=None,
        created_at=(datetime.now(timezone.utc) - timedelta(days=5)).replace(
            hour=9, minute=15, second=0, microsecond=0
        ),
    )
    session.add(task)
    session.commit()

    with eng.begin() as conn:
        migrate._mig_0003_task_occurrence_ledger(conn)

    task_id = task.id
    session.close()
    session = sessionmaker(bind=eng)()
    task = session.get(ScheduledTask, task_id)
    cadence = session.query(TaskOccurrence).filter(TaskOccurrence.task_id == task.id).one()
    assert cadence.kind == "scheduled" and cadence.status == "paused"
    assert session.get(ScheduledTask, task.id).next_run_at is None

    task_occurrences.reactivate(session, session.get(ScheduledTask, task.id))
    task_occurrences.run_now(
        session, session.get(ScheduledTask, task.id), datetime.now(timezone.utc)
    )
    session.commit()
    occurrences = (
        session.query(TaskOccurrence)
        .filter(
            TaskOccurrence.task_id == task.id,
            TaskOccurrence.status.in_(("pending", "interrupted", "paused", "claimed")),
        )
        .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
        .all()
    )
    assert [row.kind for row in occurrences] == ["manual", "scheduled"]
    assert all(row.status == "pending" for row in occurrences)


def test_occurrence_backfill_keeps_duplicate_after_cancelled_claim(tmp_path):
    import json
    from datetime import datetime, timedelta

    from sqlalchemy.orm import sessionmaker

    from anthill.web.db import Organization, ScheduledTask, TaskOccurrence, TaskRun

    eng = get_engine(tmp_path / "cancelled-duplicate.db")
    create_tables(eng)
    session = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    session.add(org)
    session.flush()
    claimed_at = datetime(2026, 9, 1, 9, 0)
    task = ScheduledTask(
        org_id=org.id,
        title="Legacy cancelled",
        goal="g",
        schedule="once",
        status="cancelled",
        next_run_at=claimed_at + timedelta(hours=1),
        queued_inputs=json.dumps(["same"]),
    )
    session.add(task)
    session.flush()
    run = TaskRun(
        org_id=org.id,
        task_id=task.id,
        status="running",
        cancel_requested=True,
        claimed_inputs=json.dumps(["same"]),
        scheduled_for=claimed_at,
    )
    session.add(run)
    session.commit()

    with eng.begin() as conn:
        migrate._mig_0003_task_occurrence_ledger(conn)

    occurrences = (
        session.query(TaskOccurrence)
        .filter(TaskOccurrence.task_id == task.id)
        .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
        .all()
    )
    assert [(row.status, row.kind, json.loads(row.inputs)) for row in occurrences] == [
        ("cancelled", "scheduled", ["same"]),
        ("paused", "queued", ["same"]),
    ]


def test_recurring_backfill_prefers_future_cadence_evidence_over_stale_last_run(tmp_path):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy.orm import sessionmaker

    from anthill.web.db import Organization, ScheduledTask, TaskOccurrence

    eng = get_engine(tmp_path / "future-cadence-evidence.db")
    create_tables(eng)
    session = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    session.add(org)
    session.flush()
    now = datetime.now(timezone.utc)
    days_until_friday = (4 - now.weekday()) % 7
    future = (now + timedelta(days=days_until_friday)).replace(
        hour=15, minute=0, second=0, microsecond=0
    )
    if future <= now:
        future += timedelta(days=7)
    stale_last_run = (future - timedelta(days=11)).replace(hour=9)
    task = ScheduledTask(
        org_id=org.id,
        title="Edited weekly cadence",
        goal="g",
        schedule="weekly",
        timezone="UTC",
        status="pending",
        next_run_at=future,
        last_run_at=stale_last_run,
    )
    session.add(task)
    session.commit()

    with eng.begin() as conn:
        migrate._mig_0003_task_occurrence_ledger(conn)

    occurrences = (
        session.query(TaskOccurrence)
        .filter(TaskOccurrence.task_id == task.id)
        .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
        .all()
    )
    assert [(row.kind, row.due_at) for row in occurrences] == [
        ("scheduled", future.replace(tzinfo=None))
    ]


def test_recurring_input_backfill_keeps_immediate_work_and_cadence(tmp_path, monkeypatch):
    import json
    from datetime import datetime, timedelta, timezone

    from sqlalchemy.orm import sessionmaker

    from anthill.web import scheduler
    from anthill.web.db import Organization, ScheduledTask, TaskOccurrence

    eng = get_engine(tmp_path / "recurring-input.db")
    create_tables(eng)
    session = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    session.add(org)
    session.flush()
    due = datetime.now(timezone.utc) - timedelta(minutes=1)
    task = ScheduledTask(
        org_id=org.id,
        title="Legacy recurring",
        goal="g",
        schedule="daily",
        status="pending",
        next_run_at=due,
        queued_inputs=json.dumps(["include incidents"]),
    )
    session.add(task)
    session.commit()

    with eng.begin() as conn:
        migrate._mig_0003_task_occurrence_ledger(conn)

    occurrences = (
        session.query(TaskOccurrence)
        .filter(TaskOccurrence.task_id == task.id)
        .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
        .all()
    )
    assert [(row.kind, json.loads(row.inputs)) for row in occurrences] == [
        ("queued", ["include incidents"]),
        ("scheduled", []),
    ]
    cadence_due = occurrences[1].due_at
    monkeypatch.setattr(scheduler, "_run_task", lambda task, db: "done")
    monkeypatch.setattr(scheduler, "_verify_task_result", lambda task, result, db: None)
    scheduler._tick(eng)

    session.expire_all()
    next_occurrence = (
        session.query(TaskOccurrence)
        .filter(
            TaskOccurrence.task_id == task.id,
            TaskOccurrence.kind == "scheduled",
            TaskOccurrence.status == "pending",
        )
        .one()
    )
    assert next_occurrence.due_at == cadence_due


def test_future_legacy_weekly_due_is_single_scheduled_cadence(tmp_path):
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    from sqlalchemy.orm import sessionmaker

    from anthill.web.db import Organization, ScheduledTask, TaskOccurrence

    zone = ZoneInfo("Europe/Madrid")
    now = datetime.now(zone)
    created_local = (now - timedelta(days=(now.weekday() - 1) % 7 + 7)).replace(
        hour=14, minute=0, second=0, microsecond=0
    )
    immediate_local = (now + timedelta(days=(4 - now.weekday()) % 7 + 7)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    immediate = immediate_local.astimezone(timezone.utc)

    eng = get_engine(tmp_path / "future-weekly-edit.db")
    create_tables(eng)
    session = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    session.add(org)
    session.flush()
    task = ScheduledTask(
        org_id=org.id,
        title="Legacy edited weekly task",
        goal="g",
        schedule=" WEEKLY ",
        timezone=zone.key,
        status="pending",
        created_at=created_local.astimezone(timezone.utc),
        next_run_at=immediate,
    )
    session.add(task)
    session.commit()

    with eng.begin() as conn:
        migrate._mig_0003_task_occurrence_ledger(conn)

    session.expire_all()
    occurrences = (
        session.query(TaskOccurrence)
        .filter(TaskOccurrence.task_id == task.id)
        .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
        .all()
    )
    migrated_task = session.get(ScheduledTask, task.id)
    cadence_local = occurrences[0].due_at.replace(tzinfo=timezone.utc).astimezone(zone)
    assert [row.kind for row in occurrences] == ["scheduled"]
    assert occurrences[0].due_at == immediate.replace(tzinfo=None)
    assert (cadence_local.weekday(), cadence_local.hour, cadence_local.minute) == (4, 9, 0)
    assert (
        migrated_task.schedule_anchor.weekday(),
        migrated_task.schedule_anchor.hour,
        migrated_task.schedule_anchor.minute,
    ) == (4, 9, 0)
    assert migrated_task.schedule == "weekly"


def test_overdue_legacy_time_preserves_ambiguous_weekly_cadence_for_review(tmp_path):
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    from sqlalchemy.orm import sessionmaker

    from anthill.web.db import Organization, ScheduledTask, TaskOccurrence

    zone = ZoneInfo("Europe/Madrid")
    now = datetime.now(zone)
    immediate_local = (now - timedelta(days=(now.weekday() - 4) % 7 + 7)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    created_local = (immediate_local - timedelta(days=3)).replace(hour=14)
    immediate = immediate_local.astimezone(timezone.utc)

    eng = get_engine(tmp_path / "overdue-weekly-run-now.db")
    create_tables(eng)
    session = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    session.add(org)
    session.flush()
    task = ScheduledTask(
        org_id=org.id,
        title="Legacy overdue run now",
        goal="g",
        schedule="weekly",
        timezone=zone.key,
        status="pending",
        created_at=created_local.astimezone(timezone.utc),
        next_run_at=immediate,
    )
    session.add(task)
    session.commit()

    with eng.begin() as conn:
        migrate._mig_0003_task_occurrence_ledger(conn)

    session.expire_all()
    occurrences = (
        session.query(TaskOccurrence)
        .filter(TaskOccurrence.task_id == task.id)
        .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
        .all()
    )
    migrated_task = session.get(ScheduledTask, task.id)
    cadence_local = occurrences[1].due_at.replace(tzinfo=timezone.utc).astimezone(zone)
    assert [row.kind for row in occurrences] == ["manual", "scheduled"]
    assert occurrences[0].due_at == immediate.replace(tzinfo=None)
    assert (cadence_local.weekday(), cadence_local.hour, cadence_local.minute) == (4, 9, 0)
    assert (
        migrated_task.schedule_anchor.weekday(),
        migrated_task.schedule_anchor.hour,
        migrated_task.schedule_anchor.minute,
    ) == (4, 9, 0)
    assert migrated_task.cadence_needs_review is True
    assert "overdue recurring time" in migrated_task.cadence_review_reason


def test_interrupted_backfill_does_not_rebind_unrelated_history(tmp_path):
    import json
    from datetime import datetime, timedelta, timezone

    from sqlalchemy.orm import sessionmaker

    from anthill.web.db import Organization, ScheduledTask, TaskOccurrence, TaskRun

    eng = get_engine(tmp_path / "interrupted-unrelated-history.db")
    create_tables(eng)
    session = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    session.add(org)
    session.flush()
    interrupted_at = datetime.now(timezone.utc) - timedelta(hours=1)
    task = ScheduledTask(
        org_id=org.id,
        title="Interrupted legacy task",
        goal="g",
        schedule="once",
        status="pending",
        interrupted_run_at=interrupted_at,
        interrupted_inputs=None,
    )
    session.add(task)
    session.flush()
    unrelated = TaskRun(
        org_id=org.id,
        task_id=task.id,
        trigger="manual",
        status="ok",
        claimed_inputs=json.dumps(["unrelated input"]),
        scheduled_for=interrupted_at - timedelta(days=1),
    )
    session.add(unrelated)
    session.commit()

    with eng.begin() as conn:
        migrate._mig_0003_task_occurrence_ledger(conn)

    occurrence = session.query(TaskOccurrence).filter(TaskOccurrence.task_id == task.id).one()
    assert (occurrence.kind, json.loads(occurrence.inputs), occurrence.claimed_run_id) == (
        "scheduled",
        [],
        None,
    )
    assert session.get(TaskRun, unrelated.id).occurrence_id is None


def test_active_recurring_run_now_backfills_as_manual_occurrence(tmp_path):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy.orm import sessionmaker

    from anthill.web.db import Organization, ScheduledTask, TaskOccurrence, TaskRun

    eng = get_engine(tmp_path / "active-run-now.db")
    create_tables(eng)
    session = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    session.add(org)
    session.flush()
    scheduled_for = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    task = ScheduledTask(
        org_id=org.id,
        title="Recurring",
        goal="g",
        schedule="daily",
        status="running",
        last_run_at=scheduled_for,
        next_run_at=scheduled_for + timedelta(hours=1),
    )
    session.add(task)
    session.flush()
    session.add(
        TaskRun(
            org_id=org.id,
            task_id=task.id,
            status="running",
            scheduled_for=scheduled_for,
        )
    )
    session.commit()

    with eng.begin() as conn:
        migrate._mig_0003_task_occurrence_ledger(conn)

    occurrences = (
        session.query(TaskOccurrence)
        .filter(TaskOccurrence.task_id == task.id)
        .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
        .all()
    )
    assert [(row.kind, row.status) for row in occurrences] == [
        ("scheduled", "claimed"),
        ("manual", "pending"),
        ("scheduled", "pending"),
    ]


def test_active_and_equal_time_run_now_backfill_as_distinct_occurrences(tmp_path):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy.orm import sessionmaker

    from anthill.web.db import Organization, ScheduledTask, TaskOccurrence, TaskRun

    claimed_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    eng = get_engine(tmp_path / "active-equal-time-run-now.db")
    create_tables(eng)
    session = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    session.add(org)
    session.flush()
    task = ScheduledTask(
        org_id=org.id,
        title="Legacy equal-time run now",
        goal="g",
        schedule="daily",
        status="running",
        last_run_at=claimed_at,
        next_run_at=claimed_at,
    )
    session.add(task)
    session.flush()
    session.add(
        TaskRun(
            org_id=org.id,
            task_id=task.id,
            status="running",
            scheduled_for=claimed_at,
        )
    )
    session.commit()

    with eng.begin() as conn:
        migrate._mig_0003_task_occurrence_ledger(conn)

    occurrences = (
        session.query(TaskOccurrence)
        .filter(TaskOccurrence.task_id == task.id)
        .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
        .all()
    )
    assert [(row.kind, row.status) for row in occurrences] == [
        ("scheduled", "claimed"),
        ("manual", "pending"),
        ("scheduled", "pending"),
    ]
    assert occurrences[0].due_at == occurrences[1].due_at


def test_task_run_fk_and_task_first_occurrence_index_upgrade_legacy_schema(tmp_path):
    from datetime import datetime

    from sqlalchemy.orm import sessionmaker

    from anthill.web.db import Organization, ScheduledTask, TaskRun

    eng = get_engine(tmp_path / "legacy-task-runs.db")
    create_tables(eng)
    session = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    session.add(org)
    session.flush()
    task = ScheduledTask(
        org_id=org.id,
        title="Legacy",
        goal="g",
        schedule="once",
        status="done",
        created_at=datetime(2026, 9, 1, 9, 0),
    )
    session.add(task)
    session.commit()
    task_id = task.id
    session.close()

    with eng.begin() as conn:
        conn.execute(text("DROP TABLE task_runs"))
        conn.execute(
            text("CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id INTEGER, status VARCHAR)")
        )
        conn.execute(
            text("INSERT INTO task_runs (id,task_id,status) VALUES (17,:task,'error')"),
            {"task": task_id},
        )
        migrate._set_user_version(conn, 3)

    create_tables(eng)

    assert _uv(eng) == migrate.MIGRATIONS[-1][0]  # the head, whatever it is
    assert {column.name for column in TaskRun.__table__.columns} == {
        column["name"] for column in inspect(eng).get_columns("task_runs")
    }
    task_run_fks = inspect(eng).get_foreign_keys("task_runs")
    assert any(
        fk["constrained_columns"] == ["occurrence_id"]
        and fk["referred_table"] == "task_occurrences"
        for fk in task_run_fks
    )
    with eng.connect() as conn:
        assert conn.execute(text("SELECT id,task_id,status FROM task_runs")).one() == (
            17,
            task_id,
            "error",
        )
    occurrence_indexes = inspect(eng).get_indexes("task_occurrences")
    assert any(
        index["column_names"] == ["task_id", "status", "due_at", "id"]
        for index in occurrence_indexes
    )
    task_run_indexes = {index["name"] for index in inspect(eng).get_indexes("task_runs")}
    assert {"ix_task_runs_active_task", "ix_task_runs_recovery"} <= task_run_indexes


def test_task_run_fk_rebuild_quarantines_legacy_orphans(tmp_path):
    import json

    from sqlalchemy.orm import sessionmaker

    from anthill.web.db import Organization, ScheduledTask

    eng = get_engine(tmp_path / "orphan-task-runs.db")
    create_tables(eng)
    session = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    session.add(org)
    session.flush()
    task = ScheduledTask(org_id=org.id, title="T", goal="g", schedule="once", status="done")
    session.add(task)
    session.commit()
    task_id = task.id
    session.close()

    with eng.begin() as conn:
        conn.execute(text("DROP TABLE task_runs"))
        conn.execute(
            text(
                "CREATE TABLE task_runs (id INTEGER PRIMARY KEY, org_id INTEGER, "
                "task_id INTEGER, occurrence_id INTEGER, status VARCHAR)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO task_runs (id,org_id,task_id,occurrence_id,status) VALUES "
                "(1,999,:task,999,'error'),(2,NULL,999,NULL,'error')"
            ),
            {"task": task_id},
        )
        migrate._set_user_version(conn, 3)

    create_tables(eng)

    with eng.connect() as conn:
        rows = conn.execute(
            text("SELECT id,org_id,task_id,occurrence_id FROM task_runs ORDER BY id")
        ).all()
        archived = conn.execute(
            text(
                "SELECT source_run_id,original_task_id,original_org_id,original_row,reason "
                "FROM task_run_archives"
            )
        ).one()
        violations = conn.execute(text("PRAGMA foreign_key_check(task_runs)")).all()
    assert rows == [(1, None, task_id, None)]
    assert archived[:3] == (2, 999, None)
    assert json.loads(archived[3]) == {
        "id": 2,
        "occurrence_id": None,
        "org_id": None,
        "scheduled_for": None,
        "status": "error",
        "task_id": 999,
    }
    assert archived[4] == "missing_scheduled_task_parent"
    assert violations == []


def test_production_migrations_apply_in_order_and_backfill_schema_and_data(tmp_path):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy.orm import sessionmaker

    from anthill.web.db import Organization, ScheduledTask, TaskOccurrence

    eng = get_engine(tmp_path / "production-migrations.db")
    create_tables(eng)
    session = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    session.add(org)
    session.flush()
    task = ScheduledTask(
        org_id=org.id,
        title="Legacy cadence",
        goal="g",
        schedule="daily",
        status="pending",
        next_run_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    session.add(task)
    session.commit()
    task_id = task.id
    session.close()

    with eng.begin() as conn:
        migrate._set_user_version(conn, 2)
    applied = run_migrations(eng, fresh=False, before_change=lambda _names: None)

    assert applied == [m[0] for m in migrate.MIGRATIONS if m[0] > 2]
    assert _uv(eng) == migrate.MIGRATIONS[-1][0]  # the head, whatever it is
    session = sessionmaker(bind=eng)()
    occurrence = session.query(TaskOccurrence).filter_by(task_id=task_id).one()
    assert occurrence.kind == "scheduled"
    assert any(
        fk["constrained_columns"] == ["occurrence_id"]
        and fk["referred_table"] == "task_occurrences"
        for fk in inspect(eng).get_foreign_keys("task_runs")
    )
