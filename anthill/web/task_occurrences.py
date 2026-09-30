"""Authoritative ordered ledger for scheduled-task work.

``ScheduledTask`` occurrence fields are compatibility projections only. All behavioral
writes and scheduler transitions go through this module.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, exists, or_
from sqlalchemy.orm import aliased

from .db import ScheduledTask, TaskOccurrence, TaskRun

_NONTERMINAL = ("pending", "claimed", "interrupted", "paused")
_DUE = ("pending", "interrupted")


class RetryMutation(RuntimeError):
    pass


def _items(value: str | None) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _dump(items: list[str]) -> str:
    return json.dumps(items)


def _aware(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _remaining(all_items: list[str], owned: list[str]) -> list[str]:
    return all_items[len(owned) :] if all_items[: len(owned)] == owned else all_items


def _legacy_run(db, task_id: int, due_at: datetime) -> Any | None:
    return (
        db.query(TaskRun)
        .filter(TaskRun.task_id == task_id, TaskRun.scheduled_for == due_at)
        .order_by(TaskRun.id.desc())
        .first()
    )


def _run_kind(run: Any | None) -> str:
    return "manual" if run is not None and run.trigger == "manual" else "scheduled"


def ensure_task(db, task: Any) -> None:
    """Conservatively materialize directly-seeded or pre-migration compatibility state."""
    if task.id is None:
        db.flush()
    has_occurrence = exists().where(TaskOccurrence.task_id == task.id)
    claimed = (
        db.query(ScheduledTask)
        .filter(
            ScheduledTask.id == task.id,
            ScheduledTask.occurrences_materialized.is_(False),
            ~has_occurrence,
        )
        .update({ScheduledTask.occurrences_materialized: True}, synchronize_session="fetch")
    )
    if not claimed:
        db.query(ScheduledTask).filter(
            ScheduledTask.id == task.id,
            ScheduledTask.occurrences_materialized.is_(False),
            has_occurrence,
        ).update({ScheduledTask.occurrences_materialized: True}, synchronize_session="fetch")
        db.query(ScheduledTask).populate_existing().filter(ScheduledTask.id == task.id).one()
        return
    db.query(ScheduledTask).populate_existing().filter(ScheduledTask.id == task.id).one()

    queued = _items(task.queued_inputs)
    active: Any = (
        db.query(TaskRun)
        .filter(TaskRun.task_id == task.id, TaskRun.status == "running")
        .order_by(TaskRun.id.desc())
        .first()
    )
    owned: list[str] = []
    occupied_due: datetime | None = None
    cancelled = task.status == "cancelled" or bool(active is not None and active.cancel_requested)
    if active is not None:
        occupied_due = (
            active.scheduled_for or task.last_run_at or active.started_at or task.created_at
        )
        occupied_due = occupied_due or datetime.now(timezone.utc)
        if cancelled:
            active.cancel_requested = True
        owned = _items(active.claimed_inputs)
        occurrence = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind=_run_kind(active),
            status="cancelled" if cancelled else "claimed",
            due_at=occupied_due,
            inputs=_dump(owned),
            claimed_run_id=active.id,
        )
        db.add(occurrence)
        db.flush()
        active.occurrence_id = occurrence.id
    elif task.interrupted_run_at is not None:
        occupied_due = task.interrupted_run_at
        prior_run = _legacy_run(db, task.id, occupied_due)
        owned = (
            _items(task.interrupted_inputs)
            if task.interrupted_inputs is not None
            else _items(prior_run.claimed_inputs if prior_run is not None else "")
        )
        occurrence = TaskOccurrence(
            org_id=task.org_id,
            task_id=task.id,
            kind=_run_kind(prior_run),
            status="cancelled" if cancelled else "interrupted",
            due_at=occupied_due,
            inputs=_dump(owned),
            claimed_run_id=prior_run.id if prior_run is not None else None,
        )
        db.add(occurrence)
        db.flush()
        if prior_run is not None:
            prior_run.occurrence_id = occurrence.id
    elif task.status == "running":
        occupied_due = task.last_run_at or task.created_at or datetime.now(timezone.utc)
        db.add(
            TaskOccurrence(
                org_id=task.org_id,
                task_id=task.id,
                kind=(
                    "manual"
                    if (task.schedule or "").strip().lower() == "once"
                    and task.created_by is not None
                    else "scheduled"
                ),
                status="interrupted",
                due_at=occupied_due,
                inputs="[]",
            )
        )

    if not cancelled:
        queued = _remaining(queued, owned)
    if task.next_run_at is not None:
        if queued:
            immediate_kind = "queued"
        elif occupied_due is not None or (
            (task.schedule or "").strip().lower() == "once" and task.created_by is not None
        ):
            immediate_kind = "manual"
        else:
            immediate_kind = "scheduled"
        db.add(
            TaskOccurrence(
                org_id=task.org_id,
                task_id=task.id,
                kind=immediate_kind,
                status="paused" if cancelled else "pending",
                due_at=task.next_run_at,
                inputs=_dump(queued),
            )
        )
        queued = []
    if queued:
        anchor = task.last_run_at or task.created_at or datetime.now(timezone.utc)
        db.add(
            TaskOccurrence(
                org_id=task.org_id,
                task_id=task.id,
                kind="queued",
                status="paused" if cancelled else "pending",
                due_at=anchor,
                inputs=_dump(queued),
            )
        )
    db.flush()


def project(db, task: Any) -> None:
    """Refresh every legacy compatibility field from the occurrence ledger."""
    db.flush()
    rows: list[Any] = (
        db.query(TaskOccurrence)
        .filter(TaskOccurrence.task_id == task.id, TaskOccurrence.status.in_(_NONTERMINAL))
        .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
        .all()
    )
    interrupted = next((row for row in rows if row.status == "interrupted"), None)
    active_run = (
        db.query(TaskRun)
        .filter(TaskRun.task_id == task.id, TaskRun.status == "running")
        .order_by(TaskRun.id.desc())
        .first()
    )
    cancelled = task.status == "cancelled" or bool(
        active_run is not None and active_run.cancel_requested
    )
    due = next((row for row in rows if row.status in _DUE), None)
    task.next_run_at = None if cancelled or due is None else due.due_at
    task.interrupted_run_at = interrupted.due_at if interrupted else None
    task.interrupted_inputs = interrupted.inputs if interrupted else None
    task.queued_inputs = _dump([item for row in rows for item in _items(row.inputs)])
    if cancelled:
        task.status = "cancelled"
    elif active_run is not None:
        task.status = "running"
    elif due is not None:
        task.status = "pending"


def add(
    db, task: Any, *, kind: str, due_at: datetime, inputs: list[str] | None = None
) -> TaskOccurrence:
    ensure_task(db, task)
    occurrence = TaskOccurrence(
        org_id=task.org_id,
        task_id=task.id,
        kind=kind,
        status="pending",
        due_at=due_at,
        inputs=_dump(inputs or []),
    )
    db.add(occurrence)
    db.flush()
    project(db, task)
    return occurrence


def create_initial(db, task: Any, due_at: datetime | None) -> None:
    if task.id is None:
        db.flush()
    task.occurrences_materialized = True
    if due_at is not None:
        db.add(
            TaskOccurrence(
                org_id=task.org_id,
                task_id=task.id,
                kind="scheduled",
                status="pending",
                due_at=due_at,
                inputs="[]",
            )
        )
        db.flush()
    project(db, task)


def _same(column: Any, value: Any) -> Any:
    return column.is_(None) if value is None else column == value


def _serialize_task_mutation(db, task: Any, target_status: str) -> str:
    from .scheduler import _normalize_task_schedule, _preserve_task_schedule_anchor

    current = (
        db.query(
            ScheduledTask.status,
            ScheduledTask.schedule,
            ScheduledTask.timezone,
            ScheduledTask.schedule_anchor,
            ScheduledTask.last_run_at,
            ScheduledTask.created_at,
        )
        .filter(ScheduledTask.id == task.id)
        .one_or_none()
    )
    if current is None:
        raise RuntimeError("task disappeared while mutating")
    occurrence = (
        db.query(
            TaskOccurrence.id,
            TaskOccurrence.status,
            TaskOccurrence.due_at,
            TaskOccurrence.inputs,
            TaskOccurrence.claimed_run_id,
            TaskOccurrence.cadence_deferred,
        )
        .filter(
            TaskOccurrence.task_id == task.id,
            TaskOccurrence.kind == "scheduled",
            TaskOccurrence.status.in_(_NONTERMINAL),
        )
        .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
        .first()
    )
    historical = None
    if occurrence is None:
        historical = (
            db.query(TaskRun.id, TaskRun.scheduled_for)
            .filter(TaskRun.task_id == task.id, TaskRun.scheduled_for.isnot(None))
            .order_by(TaskRun.id.desc())
            .first()
        )
    schedule = _normalize_task_schedule(current.schedule) or current.schedule
    anchor = _preserve_task_schedule_anchor(
        schedule,
        current.timezone,
        current.schedule_anchor,
        (
            occurrence.due_at
            if occurrence is not None
            else (historical.scheduled_for if historical is not None else None)
            or current.last_run_at
            or current.created_at
        ),
    )
    filters = [
        ScheduledTask.id == task.id,
        ScheduledTask.status == current.status,
        _same(ScheduledTask.schedule, current.schedule),
        _same(ScheduledTask.timezone, current.timezone),
        _same(ScheduledTask.schedule_anchor, current.schedule_anchor),
        _same(ScheduledTask.last_run_at, current.last_run_at),
    ]
    scheduled_exists = exists().where(
        TaskOccurrence.task_id == task.id,
        TaskOccurrence.kind == "scheduled",
        TaskOccurrence.status.in_(_NONTERMINAL),
    )
    if occurrence is None:
        filters.append(~scheduled_exists)
        historical_exists = exists().where(
            TaskRun.task_id == task.id,
            TaskRun.scheduled_for.isnot(None),
        )
        if historical is None:
            filters.append(~historical_exists)
        else:
            filters.extend(
                [
                    exists().where(
                        TaskRun.id == historical.id,
                        TaskRun.task_id == task.id,
                        TaskRun.scheduled_for == historical.scheduled_for,
                    ),
                    ~exists().where(
                        TaskRun.task_id == task.id,
                        TaskRun.scheduled_for.isnot(None),
                        TaskRun.id > historical.id,
                    ),
                ]
            )
    else:
        filters.append(
            exists().where(
                TaskOccurrence.id == occurrence.id,
                TaskOccurrence.task_id == task.id,
                TaskOccurrence.kind == "scheduled",
                TaskOccurrence.status == occurrence.status,
                TaskOccurrence.due_at == occurrence.due_at,
                TaskOccurrence.inputs == occurrence.inputs,
                _same(TaskOccurrence.claimed_run_id, occurrence.claimed_run_id),
                TaskOccurrence.cadence_deferred == occurrence.cadence_deferred,
            )
        )
    serialized = (
        db.query(ScheduledTask)
        .filter(*filters)
        .update(
            {
                ScheduledTask.status: target_status,
                ScheduledTask.schedule: schedule,
                ScheduledTask.schedule_anchor: anchor,
            },
            synchronize_session="fetch",
        )
    )
    if not serialized:
        raise RetryMutation
    db.query(ScheduledTask).populate_existing().filter(ScheduledTask.id == task.id).one()
    return current.status


def reactivate(db, task: Any) -> None:
    ensure_task(db, task)
    _serialize_task_mutation(db, task, "pending")
    db.query(TaskOccurrence).filter(
        TaskOccurrence.task_id == task.id, TaskOccurrence.status == "paused"
    ).update({TaskOccurrence.status: "pending"}, synchronize_session="fetch")
    task.status = "pending"


def run_now(db, task: Any, now: datetime) -> TaskOccurrence:
    reactivate(db, task)
    return add(db, task, kind="manual", due_at=now)


def queue_input(db, task: Any, instruction: str, now: datetime) -> TaskOccurrence:
    ensure_task(db, task)
    while True:
        expected = _serialize_task_mutation(db, task, "pending")
        paused = bool(
            db.query(TaskOccurrence.id)
            .filter(TaskOccurrence.task_id == task.id, TaskOccurrence.status == "paused")
            .first()
        )
        may_append = expected in ("pending", "running") and not paused
        db.query(TaskOccurrence).filter(
            TaskOccurrence.task_id == task.id, TaskOccurrence.status == "paused"
        ).update({TaskOccurrence.status: "pending"}, synchronize_session="fetch")
        task.status = "pending"
        if not may_append:
            return add(db, task, kind="queued", due_at=now, inputs=[instruction])
        while True:
            candidate = (
                db.query(TaskOccurrence)
                .filter(
                    TaskOccurrence.task_id == task.id,
                    TaskOccurrence.status.in_(("pending", "interrupted")),
                )
                .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
                .first()
            )
            if candidate is None or candidate.status == "interrupted":
                due_at = now if candidate is None else max(_aware(now), _aware(candidate.due_at))
                return add(db, task, kind="queued", due_at=due_at, inputs=[instruction])
            if _aware(candidate.due_at) <= _aware(now):
                return add(db, task, kind="queued", due_at=now, inputs=[instruction])
            old = candidate.inputs
            updated = (
                db.query(TaskOccurrence)
                .filter(
                    TaskOccurrence.id == candidate.id,
                    TaskOccurrence.status == "pending",
                    TaskOccurrence.inputs == old,
                )
                .update(
                    {TaskOccurrence.inputs: _dump([*_items(old), instruction])},
                    synchronize_session="fetch",
                )
            )
            if updated:
                project(db, task)
                return db.get(TaskOccurrence, candidate.id)
            db.expire(candidate)


def cancel(db, task: Any) -> None:
    """Cancel first, then close whichever claim serialized immediately before it."""
    ensure_task(db, task)
    _serialize_task_mutation(db, task, "cancelled")
    active: list[Any] = (
        db.query(TaskRun)
        .filter(TaskRun.task_id == task.id, TaskRun.status == "running")
        .order_by(TaskRun.id.desc())
        .all()
    )
    cadence_exists = any(
        not _items(occurrence.inputs)
        for occurrence in db.query(TaskOccurrence)
        .filter(
            TaskOccurrence.task_id == task.id,
            TaskOccurrence.kind == "scheduled",
            TaskOccurrence.status.in_(("pending", "paused")),
        )
        .all()
    )
    cadence_source_due = None
    cadence_deferred = False
    if active:
        active_ids = [run.id for run in active]
        cadence_source = (
            db.query(TaskOccurrence.due_at, TaskOccurrence.cadence_deferred)
            .filter(
                TaskOccurrence.task_id == task.id,
                TaskOccurrence.kind == "scheduled",
                TaskOccurrence.status == "claimed",
                TaskOccurrence.claimed_run_id.in_(active_ids),
            )
            .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
            .first()
        )
        if cadence_source is not None:
            cadence_source_due = cadence_source.due_at
            cadence_deferred = cadence_source.cadence_deferred
        db.query(TaskRun).filter(TaskRun.id.in_(active_ids), TaskRun.status == "running").update(
            {TaskRun.cancel_requested: True}, synchronize_session="fetch"
        )
        db.query(TaskOccurrence).filter(
            TaskOccurrence.task_id == task.id,
            TaskOccurrence.status == "claimed",
            TaskOccurrence.claimed_run_id.in_(active_ids),
        ).update({TaskOccurrence.status: "cancelled"}, synchronize_session="fetch")
    else:
        interrupted = (
            db.query(TaskOccurrence)
            .filter(
                TaskOccurrence.task_id == task.id,
                TaskOccurrence.status == "interrupted",
            )
            .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
            .first()
        )
        if interrupted is not None:
            if interrupted.kind == "scheduled":
                cadence_source_due = interrupted.due_at
                cadence_deferred = interrupted.cadence_deferred
            interrupted.status = "cancelled"
            db.flush()
    db.query(TaskOccurrence).filter(
        TaskOccurrence.task_id == task.id,
        TaskOccurrence.status.in_(("pending", "interrupted")),
    ).update({TaskOccurrence.status: "paused"}, synchronize_session="fetch")
    task.status = "cancelled"
    if cadence_source_due is not None and (cadence_deferred or not cadence_exists):
        from .scheduler import _next_run, _task_schedule_anchor

        now = datetime.now(timezone.utc)
        anchor = task.schedule_anchor or _task_schedule_anchor(
            task.schedule,
            from_dt=_aware(cadence_source_due),
            timezone_name=task.timezone,
        )
        cadence_due = _next_run(
            task.schedule,
            from_dt=_aware(cadence_source_due),
            timezone_name=task.timezone,
            schedule_anchor=anchor,
        )
        while cadence_due is not None and _aware(cadence_due) <= now:
            cadence_due = _next_run(
                task.schedule,
                from_dt=cadence_due,
                timezone_name=task.timezone,
                schedule_anchor=anchor,
            )
        if not replace_scheduled(db, task, cadence_due):
            raise RetryMutation
    project(db, task)


@dataclass(frozen=True)
class EditState:
    active_run_id: int | None
    active_kind: str | None


def defer_cadence(db, run_id: int) -> bool:
    return (
        db.query(TaskOccurrence)
        .filter(
            TaskOccurrence.kind == "scheduled",
            TaskOccurrence.status == "claimed",
            TaskOccurrence.claimed_run_id == run_id,
        )
        .update({TaskOccurrence.cadence_deferred: True}, synchronize_session="fetch")
        == 1
    )


def edit_state(db, task_id: int) -> EditState:
    """Snapshot the claimed occurrence that may defer a cadence edit."""
    active = (
        db.query(TaskRun.id, TaskOccurrence.kind)
        .join(TaskOccurrence, TaskOccurrence.id == TaskRun.occurrence_id)
        .filter(
            TaskRun.task_id == task_id,
            TaskRun.status == "running",
            TaskOccurrence.status == "claimed",
            TaskOccurrence.claimed_run_id == TaskRun.id,
        )
        .order_by(TaskRun.id.desc())
        .first()
    )
    return EditState(
        active_run_id=active[0] if active else None,
        active_kind=active[1] if active else None,
    )


@dataclass(frozen=True)
class Claim:
    task: Any
    occurrence: Any
    run: Any


def _claim_due(db, due_cutoff: datetime, task_id: int | None = None) -> Claim | None:
    while True:
        active_for_task = exists().where(
            TaskRun.task_id == TaskOccurrence.task_id,
            TaskRun.status == "running",
        )
        query = db.query(TaskOccurrence).join(
            ScheduledTask, ScheduledTask.id == TaskOccurrence.task_id
        )
        if task_id is not None:
            query = query.filter(TaskOccurrence.task_id == task_id)
        occurrence: Any = (
            query.filter(
                TaskOccurrence.status.in_(_DUE),
                TaskOccurrence.due_at <= due_cutoff,
                ScheduledTask.status != "cancelled",
                ~active_for_task,
            )
            .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
            .first()
        )
        if occurrence is None:
            return None
        task: Any = db.get(ScheduledTask, occurrence.task_id)
        if task is None:
            return None
        active_exists = exists().where(TaskRun.task_id == task.id, TaskRun.status == "running")
        task_available = exists().where(
            ScheduledTask.id == task.id, ScheduledTask.status != "cancelled"
        )
        old_status = occurrence.status
        old_kind = occurrence.kind
        old_due_at = occurrence.due_at
        old_inputs = occurrence.inputs
        prior_run_id = occurrence.claimed_run_id
        old_cadence_deferred = occurrence.cadence_deferred
        claim_filters = [
            TaskOccurrence.id == occurrence.id,
            TaskOccurrence.task_id == task.id,
            TaskOccurrence.status == old_status,
            TaskOccurrence.kind == old_kind,
            TaskOccurrence.due_at == old_due_at,
            TaskOccurrence.due_at <= due_cutoff,
            TaskOccurrence.inputs == old_inputs,
            _same(TaskOccurrence.claimed_run_id, prior_run_id),
            TaskOccurrence.cadence_deferred == old_cadence_deferred,
            ~active_exists,
            task_available,
        ]
        if task_id is None:
            earlier = aliased(TaskOccurrence)
            earlier_task = aliased(ScheduledTask)
            earlier_active = exists().where(
                TaskRun.task_id == earlier.task_id,
                TaskRun.status == "running",
            )
            earlier_claimable = exists().where(
                earlier.task_id == earlier_task.id,
                earlier.status.in_(_DUE),
                earlier.due_at <= due_cutoff,
                earlier_task.status != "cancelled",
                ~earlier_active,
                or_(
                    earlier.due_at < old_due_at,
                    and_(earlier.due_at == old_due_at, earlier.id < occurrence.id),
                ),
            )
            claim_filters.append(~earlier_claimable)
        claimed = (
            db.query(TaskOccurrence)
            .filter(*claim_filters)
            .update({TaskOccurrence.status: "claimed"}, synchronize_session="fetch")
        )
        if not claimed:
            db.expire_all()
            continue
        started_at = datetime.now(timezone.utc)
        run = TaskRun(
            org_id=task.org_id,
            task_id=task.id,
            occurrence_id=occurrence.id,
            trigger="manual" if old_kind in ("manual", "queued") else "scheduled",
            status="running",
            claimed_inputs=old_inputs,
            scheduled_for=old_due_at,
            started_at=started_at,
            finished_at=None,
        )
        db.add(run)
        db.flush()
        prior_run_filter = (
            TaskOccurrence.claimed_run_id.is_(None)
            if prior_run_id is None
            else TaskOccurrence.claimed_run_id == prior_run_id
        )
        linked = (
            db.query(TaskOccurrence)
            .filter(
                TaskOccurrence.id == occurrence.id,
                TaskOccurrence.status == "claimed",
                prior_run_filter,
            )
            .update({TaskOccurrence.claimed_run_id: run.id}, synchronize_session="fetch")
        )
        if not linked:
            db.query(TaskOccurrence).filter(
                TaskOccurrence.id == occurrence.id,
                TaskOccurrence.status == "claimed",
                prior_run_filter,
            ).update({TaskOccurrence.status: old_status}, synchronize_session="fetch")
            db.delete(run)
            db.flush()
            db.expire_all()
            continue
        occurrence = db.get(TaskOccurrence, occurrence.id)
        task.last_run_at = started_at
        task._attempt_verify_needs_review = False
        task._attempt_verify_reason = ""
        task._attempt_verify_confidence = ""
        task._claimed_queued_inputs = occurrence.inputs
        task._scheduled_for = _aware(occurrence.due_at)
        task._occurrence_id = occurrence.id
        task._task_run = run
        project(db, task)
        return Claim(task, occurrence, run)


def materialize_tasks(db) -> bool:
    has_occurrence = exists().where(TaskOccurrence.task_id == ScheduledTask.id)
    tasks = (
        db.query(ScheduledTask)
        .filter(ScheduledTask.occurrences_materialized.is_(False), ~has_occurrence)
        .all()
    )
    for task in tasks:
        ensure_task(db, task)
    db.flush()
    return bool(tasks)


def claim(db, task_id: int, now: datetime) -> Claim | None:
    task: Any = db.get(ScheduledTask, task_id)
    if task is None or task.status == "cancelled":
        return None
    ensure_task(db, task)
    return _claim_due(db, now, task_id)


def claim_next(db, now: datetime) -> Claim | None:
    return _claim_due(db, now)


def finalize(
    db,
    occurrence_id: int,
    run_id: int,
    *,
    outcome: str,
    next_due_at: datetime | None = None,
    calculate_next_due: Callable[[Any, datetime], datetime | None] | None = None,
) -> bool:
    occurrence: Any = db.get(TaskOccurrence, occurrence_id)
    if occurrence is None:
        return False
    with db.no_autoflush:
        completed = (
            db.query(TaskOccurrence)
            .filter(
                TaskOccurrence.id == occurrence_id,
                TaskOccurrence.status == "claimed",
                TaskOccurrence.claimed_run_id == run_id,
            )
            .update({TaskOccurrence.status: "completed"}, synchronize_session="fetch")
        )
        if not completed:
            return False
        occurrence = (
            db.query(TaskOccurrence)
            .populate_existing()
            .filter(TaskOccurrence.id == occurrence_id)
            .one()
        )
        try:
            task = (
                db.query(ScheduledTask)
                .populate_existing()
                .filter(ScheduledTask.id == occurrence.task_id)
                .one_or_none()
            )
            if occurrence.kind == "scheduled" and task is not None:
                independent_cadence = bool(
                    db.query(TaskOccurrence.id)
                    .filter(
                        TaskOccurrence.task_id == task.id,
                        TaskOccurrence.kind == "scheduled",
                        TaskOccurrence.status.in_(("pending", "paused")),
                    )
                    .first()
                )
                should_reconcile = occurrence.cadence_deferred or not independent_cadence
                if task.status != "cancelled" and should_reconcile:
                    resolved_due = (
                        calculate_next_due(task, _aware(occurrence.due_at))
                        if calculate_next_due is not None
                        else next_due_at
                    )
                    if not replace_scheduled(db, task, resolved_due):
                        db.rollback()
                        return False
            if task is not None:
                task.status = outcome
            return True
        except Exception:
            db.rollback()
            raise


def recover_run(db, run: Any, recovered_at: datetime) -> bool:
    task = db.get(ScheduledTask, run.task_id)
    if task is None:
        return False
    ensure_task(db, task)
    if run.occurrence_id is None:
        return False
    target = "cancelled" if run.cancel_requested else "interrupted"
    recovered = (
        db.query(TaskOccurrence)
        .filter(
            TaskOccurrence.id == run.occurrence_id,
            TaskOccurrence.status == "claimed",
            TaskOccurrence.claimed_run_id == run.id,
        )
        .update({TaskOccurrence.status: target}, synchronize_session="fetch")
    )
    cancelled_owned = bool(
        run.cancel_requested
        and db.query(TaskOccurrence.id)
        .filter(
            TaskOccurrence.id == run.occurrence_id,
            TaskOccurrence.status == "cancelled",
            TaskOccurrence.claimed_run_id == run.id,
        )
        .first()
    )
    if recovered or cancelled_owned:
        run.status = "error"
        run.error = (run.error or "interrupted - the scheduler lost this run before finalization")[
            :800
        ]
        run.finished_at = run.finished_at or recovered_at
        if run.cancel_requested:
            later = (
                db.query(TaskOccurrence.id)
                .filter(
                    TaskOccurrence.task_id == task.id,
                    TaskOccurrence.status.in_(_DUE),
                )
                .first()
            )
            task.status = "pending" if later else "cancelled"
        project(db, task)
    return bool(recovered or cancelled_owned)


def replace_scheduled(db, task: Any, due_at: datetime | None) -> bool:
    """Replace unclaimed cadence work without moving or deleting owned inputs."""
    ensure_task(db, task)
    pending: list[Any] = (
        db.query(TaskOccurrence)
        .filter(
            TaskOccurrence.task_id == task.id,
            TaskOccurrence.kind == "scheduled",
            TaskOccurrence.status.in_(("pending", "paused")),
        )
        .order_by(TaskOccurrence.due_at, TaskOccurrence.id)
        .all()
    )

    def mutate(occurrence: Any, values: dict[Any, Any] | None) -> bool:
        query = db.query(TaskOccurrence).filter(
            TaskOccurrence.id == occurrence.id,
            TaskOccurrence.kind == occurrence.kind,
            TaskOccurrence.status == occurrence.status,
            TaskOccurrence.due_at == occurrence.due_at,
            TaskOccurrence.inputs == occurrence.inputs,
        )
        if values is None:
            return query.delete(synchronize_session="fetch") == 1
        return query.update(values, synchronize_session="fetch") == 1

    cadence: list[Any] = []
    for occurrence in pending:
        if _items(occurrence.inputs):
            if not mutate(occurrence, {TaskOccurrence.kind: "queued"}):
                return False
        else:
            cadence.append(occurrence)

    if due_at is None:
        for occurrence in cadence:
            if not mutate(occurrence, None):
                return False
    elif cadence:
        first, *extra = cadence
        if not mutate(first, {TaskOccurrence.due_at: due_at}):
            return False
        for occurrence in extra:
            if not mutate(occurrence, None):
                return False
    else:
        db.add(
            TaskOccurrence(
                org_id=task.org_id,
                task_id=task.id,
                kind="scheduled",
                status="paused" if task.status == "cancelled" else "pending",
                due_at=due_at,
                inputs="[]",
            )
        )
    db.query(TaskOccurrence).filter(
        TaskOccurrence.task_id == task.id,
        TaskOccurrence.kind == "scheduled",
        TaskOccurrence.status == "interrupted",
        TaskOccurrence.cadence_deferred.is_(True),
    ).update({TaskOccurrence.cadence_deferred: False}, synchronize_session="fetch")
    db.flush()
    project(db, task)
    return True
