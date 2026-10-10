"""Runtime verifier hooked into scheduled-task results (Phase B). After a task runs, its result is
cross-checked against the goal and an advisory verdict is stored; a result that doesn't cover the goal
is flagged 'needs review' (the run still delivers). Spec: RUNTIME_CROSSCHECK_VERIFIER.md."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web import scheduler
from anthill.web.db import Organization, OrgSettings, ScheduledTask, TaskOccurrence, TaskRun


def _session(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    s = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    s.add(OrgSettings(org_id=org.id))
    s.commit()
    return s, org.id


def _task(s, org_id, goal):
    t = ScheduledTask(org_id=org_id, title="job", goal=goal, schedule="once", status="done")
    s.add(t)
    s.flush()
    return t


def test_memory_flush_failure_does_not_poison_task_outcome_transaction(tmp_path, monkeypatch):
    from sqlalchemy import event

    import anthill.memory as memory
    import anthill.web.memory_ops as memory_ops
    from anthill.web.db import MemoryItem, User

    session, org_id = _session(tmp_path)
    user = User(org_id=org_id, email="memory@example.com", display_name="User")
    session.add(user)
    session.flush()
    task = ScheduledTask(
        org_id=org_id,
        created_by=user.id,
        title="Remembered task",
        goal="g",
        schedule="once",
        status="done",
        last_result="published result",
    )
    session.add(task)
    monkeypatch.setattr(memory_ops, "auto_memory_on", lambda *_args: True)
    monkeypatch.setattr(memory, "embed_text", lambda _text: [1.0])
    monkeypatch.setattr(memory, "is_new", lambda *_args: True)
    monkeypatch.setattr(memory, "is_semantically_new", lambda *_args: True)
    monkeypatch.setattr(memory, "encode_vec", lambda _vec: "encoded")

    def fail_memory_flush(db, _flush_context, _instances):
        if any(isinstance(row, MemoryItem) for row in db.new):
            raise RuntimeError("memory flush failed")

    event.listen(session, "before_flush", fail_memory_flush)
    try:
        scheduler._remember_task_outcome(session, task, "published result")
    finally:
        event.remove(session, "before_flush", fail_memory_flush)

    task.run_count = 1
    session.commit()
    session.expire_all()
    assert session.get(ScheduledTask, task.id).last_result == "published result"
    assert session.get(ScheduledTask, task.id).run_count == 1
    assert session.query(MemoryItem).count() == 0


@pytest.mark.parametrize("commit_fails", [False, True])
def test_memory_promotion_push_waits_for_successful_outcome_commit(
    tmp_path, monkeypatch, commit_fails
):
    s, org_id = _session(tmp_path)
    task = _task(s, org_id, "summarise deploys")
    task.status = "pending"
    task.occurrences_materialized = True
    occurrence = TaskOccurrence(
        org_id=org_id,
        task_id=task.id,
        kind="manual",
        status="pending",
        due_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        inputs="[]",
    )
    s.add(occurrence)
    s.commit()

    worker = sessionmaker(bind=s.get_bind())()
    original_commit = worker.commit
    commits = 0

    def fail_outcome_commit():
        nonlocal commits
        commits += 1
        if commit_fails and commits == 2:
            raise RuntimeError("outcome commit failed")
        original_commit()

    worker.commit = fail_outcome_commit
    notice = ({1, 2}, "shared fact", "org", 2)
    dispatched = []
    monkeypatch.setattr(scheduler, "sessionmaker", lambda bind=None: lambda: worker)
    monkeypatch.setattr(scheduler, "_run_task", lambda task, db: "done")
    monkeypatch.setattr(scheduler, "_verify_task_result", lambda task, result, db: None)
    monkeypatch.setattr(scheduler, "_remember_task_outcome", lambda db, task, result: [notice])
    monkeypatch.setattr(
        scheduler, "_dispatch_memory_notices", lambda db, notices: dispatched.extend(notices)
    )

    if commit_fails:

        class StopTick(RuntimeError):
            pass

        def stop_after_failed_commit(*_args, **_kwargs):
            raise StopTick

        monkeypatch.setattr(scheduler, "_close_unpublished_task_run", stop_after_failed_commit)
        with pytest.raises(StopTick):
            scheduler._tick(s.get_bind())
        worker.rollback()
        worker.close()
    else:
        scheduler._tick(s.get_bind())

    assert dispatched == ([] if commit_fails else [notice])
    if not commit_fails:
        s.expire_all()
        run = s.query(TaskRun).filter(TaskRun.task_id == task.id).one()
        assert run.status == "ok"


def test_off_goal_result_is_flagged_needs_review(tmp_path):
    # real verify() on the deterministic path (no Ollama installed -> no cross-check): a longer result
    # that echoes none of the goal fails the goal-match check and is flagged.
    s, org_id = _session(tmp_path)
    t = _task(s, org_id, "summarise this week's production deploys and incidents")
    scheduler._verify_task_result(
        t,
        "The garden looked lovely in the morning light, with dew on every leaf and birds calling "
        "from the old oak. I walked slowly along the path, admiring the roses, and thought about "
        "nothing in particular except how pleasant a quiet start to any given day can feel.",
        s,
    )
    assert t.verify_needs_review is True
    assert t.verify_confidence == "0.90"  # a failed deterministic check -> high-confidence-bad
    assert "goal_match" in t.verify_reason


def test_short_generative_result_not_hard_failed(tmp_path):
    # issue #393: a valid short generative result (no Ollama -> no cross-check) is surfaced for
    # review but must NOT be recorded as a high-confidence deterministic failure.
    s, org_id = _session(tmp_path)
    t = _task(s, org_id, "Write one sentence with a practical tip for effective teamwork.")
    scheduler._verify_task_result(
        t, "Tip: Always communicate your progress and blockers to keep everyone aligned.", s
    )
    assert t.verify_needs_review is True  # still surfaced (no independent verifier available)...
    assert t.verify_confidence == "0.50"  # ...but advisory, not a 0.90 deterministic failure
    assert "goal_match" not in (t.verify_reason or "")


def test_clean_verdict_is_stored_and_not_flagged(tmp_path, monkeypatch):
    import anthill.verify as verify_pkg

    s, org_id = _session(tmp_path)
    t = _task(s, org_id, "summarise deploys")
    monkeypatch.setattr(
        verify_pkg,
        "verify",
        lambda *a, **k: verify_pkg.Verdict(
            ok=True, confidence=0.85, reason="covers the deploys", needs_review=False
        ),
    )
    scheduler._verify_task_result(t, "This week: 4 deploys, all green.", s)
    assert t.verify_needs_review is False
    assert t.verify_reason == "covers the deploys" and t.verify_confidence == "0.85"


def test_new_task_run_clears_old_verifier_state_and_one_shot_schedule(tmp_path, monkeypatch):
    import anthill.verify as verify_pkg

    s, org_id = _session(tmp_path)
    t = _task(s, org_id, "summarise deploys")
    t.status = "pending"
    t.next_run_at = datetime.now(timezone.utc)
    t.verify_needs_review = True
    t.verify_reason = "old run reason"
    t.verify_confidence = "0.90"
    t.cadence_needs_review = True
    t.cadence_review_reason = "verify migrated cadence"
    s.commit()
    monkeypatch.setattr(scheduler, "_run_task", lambda task, db: "This week: 4 deploys, all green.")
    monkeypatch.setattr(
        verify_pkg,
        "verify",
        lambda *a, **k: verify_pkg.Verdict(
            ok=True, confidence=0.85, reason="current run", needs_review=False
        ),
    )

    scheduler._tick(s.get_bind())
    fresh = s.query(ScheduledTask).filter(ScheduledTask.id == t.id).one()
    assert fresh.status == "done"
    assert fresh.next_run_at is None
    assert fresh.verify_needs_review is False
    assert fresh.verify_reason == "current run"
    assert fresh.cadence_needs_review is True
    assert fresh.cadence_review_reason == "verify migrated cadence"

    scheduler._tick(s.get_bind())
    assert s.query(ScheduledTask).filter(ScheduledTask.id == t.id).one().run_count == 1


def test_verifier_state_is_reset_between_occurrences_in_one_tick(tmp_path, monkeypatch):
    s, org_id = _session(tmp_path)
    task = _task(s, org_id, "summarise deploys")
    task.status = "pending"
    due = datetime.now(timezone.utc) - timedelta(minutes=2)
    s.add_all(
        [
            TaskOccurrence(
                org_id=org_id,
                task_id=task.id,
                kind="manual",
                status="pending",
                due_at=due,
                inputs="[]",
            ),
            TaskOccurrence(
                org_id=org_id,
                task_id=task.id,
                kind="manual",
                status="pending",
                due_at=due + timedelta(minutes=1),
                inputs="[]",
            ),
        ]
    )
    s.commit()
    calls = 0

    def run(current, _db):
        nonlocal calls
        calls += 1
        if calls == 1:
            scheduler._flag_task_for_review(current, "first occurrence needs review")
        return f"result {calls}"

    monkeypatch.setattr(scheduler, "_run_task", run)
    monkeypatch.setattr(scheduler, "_verify_task_result", lambda task, result, db: None)

    scheduler._tick(s.get_bind())

    runs = s.query(TaskRun).filter(TaskRun.task_id == task.id).order_by(TaskRun.id).all()
    assert [(run.verify_needs_review, run.verify_reason) for run in runs] == [
        (True, "first occurrence needs review"),
        (False, ""),
    ]
    s.expire_all()
    assert s.get(ScheduledTask, task.id).verify_needs_review is False


def test_recurring_task_reschedules_in_its_timezone(tmp_path, monkeypatch):
    from zoneinfo import ZoneInfo

    s, org_id = _session(tmp_path)
    t = _task(s, org_id, "summarise deploys")
    t.schedule = "09:00"
    t.timezone = "America/New_York"
    t.status = "pending"
    t.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    s.commit()
    monkeypatch.setattr(scheduler, "_run_task", lambda task, db: "done")
    monkeypatch.setattr(scheduler, "_verify_task_result", lambda task, result, db: None)

    scheduler._tick(s.get_bind())

    fresh = s.query(ScheduledTask).filter(ScheduledTask.id == t.id).one()
    assert fresh.status == "pending"
    assert (
        fresh.next_run_at.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(fresh.timezone)).hour
        == 9
    )


def test_delayed_tick_keeps_daily_task_calendar_time(tmp_path, monkeypatch):
    from zoneinfo import ZoneInfo

    s, org_id = _session(tmp_path)
    t = _task(s, org_id, "summarise deploys")
    due_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(second=0, microsecond=0)
    t.schedule = "daily"
    t.timezone = "Europe/Madrid"
    t.status = "pending"
    t.next_run_at = due_at
    s.commit()
    monkeypatch.setattr(scheduler, "_run_task", lambda task, db: "done")
    monkeypatch.setattr(scheduler, "_verify_task_result", lambda task, result, db: None)

    scheduler._tick(s.get_bind())

    fresh = s.query(ScheduledTask).filter(ScheduledTask.id == t.id).one()
    expected = due_at.astimezone(ZoneInfo(t.timezone)) + timedelta(days=1)
    actual = fresh.next_run_at.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(t.timezone))
    assert actual == expected


@pytest.mark.parametrize(
    ("schedule", "expected"),
    [
        ("daily", datetime(2026, 3, 9, 6, 30)),
        ("weekly", datetime(2026, 3, 15, 6, 30)),
    ],
)
def test_task_keeps_wall_clock_anchor_after_spring_gap(tmp_path, monkeypatch, schedule, expected):
    due_at = datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return due_at + timedelta(minutes=1)

    s, org_id = _session(tmp_path)
    t = _task(s, org_id, "summarise deploys")
    t.schedule = schedule
    t.timezone = "America/New_York"
    t.schedule_anchor = datetime(2026, 3, 1, 2, 30)
    t.status = "pending"
    t.next_run_at = due_at
    s.commit()
    monkeypatch.setattr(scheduler, "datetime", Clock)
    monkeypatch.setattr(scheduler, "_run_task", lambda task, db: "done")
    monkeypatch.setattr(scheduler, "_verify_task_result", lambda task, result, db: None)

    scheduler._tick(s.get_bind())

    fresh = s.query(ScheduledTask).filter(ScheduledTask.id == t.id).one()
    assert fresh.next_run_at == expected
    assert fresh.schedule_anchor == datetime(2026, 3, 1, 2, 30)


def test_long_running_task_advances_recurrence_past_completion(tmp_path, monkeypatch):
    start = datetime(2026, 8, 23, 10, 0, tzinfo=timezone.utc)

    class Clock(datetime):
        current = start

        @classmethod
        def now(cls, tz=None):
            return cls.current

    s, org_id = _session(tmp_path)
    t = _task(s, org_id, "summarise deploys")
    t.schedule = "hourly"
    t.status = "pending"
    t.next_run_at = start
    s.commit()

    def finish_late(task, db):
        Clock.current = start + timedelta(hours=2, minutes=30)
        return "done"

    monkeypatch.setattr(scheduler, "datetime", Clock)
    monkeypatch.setattr(scheduler, "_run_task", finish_late)
    monkeypatch.setattr(scheduler, "_verify_task_result", lambda task, result, db: None)

    scheduler._tick(s.get_bind())

    fresh = s.query(ScheduledTask).filter(ScheduledTask.id == t.id).one()
    assert fresh.next_run_at == start.replace(tzinfo=None) + timedelta(hours=3)


def test_only_one_scheduler_can_claim_a_due_task(tmp_path):
    s, org_id = _session(tmp_path)
    t = _task(s, org_id, "summarise deploys")
    t.status = "pending"
    t.next_run_at = datetime.now(timezone.utc)
    s.commit()

    other = sessionmaker(bind=s.get_bind())()
    assert other.query(ScheduledTask.id).filter(ScheduledTask.id == t.id).one()
    claimed = scheduler._claim_task(s, t.id, datetime.now(timezone.utc))
    assert claimed is not None
    s.commit()
    assert scheduler._claim_task(other, t.id, datetime.now(timezone.utc)) is None


def test_claim_preserves_legacy_calendar_anchor_before_clearing_due_time(tmp_path):
    from zoneinfo import ZoneInfo

    s, org_id = _session(tmp_path)
    t = _task(s, org_id, "summarise deploys")
    due_at = datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)
    manual_at = datetime(2026, 8, 25, 15, 0, tzinfo=timezone.utc)
    t.schedule = "weekly"
    t.timezone = ""
    t.schedule_anchor = None
    t.status = "pending"
    t.next_run_at = due_at
    s.commit()

    claimed = scheduler._claim_task(s, t.id, manual_at)

    assert claimed is not None
    assert claimed.next_run_at is None
    assert claimed.schedule_anchor == due_at.replace(tzinfo=None)
    next_run_at = scheduler._next_run(
        claimed.schedule,
        from_dt=manual_at,
        timezone_name="America/New_York",
        schedule_anchor=claimed.schedule_anchor,
    )
    next_local = next_run_at.astimezone(ZoneInfo("America/New_York"))
    assert (next_local.weekday(), next_local.hour, next_local.minute) == (0, 9, 0)


def test_claim_skips_a_task_with_an_active_run(tmp_path):
    s, org_id = _session(tmp_path)
    t = _task(s, org_id, "summarise deploys")
    t.status = "pending"
    t.next_run_at = datetime.now(timezone.utc)
    s.add(TaskRun(org_id=org_id, task_id=t.id, status="running"))
    s.commit()

    assert scheduler._claim_task(s, t.id, datetime.now(timezone.utc)) is None
    fresh = s.query(ScheduledTask).filter(ScheduledTask.id == t.id).one()
    assert fresh.status == "pending"


def test_start_scheduler_is_idempotent_before_startup_sweep(monkeypatch):
    starts = []
    sweeps = []

    class FakeThread:
        def __init__(self, **kwargs):
            self.alive = False

        def start(self):
            self.alive = True
            starts.append(self)

        def is_alive(self):
            return self.alive

    from anthill.web import imap_idle

    monkeypatch.setattr(scheduler, "_scheduler_thread", None)
    monkeypatch.setattr(scheduler, "_sweep_stale_running_runs", sweeps.append)
    monkeypatch.setattr(scheduler.threading, "Thread", FakeThread)
    monkeypatch.setattr(imap_idle, "start_worker", lambda engine: None)
    engine = object()

    first = scheduler.start_scheduler(engine)
    second = scheduler.start_scheduler(engine)

    assert first is second
    assert sweeps == [engine]
    assert starts == [first]


@pytest.mark.parametrize(
    ("initial_schedule", "edited_schedule", "expected_status", "has_next_run"),
    [
        ("hourly", "once", "done", False),
        ("once", "hourly", "pending", True),
        ("hourly", "hourly", "pending", True),
    ],
)
def test_running_schedule_edits_determine_the_next_run(
    tmp_path,
    monkeypatch,
    initial_schedule,
    edited_schedule,
    expected_status,
    has_next_run,
):
    import anthill.verify as verify_pkg

    s, org_id = _session(tmp_path)
    t = _task(s, org_id, "summarise deploys")
    t.schedule = initial_schedule
    t.status = "pending"
    t.next_run_at = datetime.now(timezone.utc)
    s.commit()

    def _run_and_edit(task, db):
        other = sessionmaker(bind=db.get_bind())()
        current = other.query(ScheduledTask).filter(ScheduledTask.id == task.id).one()
        current.schedule = edited_schedule
        other.commit()
        other.close()
        return "This week: 4 deploys, all green."

    monkeypatch.setattr(scheduler, "_run_task", _run_and_edit)
    monkeypatch.setattr(
        verify_pkg,
        "verify",
        lambda *a, **k: verify_pkg.Verdict(
            ok=True, confidence=0.85, reason="current run", needs_review=False
        ),
    )

    scheduler._tick(s.get_bind())
    fresh_session = sessionmaker(bind=s.get_bind())()
    fresh = fresh_session.query(ScheduledTask).filter(ScheduledTask.id == t.id).one()
    assert fresh.schedule == edited_schedule
    assert fresh.status == expected_status
    assert (fresh.next_run_at is not None) is has_next_run
    assert fresh.run_count == 1


def test_run_again_during_one_shot_run_is_preserved(tmp_path, monkeypatch):
    import anthill.verify as verify_pkg

    s, org_id = _session(tmp_path)
    t = _task(s, org_id, "summarise deploys")
    t.status = "pending"
    t.next_run_at = datetime.now(timezone.utc)
    s.commit()

    def _run_and_requeue(task, db):
        from anthill.web import task_occurrences

        other = sessionmaker(bind=db.get_bind())()
        queued = other.query(ScheduledTask).filter(ScheduledTask.id == task.id).one()
        assert queued.status == "running"
        task_occurrences.run_now(other, queued, datetime.now(timezone.utc))
        other.commit()
        other.close()
        return "This week: 4 deploys, all green."

    monkeypatch.setattr(scheduler, "_run_task", _run_and_requeue)
    monkeypatch.setattr(
        verify_pkg,
        "verify",
        lambda *a, **k: verify_pkg.Verdict(
            ok=True, confidence=0.85, reason="current run", needs_review=False
        ),
    )

    scheduler._tick(s.get_bind())
    fresh = s.query(ScheduledTask).filter(ScheduledTask.id == t.id).one()
    assert fresh.status == "pending"
    assert fresh.next_run_at is not None
    assert fresh.run_count == 1


def test_follow_up_queued_during_run_is_not_overwritten(tmp_path, monkeypatch):
    import json

    import anthill.verify as verify_pkg

    s, org_id = _session(tmp_path)
    t = _task(s, org_id, "summarise deploys")
    t.status = "pending"
    t.next_run_at = datetime.now(timezone.utc)
    t.queued_inputs = json.dumps(["include incidents"])
    s.commit()

    def _run_and_queue(task, db):
        from anthill.web import task_occurrences

        goal = scheduler._effective_goal(task)
        assert "include incidents" in goal
        other = sessionmaker(bind=db.get_bind())()
        queued = other.query(ScheduledTask).filter(ScheduledTask.id == task.id).one()
        assert json.loads(queued.queued_inputs) == ["include incidents"]
        task_occurrences.queue_input(
            other, queued, "also include mitigations", datetime.now(timezone.utc)
        )
        other.commit()
        other.close()
        return "This week: 4 deploys, all green."

    monkeypatch.setattr(scheduler, "_run_task", _run_and_queue)
    monkeypatch.setattr(
        verify_pkg,
        "verify",
        lambda *a, **k: verify_pkg.Verdict(
            ok=True, confidence=0.85, reason="current run", needs_review=False
        ),
    )

    scheduler._tick(s.get_bind())
    fresh = s.query(ScheduledTask).filter(ScheduledTask.id == t.id).one()
    assert fresh.status == "pending"
    assert json.loads(fresh.queued_inputs) == ["also include mitigations"]


def test_tick_rearms_a_run_left_open_after_finalization_failure(tmp_path, monkeypatch):
    import json

    s, org_id = _session(tmp_path)
    recovered_at = datetime.now(timezone.utc)
    scheduled_for = recovered_at - timedelta(hours=1)
    started_at = recovered_at - scheduler._ORPHAN_TASK_AGE - timedelta(seconds=1)
    t = _task(s, org_id, "summarise deploys")
    t.status = "running"
    t.next_run_at = None
    s.add(
        TaskRun(
            org_id=org_id,
            task_id=t.id,
            status="running",
            scheduled_for=scheduled_for,
            started_at=started_at,
        )
    )
    queued_for = recovered_at - timedelta(minutes=30)
    queued = _task(s, org_id, "queued rerun")
    queued.status = "running"
    queued.next_run_at = queued_for
    queued.queued_inputs = json.dumps(["for interrupted run", "for queued rerun"])
    s.add(
        TaskRun(
            org_id=org_id,
            task_id=queued.id,
            status="running",
            scheduled_for=scheduled_for,
            claimed_inputs=json.dumps(["for interrupted run"]),
            started_at=started_at,
        )
    )
    s.commit()

    scheduler._recover_orphaned_task_runs(s, recovered_at)

    fresh = s.query(ScheduledTask).filter(ScheduledTask.id == t.id).one()
    fresh_queued = s.query(ScheduledTask).filter(ScheduledTask.id == queued.id).one()
    run = s.query(TaskRun).filter(TaskRun.task_id == t.id).one()
    assert fresh.status == "pending"
    assert fresh.next_run_at == scheduled_for.replace(tzinfo=None)
    assert fresh.interrupted_run_at == scheduled_for.replace(tzinfo=None)
    assert fresh_queued.status == "pending"
    assert fresh_queued.interrupted_run_at == scheduled_for.replace(tzinfo=None)
    assert fresh_queued.next_run_at == scheduled_for.replace(tzinfo=None)
    assert run.status == "error" and "finalization" in run.error

    seen = []
    monkeypatch.setattr(
        scheduler,
        "_run_task",
        lambda task, db: seen.append((task.goal, task._scheduled_for)) or "done",
    )
    monkeypatch.setattr(scheduler, "_verify_task_result", lambda task, result, db: None)

    scheduler._tick(s.get_bind())
    s.expire_all()
    fresh_queued = s.query(ScheduledTask).filter(ScheduledTask.id == queued.id).one()
    assert fresh_queued.status == "done"
    assert fresh_queued.interrupted_run_at is None
    assert fresh_queued.next_run_at is None
    assert json.loads(fresh_queued.queued_inputs) == []
    queued_occurrences = [occurrence for goal, occurrence in seen if goal == "queued rerun"]
    assert queued_occurrences == [scheduled_for, queued_for]


def test_startup_restores_inference_before_starting_scheduler(monkeypatch):
    import anthill.web.app as app_mod

    calls = []
    monkeypatch.setattr(app_mod, "_scheduler_started", False)
    monkeypatch.setattr(app_mod, "_engine", object())
    for name, label in (
        ("_reap_aws_orphans", "reap"),
        ("_autostart_remote_access", "remote"),
        ("_autostart_llm_tunnels", "llm"),
        ("_autostart_local_serving", "mlx"),
        ("_migrate_legacy_personal_wiki", "migrate"),
        ("_backfill_knowledge_registry", "backfill"),
    ):
        monkeypatch.setattr(app_mod, name, lambda label=label: calls.append(label))
    monkeypatch.setattr(scheduler, "start_scheduler", lambda engine: calls.append("scheduler"))

    app_mod._startup()

    assert calls == ["reap", "remote", "llm", "mlx", "migrate", "backfill", "scheduler"]


def test_stale_task_run_rearms_the_task_after_restart(tmp_path, monkeypatch):
    import json

    s, org_id = _session(tmp_path)
    t = _task(s, org_id, "summarise deploys")
    t.status = "running"
    t.next_run_at = None
    scheduled_for = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)
    s.add(
        TaskRun(
            org_id=org_id,
            task_id=t.id,
            status="running",
            scheduled_for=scheduled_for,
        )
    )
    migration_occurrence = datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc)
    queued_for = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)
    queued = _task(s, org_id, "queued rerun")
    queued.schedule = "weekly"
    queued.timezone = ""
    queued.schedule_anchor = None
    queued.status = "running"
    queued.last_run_at = migration_occurrence
    queued.next_run_at = queued_for
    queued.queued_inputs = json.dumps(["claimed before migration"])
    s.add(
        TaskRun(
            org_id=org_id,
            task_id=queued.id,
            status="running",
            scheduled_for=None,
            started_at=migration_occurrence + timedelta(minutes=1),
        )
    )
    s.commit()

    scheduler._sweep_stale_running_runs(s.get_bind())

    fresh_session = sessionmaker(bind=s.get_bind())()
    fresh = fresh_session.query(ScheduledTask).filter(ScheduledTask.id == t.id).one()
    fresh_queued = fresh_session.query(ScheduledTask).filter(ScheduledTask.id == queued.id).one()
    stale = fresh_session.query(TaskRun).filter(TaskRun.task_id == t.id).one()
    migrated_run = fresh_session.query(TaskRun).filter(TaskRun.task_id == queued.id).one()
    assert fresh.status == "pending"
    assert fresh.next_run_at == scheduled_for.replace(tzinfo=None)
    assert fresh.interrupted_run_at == scheduled_for.replace(tzinfo=None)
    assert fresh_queued.status == "pending"
    assert fresh_queued.interrupted_run_at == migration_occurrence.replace(tzinfo=None)
    assert fresh_queued.next_run_at == migration_occurrence.replace(tzinfo=None)
    assert fresh_queued.schedule_anchor == migration_occurrence.replace(tzinfo=None)
    assert migrated_run.scheduled_for == migration_occurrence.replace(tzinfo=None)
    assert migrated_run.claimed_inputs == ""
    assert stale.status == "error" and "interrupted" in stale.error

    seen = []

    def _run(task, db):
        seen.append((task._scheduled_for, scheduler._effective_goal(task)))
        return "done"

    monkeypatch.setattr(scheduler, "_run_task", _run)
    monkeypatch.setattr(scheduler, "_verify_task_result", lambda task, result, db: None)
    scheduler._tick(s.get_bind())
    first_retry = fresh_session.get(ScheduledTask, queued.id)
    fresh_session.refresh(first_retry)
    assert json.loads(first_retry.queued_inputs) == []

    queued_runs = [
        (occurrence, goal)
        for occurrence, goal in seen
        if occurrence in {migration_occurrence, queued_for}
    ]
    assert [occurrence for occurrence, _ in queued_runs] == [migration_occurrence, queued_for]
    assert "claimed before migration" not in queued_runs[0][1]
    assert "claimed before migration" in queued_runs[1][1]


def test_stale_attempt_cannot_finalize_an_occurrence_after_retry(tmp_path):
    from anthill.web import task_occurrences

    s, org_id = _session(tmp_path)
    task = _task(s, org_id, "summarise deploys")
    task.status = "pending"
    due = datetime.now(timezone.utc) - timedelta(hours=1)
    occurrence = TaskOccurrence(
        org_id=org_id,
        task_id=task.id,
        kind="scheduled",
        status="pending",
        due_at=due,
        inputs="[]",
    )
    s.add(occurrence)
    s.commit()

    first = task_occurrences.claim(s, task.id, datetime.now(timezone.utc))
    assert first is not None
    s.commit()
    task_occurrences.recover_run(s, first.run, datetime.now(timezone.utc))
    s.commit()
    retry = task_occurrences.claim(s, task.id, datetime.now(timezone.utc))
    assert retry is not None
    s.commit()

    assert (
        task_occurrences.finalize(s, occurrence.id, first.run.id, outcome="done", next_due_at=None)
        is False
    )
    fresh = s.get(TaskOccurrence, occurrence.id)
    assert fresh.status == "claimed"
    assert fresh.claimed_run_id == retry.run.id


def test_cancel_blocks_later_claim_and_cancels_claim_that_already_won(tmp_path):
    from anthill.web import task_occurrences

    s, org_id = _session(tmp_path)
    task = _task(s, org_id, "summarise deploys")
    task.status = "pending"
    occurrence = TaskOccurrence(
        org_id=org_id,
        task_id=task.id,
        kind="scheduled",
        status="pending",
        due_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        inputs="[]",
    )
    s.add(occurrence)
    s.commit()

    # The first cancellation CAS has committed, but its ledger cleanup has not yet run.
    s.query(ScheduledTask).filter(
        ScheduledTask.id == task.id, ScheduledTask.status == "pending"
    ).update({ScheduledTask.status: "cancelled"}, synchronize_session=False)
    s.commit()
    contender = sessionmaker(bind=s.get_bind())()
    assert task_occurrences.claim(contender, task.id, datetime.now(timezone.utc)) is None
    contender.rollback()

    # The opposite serialization is also safe: cancellation owns and closes a claim that won first.
    s.query(ScheduledTask).filter(ScheduledTask.id == task.id).update(
        {ScheduledTask.status: "pending"}, synchronize_session=False
    )
    s.commit()
    winner = task_occurrences.claim(s, task.id, datetime.now(timezone.utc))
    assert winner is not None
    s.commit()
    canceller = sessionmaker(bind=s.get_bind())()
    task_occurrences.cancel(canceller, canceller.get(ScheduledTask, task.id))
    canceller.commit()

    s.expire_all()
    assert s.get(ScheduledTask, task.id).status == "cancelled"
    assert s.get(TaskOccurrence, occurrence.id).status == "cancelled"
    assert s.get(TaskRun, winner.run.id).cancel_requested is True


def test_finalization_uses_schedule_committed_before_ownership_cas(tmp_path, monkeypatch):
    from sqlalchemy import event

    s, org_id = _session(tmp_path)
    task = _task(s, org_id, "summarise deploys")
    due_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    task.schedule = "daily"
    task.timezone = "UTC"
    task.status = "pending"
    task.occurrences_materialized = True
    occurrence = TaskOccurrence(
        org_id=org_id,
        task_id=task.id,
        kind="scheduled",
        status="pending",
        due_at=due_at,
        inputs="[]",
    )
    s.add(occurrence)
    s.commit()
    raced = False

    def edit_before_finalization_cas(_conn, _cursor, statement, parameters, _context, _many):
        nonlocal raced
        if (
            raced
            or not statement.lstrip().startswith("UPDATE task_occurrences SET status")
            or "completed" not in str(parameters)
        ):
            return
        raced = True
        other = sessionmaker(bind=s.get_bind())()
        other.query(ScheduledTask).filter(ScheduledTask.id == task.id).update(
            {
                ScheduledTask.schedule: "once",
                ScheduledTask.schedule_anchor: None,
            },
            synchronize_session=False,
        )
        other.commit()
        other.close()

    event.listen(s.get_bind(), "before_cursor_execute", edit_before_finalization_cas)
    try:
        monkeypatch.setattr(scheduler, "_run_task", lambda task, db: "done")
        monkeypatch.setattr(scheduler, "_verify_task_result", lambda task, result, db: None)
        scheduler._tick(s.get_bind())
    finally:
        event.remove(s.get_bind(), "before_cursor_execute", edit_before_finalization_cas)

    s.expire_all()
    assert raced
    assert s.get(ScheduledTask, task.id).schedule == "once"
    assert (
        s.query(TaskOccurrence)
        .filter(
            TaskOccurrence.task_id == task.id,
            TaskOccurrence.kind == "scheduled",
            TaskOccurrence.status.in_(("pending", "paused", "interrupted", "claimed")),
        )
        .count()
        == 0
    )


def test_finalization_rolls_back_ownership_when_cadence_calculation_fails(tmp_path):
    from anthill.web import task_occurrences

    s, org_id = _session(tmp_path)
    task = _task(s, org_id, "summarise deploys")
    due_at = datetime.now(timezone.utc)
    task.schedule = "daily"
    task.status = "running"
    task.occurrences_materialized = True
    occurrence = TaskOccurrence(
        org_id=org_id,
        task_id=task.id,
        kind="scheduled",
        status="claimed",
        due_at=due_at,
        inputs="[]",
    )
    s.add(occurrence)
    s.flush()
    run = TaskRun(
        org_id=org_id,
        task_id=task.id,
        occurrence_id=occurrence.id,
        status="running",
        scheduled_for=due_at,
    )
    s.add(run)
    s.flush()
    occurrence.claimed_run_id = run.id
    s.commit()

    def fail_cadence(_task, _due_at):
        raise ValueError("invalid cadence")

    with pytest.raises(ValueError, match="invalid cadence"):
        task_occurrences.finalize(
            s,
            occurrence.id,
            run.id,
            outcome="done",
            calculate_next_due=fail_cadence,
        )

    s.expire_all()
    assert s.get(TaskOccurrence, occurrence.id).status == "claimed"


def test_tick_rolls_back_stale_execution_state_before_closing_history(tmp_path, monkeypatch):
    from anthill.web import task_occurrences
    from anthill.web.db import MemoryItem

    s, org_id = _session(tmp_path)
    task = _task(s, org_id, "summarise deploys")
    task.status = "pending"
    task.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    task.verify_needs_review = True
    task.verify_reason = "previous result needs review"
    task.verify_confidence = "0.87"
    s.commit()

    def cancel_while_running(task, db):
        other = sessionmaker(bind=db.get_bind())()
        current = other.get(ScheduledTask, task.id)
        task_occurrences.cancel(other, current)
        other.commit()
        other.close()
        task.verify_needs_review = True
        task.verify_reason = "stale verifier"
        task.verify_confidence = "0.99"
        db.add(
            MemoryItem(
                org_id=task.org_id,
                scope="personal",
                text="stale task memory",
                source="task",
                source_id=task.id,
            )
        )
        return "stale result must not publish"

    monkeypatch.setattr(scheduler, "_run_task", cancel_while_running)
    monkeypatch.setattr(
        scheduler,
        "_verify_task_result",
        lambda task, result, db: pytest.fail("stale result reached the verifier"),
    )

    scheduler._tick(s.get_bind())

    s.expire_all()
    fresh = s.get(ScheduledTask, task.id)
    run = s.query(TaskRun).filter(TaskRun.task_id == task.id).one()
    assert fresh.status == "cancelled"
    assert fresh.last_result is None
    assert fresh.run_count == 0
    assert fresh.verify_needs_review is True
    assert fresh.verify_reason == "previous result needs review"
    assert fresh.verify_confidence == "0.87"
    assert s.query(MemoryItem).filter(MemoryItem.text == "stale task memory").count() == 0
    assert run.status == "error"
    assert run.result == ""
    assert run.error == "Cancelled while it was running. Its result was not kept."


def test_claim_cas_retries_when_earlier_paused_work_is_resumed_meanwhile(tmp_path):
    from sqlalchemy import event

    from anthill.web import task_occurrences

    s, org_id = _session(tmp_path)
    due_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    later = _task(s, org_id, "later")
    earlier = _task(s, org_id, "earlier")
    later.status = "pending"
    earlier.status = "cancelled"
    later.occurrences_materialized = True
    earlier.occurrences_materialized = True
    later_occurrence = TaskOccurrence(
        org_id=org_id,
        task_id=later.id,
        kind="scheduled",
        status="pending",
        due_at=due_at + timedelta(minutes=2),
        inputs="[]",
    )
    earlier_occurrence = TaskOccurrence(
        org_id=org_id,
        task_id=earlier.id,
        kind="scheduled",
        status="paused",
        due_at=due_at,
        inputs="[]",
    )
    s.add_all([later_occurrence, earlier_occurrence])
    s.commit()
    raced = False

    def reactivate_before_claim_cas(_conn, _cursor, statement, parameters, _context, _many):
        nonlocal raced
        if (
            raced
            or not statement.lstrip().startswith("UPDATE task_occurrences SET status")
            or "claimed" not in str(parameters)
        ):
            return
        raced = True
        # Another request resumes the earlier paused work between the claim's read and its update.
        other = sessionmaker(bind=s.get_bind())()
        other.query(TaskOccurrence).filter(TaskOccurrence.id == earlier_occurrence.id).update(
            {TaskOccurrence.status: "pending"}
        )
        other.query(ScheduledTask).filter(ScheduledTask.id == earlier.id).update(
            {ScheduledTask.status: "pending"}
        )
        other.commit()
        other.close()

    event.listen(s.get_bind(), "before_cursor_execute", reactivate_before_claim_cas)
    try:
        claimed = task_occurrences.claim_next(s, datetime.now(timezone.utc))
    finally:
        event.remove(s.get_bind(), "before_cursor_execute", reactivate_before_claim_cas)

    assert raced
    assert claimed is not None
    assert claimed.task.id == earlier.id
    assert claimed.occurrence.id == earlier_occurrence.id


def test_tick_claims_due_occurrences_in_global_order(tmp_path, monkeypatch):
    s, org_id = _session(tmp_path)
    base = datetime.now(timezone.utc) - timedelta(minutes=5)
    task_a = _task(s, org_id, "A")
    task_b = _task(s, org_id, "B")
    for task in (task_a, task_b):
        task.status = "pending"
    s.add_all(
        [
            TaskOccurrence(
                org_id=org_id,
                task_id=task_a.id,
                kind="manual",
                status="pending",
                due_at=base,
                inputs="[]",
            ),
            TaskOccurrence(
                org_id=org_id,
                task_id=task_a.id,
                kind="manual",
                status="pending",
                due_at=base + timedelta(seconds=1),
                inputs="[]",
            ),
            TaskOccurrence(
                org_id=org_id,
                task_id=task_b.id,
                kind="manual",
                status="pending",
                due_at=base + timedelta(seconds=2),
                inputs="[]",
            ),
        ]
    )
    s.commit()
    seen = []
    monkeypatch.setattr(
        scheduler,
        "_run_task",
        lambda task, db: seen.append((task.goal, task._scheduled_for)) or "done",
    )
    monkeypatch.setattr(scheduler, "_verify_task_result", lambda task, result, db: None)

    scheduler._tick(s.get_bind())

    assert [goal for goal, _due in seen] == ["A", "A", "B"]


def test_rolled_back_claim_leaves_no_provisional_run_or_lease(tmp_path):
    from anthill.web import task_occurrences

    s, org_id = _session(tmp_path)
    task = _task(s, org_id, "summarise deploys")
    task.status = "pending"
    occurrence = TaskOccurrence(
        org_id=org_id,
        task_id=task.id,
        kind="manual",
        status="pending",
        due_at=datetime.now(timezone.utc),
        inputs="[]",
    )
    s.add(occurrence)
    s.commit()

    assert task_occurrences.claim(s, task.id, datetime.now(timezone.utc)) is not None
    s.rollback()

    other = sessionmaker(bind=s.get_bind())()
    assert other.get(TaskOccurrence, occurrence.id).status == "pending"
    assert other.query(TaskRun).filter(TaskRun.task_id == task.id).count() == 0


def test_verify_is_best_effort_and_never_raises(tmp_path, monkeypatch):
    import anthill.verify as verify_pkg

    s, org_id = _session(tmp_path)
    t = _task(s, org_id, "do the thing")

    def _boom(*a, **k):
        raise RuntimeError("verifier exploded")

    monkeypatch.setattr(verify_pkg, "verify", _boom)
    scheduler._verify_task_result(t, "a result", s)  # must not raise
    assert t.verify_needs_review is False  # unchanged default; the run is unaffected


def test_verify_columns_migrate_onto_an_existing_scheduled_tasks_table(tmp_path):
    from anthill.web.migrate import ensure_columns

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as c:
        c.execute(
            text("CREATE TABLE organizations (id INTEGER PRIMARY KEY, name VARCHAR, slug VARCHAR)")
        )
        c.execute(
            text(
                "CREATE TABLE scheduled_tasks (id INTEGER PRIMARY KEY, org_id INTEGER, "
                "title VARCHAR, goal TEXT, schedule VARCHAR, status VARCHAR)"
            )
        )
        c.execute(text("INSERT INTO scheduled_tasks (id, goal) VALUES (1, 'x')"))
    added = ensure_columns(eng)
    cols = {col["name"] for col in inspect(eng).get_columns("scheduled_tasks")}
    assert "scheduled_tasks.verify_needs_review" in added
    assert {"verify_needs_review", "verify_reason", "verify_confidence"} <= cols
    with eng.begin() as c:
        v = c.execute(text("SELECT verify_needs_review FROM scheduled_tasks WHERE id=1")).scalar()
    assert v == 0  # existing rows default to not-flagged


def test_occurrence_migration_keeps_blank_claim_separate_from_later_queue(tmp_path):
    import json

    from anthill.web import migrate

    s, org_id = _session(tmp_path)
    due = datetime(2026, 8, 24, 9, 0)
    queued_at = datetime(2026, 8, 24, 10, 0)
    task = _task(s, org_id, "migrated")
    task.status = "running"
    task.last_run_at = due
    task.next_run_at = queued_at
    task.queued_inputs = json.dumps(["later Q"])
    run = TaskRun(
        org_id=org_id,
        task_id=task.id,
        status="running",
        claimed_inputs="",
        scheduled_for=due,
    )
    s.add(run)
    s.commit()
    with s.get_bind().begin() as conn:
        migrate._mig_0003_task_occurrence_ledger(conn)
    s.expire_all()

    occurrences = (
        s.query(TaskOccurrence)
        .filter(TaskOccurrence.task_id == task.id)
        .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
        .all()
    )
    assert [(row.status, json.loads(row.inputs)) for row in occurrences] == [
        ("claimed", []),
        ("pending", ["later Q"]),
    ]
    assert s.get(TaskRun, run.id).occurrence_id == occurrences[0].id


def test_recovery_fields_migrate_onto_existing_task_runs(tmp_path):
    from anthill.web.migrate import ensure_columns

    eng = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as c:
        c.execute(
            text("CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id INTEGER, status VARCHAR)")
        )
        c.execute(text("INSERT INTO task_runs VALUES (1, 1, 'running')"))

    added = ensure_columns(eng)

    assert {
        "task_runs.scheduled_for",
        "task_runs.claimed_inputs",
        "task_runs.cancel_requested",
        "task_runs.occurrence_id",
    } <= set(added)
    with eng.connect() as c:
        row = c.execute(
            text("SELECT scheduled_for, claimed_inputs, cancel_requested FROM task_runs WHERE id=1")
        ).one()
    assert row == (None, "", 0)
