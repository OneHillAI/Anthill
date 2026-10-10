"""Background task scheduler.

Runs as a daemon thread inside the web process. Claims durable task occurrences in
``(due_at, id)`` order, executes them with the agent, and records results.
``ScheduledTask.next_run_at`` is only a compatibility projection of that ledger.
"""

from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import object_session, sessionmaker

from ..platform_layer import try_lock_exclusive
from .db import (
    OrgSettings,
    QueuedUpload,
    ScheduledTask,
    TaskRun,
    TrainingExample,
    TrainingRun,
    WikiReview,
    create_tables,
    get_engine,
)

# Push sources (webhooks / IMAP) set this to wake the fast event tick immediately,
# instead of waiting up to one fast_interval_s for the next poll.
_wake = threading.Event()
_scheduler_lock = threading.Lock()
_scheduler_thread: threading.Thread | None = None
_scheduler_process_lock = None


def _scheduler_lock_path(engine) -> Path | None:
    """Return the per-database scheduler lock path for a file-backed SQLite engine."""
    database = getattr(getattr(engine, "url", None), "database", None)
    if not database or database == ":memory:":
        return None
    return Path(database).with_name(f"{Path(database).name}.scheduler.lock")


def _acquire_scheduler_process_lock(engine) -> bool:
    """Elect one scheduler process per database using an OS lock released on process death."""
    global _scheduler_process_lock
    if _scheduler_process_lock is not None:
        return True
    path = _scheduler_lock_path(engine)
    if path is None:
        return True
    handle = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+")
        os.chmod(path, 0o600)
        if not try_lock_exclusive(handle):
            handle.close()
            return False
        _scheduler_process_lock = handle
        return True
    except (ImportError, OSError):
        if handle is not None:
            handle.close()
        # File systems that cannot lock retain the process-local guard; normal Anthill deployments use
        # SQLite on a local disk, where the lock is released by the kernel when the process exits.
        return True


def signal_event() -> None:
    """Wake the event tick now (called after a push source drops work into inbox/)."""
    _wake.set()


def _normalize_task_schedule(value: str) -> str:
    """Return the scheduler's canonical task grammar, or empty for invalid input."""
    value = " ".join((value or "").lower().split())
    if value in {"once", "hourly", "daily", "weekly", "weekdays"}:
        return value
    match = re.fullmatch(r"(\d{1,2}):(\d{2})( weekdays)?", value)
    if not match:
        return ""
    hour, minute = int(match[1]), int(match[2])
    if hour > 23 or minute > 59:
        return ""
    return f"{hour:02d}:{minute:02d}{match[3] or ''}"


def _normalize_timezone(name: str) -> str:
    """Return a valid IANA timezone name, or the legacy UTC fallback marker."""
    name = (name or "").strip()
    try:
        ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError):
        return ""
    return name


def _task_schedule_anchor(
    schedule: str,
    from_dt: datetime | None = None,
    timezone_name: str = "",
) -> datetime | None:
    if _normalize_task_schedule(schedule) not in {"daily", "weekly", "weekdays"}:
        return None
    timezone_name = _normalize_timezone(timezone_name)
    if not timezone_name:
        return None
    now = from_dt or datetime.now(timezone.utc)
    aware_now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now
    return aware_now.astimezone(ZoneInfo(timezone_name)).replace(tzinfo=None)


def _preserve_task_schedule_anchor(
    schedule: str,
    timezone_name: str,
    schedule_anchor: datetime | None,
    due_at: datetime | None,
) -> datetime | None:
    if schedule_anchor is not None or due_at is None:
        return schedule_anchor
    return _task_schedule_anchor(
        schedule,
        from_dt=due_at,
        timezone_name=timezone_name or "UTC",
    )


def _next_run(
    schedule: str,
    from_dt: datetime | None = None,
    timezone_name: str = "",
    schedule_anchor: datetime | None = None,
) -> datetime | None:
    """Compute the next run time, preserving task-local calendar time when supplied."""
    now = from_dt or datetime.now(timezone.utc)
    s = _normalize_task_schedule(schedule)
    if not s:
        return None
    if s == "once":
        return None  # runs once then stops
    if s == "hourly":
        return now + timedelta(hours=1)

    timezone_name = _normalize_timezone(timezone_name)
    if schedule_anchor is not None and not timezone_name:
        timezone_name = "UTC"
    if timezone_name:
        aware_now = now.replace(tzinfo=timezone.utc) if now.tzinfo is None else now
        now = aware_now.astimezone(ZoneInfo(timezone_name))

    def anchored(candidate: datetime) -> datetime:
        if not timezone_name or schedule_anchor is None:
            return candidate
        return candidate.replace(
            hour=schedule_anchor.hour,
            minute=schedule_anchor.minute,
            second=schedule_anchor.second,
            microsecond=schedule_anchor.microsecond,
            fold=0,
        )

    def stored(candidate: datetime) -> datetime:
        return candidate.astimezone(timezone.utc) if timezone_name else candidate

    if s == "daily":
        candidate = anchored(now)
        if stored(candidate) <= stored(now):
            candidate += timedelta(days=1)
        return stored(candidate)
    if s == "weekly":
        if timezone_name and schedule_anchor is not None:
            days_ahead = (schedule_anchor.weekday() - now.weekday()) % 7
            candidate = anchored(now + timedelta(days=days_ahead))
            if stored(candidate) <= stored(now):
                candidate += timedelta(weeks=1)
            return stored(candidate)
        return stored(now + timedelta(weeks=1))
    weekdays_only = "weekday" in s  # "weekdays" or "HH:MM weekdays" -> Mon-Fri only (issue #394)
    if s == "weekdays":
        candidate = anchored(now)
        if stored(candidate) <= stored(now):
            candidate += timedelta(days=1)
        while candidate.weekday() >= 5:  # skip Sat (5) / Sun (6)
            candidate += timedelta(days=1)
        return stored(candidate)
    # Time-of-day: "HH:MM" (daily at that time), optionally "HH:MM weekdays" (that time, Mon-Fri only).
    if ":" in s:
        parts = s.split()
        hhmm = parts[0]
        h, m = int(hhmm.split(":")[0]), int(hhmm.split(":")[1])
        candidate = now.replace(hour=h, minute=m, second=0, microsecond=0, fold=0)
        if stored(candidate) <= stored(now):
            candidate += timedelta(days=1)
        while weekdays_only and candidate.weekday() >= 5:
            candidate += timedelta(days=1)
        return stored(candidate)
    return None


def _effective_goal(task) -> str:
    """The task goal plus any queued follow-up instructions, which are consumed
    (cleared) so the same instruction is not replayed on a later run."""
    import json

    claimed_inputs = getattr(task, "_claimed_queued_inputs", None)
    goal = task.goal
    try:
        extra = json.loads(
            claimed_inputs if claimed_inputs is not None else (task.queued_inputs or "[]")
        )
    except Exception:
        extra = []
    if extra:
        goal += "\n\nAdditional follow-up instructions (queued):\n" + "\n".join(
            f"- {x}" for x in extra
        )
    return goal


def _verify_task_result(task: ScheduledTask, result: str, db) -> None:
    """Best-effort runtime cross-check of a finished task result against its goal (the verifier). Records
    an advisory verdict on the task; NEVER raises - a verifier hiccup must not fail the task, and the
    result is delivered either way. Surfaced to the user as a 'needs review' flag when the result may
    not satisfy the goal or an independent (different-family) local model disagrees. See anthill.verify.

    OR-combines with any flag already set on ``task`` (#278: _run_task may have already flagged an
    uncertain answer that wasn't escalated) rather than overwriting it - both signals are real and
    independent, so whichever ran first must survive the other."""
    try:
        from ..verify import crosscheck_for, verify

        cfg = db.query(OrgSettings).filter(OrgSettings.org_id == task.org_id).first()
        model = (getattr(cfg, "ollama_model", "") or "") if cfg else ""
        url = (getattr(cfg, "ollama_url", "") if cfg else "") or "http://localhost:11434"
        verdict = verify(
            result or "",
            kind="task_result",
            goal=task.goal or "",
            crosscheck=crosscheck_for(url, model),
        )
        prior_needs_review = bool(task.verify_needs_review)
        prior_reason = task.verify_reason or ""
        task.verify_needs_review = prior_needs_review or bool(verdict.needs_review)
        new_reason = (verdict.reason or "")[:400]
        task.verify_reason = (
            f"{prior_reason} | {new_reason}"
            if prior_reason and new_reason
            else (prior_reason or new_reason)
        )[:400]
        task.verify_confidence = f"{verdict.confidence:.2f}"
    except Exception:
        pass  # advisory only - a verifier failure must never break the task run


def _run_task(task: ScheduledTask, db) -> str:
    """Execute one task with the agent executor and return the result.

    Plane-aware (P2): a Solo task runs on the local model + personal context; an Org task runs on the
    organization's shared endpoint + org workspace, with personal context excluded (the privacy
    invariant). An Org task whose backend is not connected raises ``PlaneUnavailable`` - the tick
    marks it failed (and a recurring task retries when the backend returns); it never silently runs
    on the local model.
    """

    from ..agent.executor import AgentExecutor
    from ..agent.tools import files_owner, make_tools
    from ..config import Config
    from ..inference.base import build_backend
    from . import audit
    from .crypto import decrypt
    from .plane_routing import plane_inference

    cfg_row = db.query(OrgSettings).filter(OrgSettings.org_id == task.org_id).first()
    plane_inf = plane_inference(getattr(task, "plane", "solo"), cfg_row, decrypt=decrypt)
    audit.log_inference_call(
        db, plane_inf, org_id=task.org_id, user_id=task.created_by, surface="task"
    )
    config = Config.from_env()
    config.backend = plane_inf.backend
    config.base_url = plane_inf.base_url
    config.model = plane_inf.model
    if plane_inf.api_key:
        config.api_key = plane_inf.api_key

    # A project task writes to ITS OWN wiki (team-<id>, when its creator is a member); an Org task to the
    # org wiki; a Solo task to the default/personal workspace (#419 P2). One shared resolver for all runs.
    from .agent_context import run_wiki_workspace

    ws_path = run_wiki_workspace(
        db,
        plane_inf=plane_inf,
        team_id=getattr(task, "team_id", None),
        member_user_id=task.created_by,
        personal_user_id=task.created_by,
        org_id=task.org_id,
    )
    from .mcp_store import mcp_client_tools

    tools = make_tools(
        workspace=ws_path, owner=files_owner(task.org_id, task.created_by)
    ) + mcp_client_tools(
        db, task.org_id
    )  # builtin tools + approved MCP; files scoped per-user (org/user)
    backend = build_backend(config)

    # Run under the org's governed "scheduler" agent identity (least-privilege + audit).
    from .agents import audit_hook, get_or_create_identity, principal_for

    ident = get_or_create_identity(db, task.org_id, "scheduler", agent_type="scheduler")
    from .. import planes
    from .agent_context import agent_context_for

    principles, skills = agent_context_for(
        db,
        user_id=task.created_by,
        org_id=task.org_id,
        plane=getattr(task, "plane", "solo"),
        team_id=getattr(task, "team_id", None),
        is_org=planes.is_org_mode(cfg_row),
    )
    executor = AgentExecutor(
        backend,
        tools,
        max_steps=int(getattr(cfg_row, "task_max_steps", 10) or 10),  # org run cap (Task settings)
        identity=principal_for(ident),
        on_action=audit_hook(db, task.org_id),
        skills=skills,
        principles=principles,
    )
    from .recall import recall_memory

    goal = _effective_goal(task)  # task goal + any queued follow-ups (consumed)
    # Privacy: personal memory is never sent to the org model (Org tasks run without personal context).
    mem_ctx = (
        recall_memory(db, task.org_id, task.created_by, goal)
        if (task.created_by and plane_inf.use_personal_context)
        else ""
    )
    from .task_context import build_task_context

    attached = build_task_context(
        db, task
    )  # connected-service files + snippets the creator attached
    ctx = f"{attached}\n\n{mem_ctx}".strip() if attached else mem_ctx
    result = executor.run(goal, context=ctx)
    answer = result.answer
    # Council review (Phase 4b): the task's tool-calling loop already ran exactly once, on `backend`,
    # above - this only ever critiques the finished text via .chat(), never re-runs the loop or gives a
    # reviewer tool access. Degrades to `answer` unchanged on any failure - a review must never break or
    # worsen a task run. Belt-and-suspenders: review_completed_answer() already degrades internally, this
    # try/except is a second guard in case something outside it (e.g. an import error) raises.
    # Admin-gated (OrgSettings.council_review_tasks, on by default): each review is extra API calls,
    # latency, and cost, and sends the finished answer to additional backends - an org that wants
    # council review for chat but not for task/agent runs can turn just this off.
    if getattr(cfg_row, "council_review_tasks", True):
        try:
            from ..council.engine import review_completed_answer

            answer = review_completed_answer(cfg_row, goal, answer, backend, decrypt).answer
        except Exception:
            pass  # keep the pre-review answer; a review failure must never worsen the run
    answer = _maybe_escalate_task(
        task, cfg_row, decrypt, plane_inf, goal=goal, context=ctx, answer=answer, db=db
    )
    return answer


def _reset_monthly_escalation_count_if_due(cfg_row) -> None:
    """Lazily reset the monthly escalation counter (mirrors this codebase's other running-total
    fields, e.g. cloud_spent_usd - reset on next use rather than a separate scheduled job)."""
    reset_at = getattr(cfg_row, "task_escalations_reset_at", None)
    now = datetime.now(timezone.utc)
    if reset_at is None or (now - reset_at.replace(tzinfo=timezone.utc)) > timedelta(days=30):
        cfg_row.task_escalations_this_month = 0
        cfg_row.task_escalations_reset_at = now


def _flag_task_for_review(task, reason: str) -> None:
    if getattr(task, "_occurrence_id", None) is not None:
        task._attempt_verify_needs_review = True
        task._attempt_verify_reason = reason[:400]
        task._attempt_verify_confidence = ""
    else:
        task.verify_needs_review = True
        task.verify_reason = reason[:400]
        task.verify_confidence = ""


def _maybe_escalate_task(
    task, cfg_row, decrypt, plane_inf, *, goal: str, context: str, answer: str, db
) -> str:
    """#278: an unattended Task's escalation decision is made ONCE at creation time
    (``task.escalate_on_uncertainty``), never live - there is no one present to approve anything
    mid-run. When eligible but not escalated (disabled, disconnected, or the monthly cap is hit), the
    run is honestly flagged via verify_needs_review rather than silently accepting a weak answer.
    Never raises - an escalation failure keeps the original (weak but real) local answer."""
    from ..agent.intent import looks_uncertain
    from ..inference.base import stays_local

    try:
        if not stays_local(plane_inf.backend, plane_inf.base_url):
            return answer  # already answered on a remote backend
        if not looks_uncertain(answer):
            return answer
        if not getattr(task, "escalate_on_uncertainty", False):
            _flag_task_for_review(task, "Answer looked uncertain; escalation is off for this task.")
            return answer
        from .plane_routing import org_endpoint_connected

        if not org_endpoint_connected(cfg_row, decrypt):
            _flag_task_for_review(
                task,
                "Answer looked uncertain; escalation is on for this task, but no "
                "backend is connected.",
            )
            return answer
        _reset_monthly_escalation_count_if_due(cfg_row)
        cap = int(getattr(cfg_row, "task_escalation_cap_per_month", 20) or 20)
        used = int(getattr(cfg_row, "task_escalations_this_month", 0) or 0)
        if used >= cap:
            _flag_task_for_review(
                task,
                f"Answer looked uncertain; this org's monthly escalation cap ({cap}) is reached.",
            )
            return answer
        from ..inference.base import Message
        from .escalation import build_escalation_backend

        backend, _plane_inf = build_escalation_backend(
            db, cfg_row, decrypt, org_id=task.org_id, user_id=task.created_by, surface="task"
        )
        cfg_row.task_escalations_this_month = used + 1
        content = backend.chat([Message("user", f"{goal}\n\n{context}".strip())])
        return f"[Answered by the connected backend] {content}"
    except Exception:
        _flag_task_for_review(
            task, "Answer looked uncertain; escalating to the connected backend failed."
        )
        return answer


def _start_task_run(db, task, started_at):
    """Return the history row atomically created by the occurrence claim."""
    return getattr(task, "_task_run", None)


def _finish_task_run(db, run, task, *, started_at, result: str, error: str) -> None:
    """Close the task's run history row with its outcome + verifier verdict. Best-effort. Duration is
    from the in-memory ``started_at`` (the row reads back tz-naive from SQLite)."""
    if run is None:
        return
    try:
        finished = datetime.now(timezone.utc)
        run.status = "error" if error else "ok"
        run.result = (result or "")[:8000]
        run.error = (error or "")[:800]
        run.verify_needs_review = bool(task.verify_needs_review)
        run.verify_reason = task.verify_reason or ""
        run.verify_confidence = task.verify_confidence or ""
        run.finished_at = finished
        run.duration_ms = int((finished - started_at).total_seconds() * 1000)
    except Exception:
        pass  # advisory history - never break the run on a recording error


def _prepare_claimed_task(claimed) -> ScheduledTask:
    task = claimed.task
    task.schedule_anchor = _preserve_task_schedule_anchor(
        task.schedule,
        task.timezone,
        task.schedule_anchor,
        claimed.occurrence.due_at,
    )
    return task


def _claim_task(db, task_id: int, now: datetime) -> ScheduledTask | None:
    from . import task_occurrences

    claimed = task_occurrences.claim(db, task_id, now)
    if claimed is None:
        db.rollback()
        return None
    return _prepare_claimed_task(claimed)


def _claim_next_task(db, now: datetime) -> ScheduledTask | None:
    from . import task_occurrences

    claimed = task_occurrences.claim_next(db, now)
    if claimed is None:
        db.rollback()
        return None
    return _prepare_claimed_task(claimed)


_ORPHAN_TASK_AGE = timedelta(minutes=2)


def _recover_interrupted_task_run(task, run, recovered_at: datetime) -> None:
    if task is None:
        return
    from . import task_occurrences

    scheduled_for = (
        run.scheduled_for or task.last_run_at or run.started_at or task.created_at or recovered_at
    )
    run.scheduled_for = scheduled_for
    task.schedule_anchor = _preserve_task_schedule_anchor(
        task.schedule,
        task.timezone,
        task.schedule_anchor,
        scheduled_for,
    )
    task_occurrences.recover_run(object_session(run), run, recovered_at)


def _recover_orphaned_task_runs(db, now: datetime) -> None:
    """Re-arm a task left running after an in-process tick failed during finalization.

    The scheduler is single-threaded per database, so a run older than the recovery window cannot
    still be executing when a later tick reaches this function. Startup recovery handles a process
    crash immediately; this bounded sweep covers a database write failure after the process survives.
    """
    cutoff = now - _ORPHAN_TASK_AGE
    rows = (
        db.query(TaskRun)
        .filter(
            TaskRun.status == "running",
            TaskRun.started_at.isnot(None),
            TaskRun.started_at <= cutoff,
        )
        .all()
    )
    if not rows:
        return
    recovered_at = now
    for run in rows:
        task = db.get(ScheduledTask, run.task_id)
        _recover_interrupted_task_run(task, run, recovered_at)
    db.commit()


def _remember_task_outcome(db, task, result: str) -> list[tuple[set[int], str, str, int]]:
    """Stage a successful task memory in the same transaction as occurrence finalization."""
    notices: list[tuple[set[int], str, str, int]] = []
    try:
        with db.begin_nested():
            from .. import memory as mem
            from .db import MemoryItem
            from .memory_ops import auto_memory_on

            first = (result or "").strip().splitlines()
            summary = f"Task '{task.title}': {first[0][:280]}" if first else ""
            if not summary or not task.created_by or not auto_memory_on(db, task.created_by):
                return notices
            rows = (
                db.query(MemoryItem)
                .filter(MemoryItem.user_id == task.created_by, MemoryItem.scope == "personal")
                .all()
            )
            vec = mem.embed_text(summary)
            if not mem.is_new(summary, [row.text for row in rows]) or not mem.is_semantically_new(
                vec, [mem.decode_vec(row.embedding) for row in rows]
            ):
                return notices
            new = MemoryItem(
                org_id=task.org_id,
                user_id=task.created_by,
                scope="personal",
                kind="task_outcome",
                text=summary,
                embedding=mem.encode_vec(vec),
                source="task",
                source_id=task.id,
            )
            db.add(new)
            db.flush()
            from .memory_ops import maybe_corroborate

            maybe_corroborate(db, task.org_id, new, notification_queue=notices)
        return notices
    except Exception:
        return []


def _dispatch_memory_notices(db, notices: list[tuple[set[int], str, str, int]]) -> None:
    from .memory_ops import _notify_promoted

    for notice in notices:
        _notify_promoted(db, *notice)


def _close_unpublished_task_run(db, run_id: int, started_at: datetime, error: str) -> None:
    """Rollback stale/provisional outcome writes and durably close the already-published lease."""
    from . import task_occurrences

    db.rollback()
    run = db.get(TaskRun, run_id)
    if run is None:
        return
    finished = datetime.now(timezone.utc)
    if run.status == "running":
        task_occurrences.recover_run(db, run, finished)
    run.status = "error"
    run.result = ""
    run.error = error[:800]
    run.verify_needs_review = False
    run.verify_reason = ""
    run.verify_confidence = ""
    run.finished_at = finished
    run.duration_ms = int((finished - started_at).total_seconds() * 1000)
    try:
        db.commit()
    except Exception:
        db.rollback()


def _tick(engine):
    """One scheduler tick - claim and publish each due occurrence in global order."""
    Session = sessionmaker(bind=engine)
    db = Session()
    now = datetime.now(timezone.utc)
    _recover_orphaned_task_runs(db, now)

    from . import task_occurrences

    if task_occurrences.materialize_tasks(db):
        db.commit()

    while True:
        task = _claim_next_task(db, now)
        if task is None:
            break
        started = datetime.now(timezone.utc)
        run_row = _start_task_run(db, task, started)
        if run_row is None:
            db.rollback()
            continue
        try:
            db.flush()
            db.commit()  # publish only the lease so the result page can show it live
        except Exception:
            db.rollback()
            continue

        result, error = "", ""
        outcome_status = "done"
        try:
            result = _run_task(task, db) or ""
        except Exception as exc:
            outcome_status = "failed"
            error = str(exc)

        finalization_error = ""
        try:

            def calculate_next_due(current_task, due_at):
                schedule_anchor = current_task.schedule_anchor or _task_schedule_anchor(
                    current_task.schedule,
                    from_dt=due_at,
                    timezone_name=current_task.timezone,
                )
                current_task.schedule_anchor = schedule_anchor
                reschedule_after = datetime.now(timezone.utc)
                nxt = _next_run(
                    current_task.schedule,
                    from_dt=due_at,
                    timezone_name=current_task.timezone,
                    schedule_anchor=schedule_anchor,
                )
                while nxt is not None and nxt <= reschedule_after:
                    nxt = _next_run(
                        current_task.schedule,
                        from_dt=nxt,
                        timezone_name=current_task.timezone,
                        schedule_anchor=schedule_anchor,
                    )
                return nxt

            finalized = task_occurrences.finalize(
                db,
                task._occurrence_id,
                run_row.id,
                outcome=outcome_status,
                calculate_next_due=calculate_next_due,
            )
        except Exception as exc:
            finalized = False
            finalization_error = f"task finalization failed: {exc}"
        if not finalized:
            try:
                was_cancelled = bool(
                    db.query(TaskRun.cancel_requested).filter(TaskRun.id == run_row.id).scalar()
                )
            except Exception:
                db.rollback()
                was_cancelled = False
            _close_unpublished_task_run(
                db,
                run_row.id,
                started,
                "Cancelled while it was running. Its result was not kept."
                if was_cancelled
                else (finalization_error or "stale attempt lost occurrence ownership"),
            )
            continue

        task.verify_needs_review = bool(getattr(task, "_attempt_verify_needs_review", False))
        task.verify_reason = getattr(task, "_attempt_verify_reason", "")
        task.verify_confidence = getattr(task, "_attempt_verify_confidence", "")
        memory_notices = []
        if error:
            task.last_result = f"ERROR: {error}"
            task.verify_needs_review = False
            task.verify_reason = ""
            task.verify_confidence = ""
        else:
            task.last_result = result[:4000]
            _verify_task_result(task, result, db)
            memory_notices = _remember_task_outcome(db, task, result)
        task.run_count += 1
        _finish_task_run(db, run_row, task, started_at=started, result=result, error=error)
        task_occurrences.project(db, task)
        try:
            db.commit()
        except Exception as exc:
            _close_unpublished_task_run(
                db, run_row.id, started, f"task outcome publication failed: {exc}"
            )
            continue

        _dispatch_memory_notices(db, memory_notices)

        # Surface a run that needs attention through the unified notification centre (#284): a flagged or
        # failed run pings the owner (bell + centre + web push); a clean routine run stays quiet.
        if task.created_by and (task.verify_needs_review or error):
            from .notify import notify

            notify(
                db,
                user_id=task.created_by,
                org_id=task.org_id,
                kind="run",
                title=("Task failed" if error else "A task result needs your review"),
                body=f"{task.title}: {task.verify_reason or error or task.status}",
                link=f"/tasks/{task.id}/result",
            )

    db.close()


def _gold_count(db, org_id: int) -> int:
    """Org-scope gold only - personal signals don't trigger shared-model training."""
    return (
        db.query(TrainingExample)
        .filter(
            TrainingExample.org_id == org_id,
            TrainingExample.quality == "gold",
            TrainingExample.scope == "org",
        )
        .count()
    )


def _train_tick(engine):
    """Once per loop: for each training-enabled org, fire a run iff new gold
    accumulated and the 24h cadence elapsed (§7.5 'when').

    This creates the TrainingRun record and advances the watermark. The trainer
    *execution* (fine-tune → eval-gate → Ollama register) is a separate module;
    a scheduled run waits in status 'scheduled' until that module picks it up.
    """
    from sqlalchemy import or_

    from ..training.model_select import resolve_base_model, training_on
    from ..training.schedule import should_train

    Session = sessionmaker(bind=engine)
    db = Session()
    now = datetime.now(timezone.utc)
    try:
        # Include solo orgs even with training_enabled unset: local/solo training is always-on.
        for cfg in (
            db.query(OrgSettings)
            .filter(
                or_(
                    OrgSettings.training_enabled.is_(True),
                    OrgSettings.deployment_topology == "solo",
                )
            )
            .all()
        ):
            gold = _gold_count(db, cfg.org_id)
            go, reason = should_train(cfg, gold, now=now, enabled=training_on(cfg))
            if not go:
                continue
            db.add(
                TrainingRun(
                    org_id=cfg.org_id,
                    base_model=resolve_base_model(cfg),
                    backend=cfg.training_backend,
                    gold_count=gold,
                    status="scheduled",
                    note=reason,
                )
            )
            cfg.training_gold_mark = gold  # advance watermark so we don't re-fire
            cfg.training_last_run = now
            cfg.training_status = "scheduled"
            db.commit()
    finally:
        db.close()


_executing_training = threading.Event()  # one execution pass at a time


def _execute_tick(engine):
    """Pick up scheduled TrainingRuns and execute them. A fine-tune can take minutes, so this
    runs off the scheduler thread (a daemon worker) and is guarded so passes never overlap."""
    if _executing_training.is_set():
        return

    def _go():
        _executing_training.set()
        try:
            from ..training.executor import run_scheduled

            run_scheduled(engine)
        except Exception:
            pass
        finally:
            _executing_training.clear()

    threading.Thread(target=_go, daemon=True, name="anthill-train-exec").start()


_last_reap_at: float | None = None  # monotonic seconds of the last reap sweep; None = never yet
_REAP_EVERY_S = (
    600.0  # sweep for leaked training pods ~every 10 min (the slow tick fires ~every 60s)
)


def _reap_tick(engine):
    """Terminate leaked RunPod training pods - the safety net for a crash or redeploy between a pod's
    ``launch()`` and the trainer's own try/finally teardown (#254). Sweeps on the first tick (so a leak
    from a prior crashed process is caught promptly), then ~every 10 min, not every slow tick.
    Best-effort and per-org: only orgs that train on RunPod with a key are touched, and a healthy
    in-flight run's pod is never in the reap window (reaper.plan_max_age).
    """
    import time as _time

    global _last_reap_at
    if _last_reap_at is not None and _time.monotonic() - _last_reap_at < _REAP_EVERY_S:
        return
    _last_reap_at = _time.monotonic()

    from ..training.reaper import configured_run_max_min, reaper_client_for_cfg, sweep

    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        for cfg in db.query(OrgSettings).all():
            client = reaper_client_for_cfg(cfg)  # None unless this org trains on RunPod with a key
            if client is None:
                continue
            active = (
                db.query(TrainingRun)
                .filter(TrainingRun.org_id == cfg.org_id, TrainingRun.status == "running")
                .count()
                > 0
            )
            try:
                res = sweep(client, active_run=active, run_max_min=configured_run_max_min(cfg))
            except Exception:
                continue  # a provider hiccup: just try again next sweep
            if res.reaped:
                _report_reaped(db, cfg.org_id, res)
    finally:
        db.close()


def _report_reaped(db, org_id: int, res) -> None:
    """Record a reaped leak in the audit log and ping the org's admins - a leaked GPU pod is money."""
    from . import audit
    from .db import User
    from .notify import notify

    names = ", ".join(name for _id, name, _age in res.reaped) or "(unnamed)"
    try:
        audit.log(
            db,
            "training.pod_reaped",
            f"terminated {len(res.reaped)} leaked training pod(s): {names}",
            org_id=org_id,
        )
    except Exception:
        pass
    admins = (
        db.query(User)
        .filter(User.org_id == org_id, User.active.is_(True), User.role == "admin")
        .all()
    )
    for a in admins:
        notify(
            db,
            user_id=a.id,
            org_id=org_id,
            kind="training",
            title="Cleaned up a leaked training GPU",
            body=(
                f"A training pod was still running after its run and has been terminated "
                f"({len(res.reaped)}). No further billing for it."
            ),
            link="/settings",
        )


def _set_aside(source, state: str, identity_name: str, hook, message: str) -> None:
    """Move an inbox file the drain can never complete into a visible state directory.

    `inbox_files` lists files only, so a subdirectory of inbox/ is skipped by every later
    tick - which is what stops the retry loop. The content-addressed raw copy is already
    preserved by `ingest` before it raises, so the source is never lost. Confirmation is
    the user's to give: nothing here auto-confirms.
    """
    from pathlib import Path

    try:
        target_dir = Path(source).parent / state
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / source.name
        suffix = 1
        while target.exists():
            target = target_dir / f"{source.stem}-{suffix}{source.suffix}"
            suffix += 1
        source.rename(target)
    except OSError:
        return  # could not set it aside; the next tick retries rather than losing the file
    hook(identity_name, "ingest_inbox", f"wiki {state}: {source.name}: {message}", False)


def _ingest_inbox_file(ws, source, backend, *, on_write, identity_name: str, hook) -> None:
    """Ingest one inbox file, routing the failures the drain can never resolve on its own.

    A PDF over the automatic-work limits, or one that violates a hard structural limit, fails
    identically on every tick, so it is set aside where the user can see it rather than
    re-parsed forever. Host-level failures are the file's to survive, not its fault: they stay
    queued for the next tick, but are surfaced so a broken host cannot pass for an idle inbox.
    """
    from ..multimodal.reader import PdfParseError
    from ..wiki.ingest import PdfConfirmationRequired, PdfProcessingLimitExceeded
    from ..wiki.ingest import ingest as wiki_ingest

    try:
        wiki_ingest(ws, source, backend, on_write=on_write)
        source.unlink()
        hook(identity_name, "ingest_inbox", "wiki", True)
    except PdfConfirmationRequired as exc:
        _set_aside(source, "needs-confirmation", identity_name, hook, str(exc))
    except PdfProcessingLimitExceeded as exc:
        if exc.transient:
            hook(identity_name, "ingest_inbox", f"wiki retrying: {source.name}: {exc}", False)
        else:
            _set_aside(source, "rejected", identity_name, hook, str(exc))
    except PdfParseError as exc:
        _set_aside(source, "rejected", identity_name, hook, str(exc))
    except Exception:
        pass  # leave the file for the next tick


def _event_tick(engine):
    """Event-driven proactivity: react to pending inputs (inbox/) without waiting
    for a batch window. Respects each org's proactivity_mode. TRAINING is NOT
    here - it stays batched in _train_tick."""
    import os

    from ..config import Config
    from ..inference.base import build_backend
    from ..wiki.workspace import workspace_for
    from .agents import audit_hook, get_or_create_identity
    from .events import inbox_files, should_drain

    ws_path = os.environ.get("ANTHILL_WORKSPACE", "workspace")
    files = inbox_files(ws_path)
    if not files:
        return

    Session = sessionmaker(bind=engine)
    db = Session()
    now = datetime.now(timezone.utc)
    try:
        for cfg in db.query(OrgSettings).all():
            go, _reason = should_drain(
                cfg.proactivity_mode,
                bool(files),
                cfg.proactivity_last_run,
                cfg.agent_interval_secs,
                now=now,
            )
            if not go:
                continue
            config = Config.from_env()
            config.model = cfg.ollama_model or config.model
            config.base_url = cfg.ollama_url or config.base_url
            backend = build_backend(config)
            ws = workspace_for("personal")
            if not ws.exists():
                ws.init()
            ident = get_or_create_identity(db, cfg.org_id, "scheduler", agent_type="scheduler")
            hook = audit_hook(db, cfg.org_id)
            from .app import propose_wiki_write

            def _gate(title, md, _org=cfg.org_id):  # route the page through the review gate
                propose_wiki_write(
                    db,
                    org_id=_org,
                    proposed_by=None,
                    slug=title,
                    content=md,
                    target_scope="personal",
                    source="inbox ingest",
                )

            for f in files:
                _ingest_inbox_file(
                    ws, f, backend, on_write=_gate, identity_name=ident.name, hook=hook
                )
            cfg.proactivity_last_run = now
            db.commit()
            break  # single shared workspace in the alpha - one drain per tick
    finally:
        db.close()


def _digest_due(schedule: str, last_sent_at, now: datetime) -> bool:
    """Whether an org's configured knowledge-digest cadence (#683 phase 7) is due, given when it was
    last sent. `schedule` is `OrgSettings.digest_schedule` ("off"|"daily"|"weekly"); "off" is never
    due. Reuses `_next_run` (the same daily/weekly logic scheduled tasks and agents already use)
    rather than reimplementing interval math."""
    if schedule not in ("daily", "weekly"):
        return False
    if last_sent_at is None:
        return True  # never sent - send on the first tick once a cadence is turned on
    if last_sent_at.tzinfo is None:  # SQLite round-trips DateTime naive; treat it as UTC
        last_sent_at = last_sent_at.replace(tzinfo=timezone.utc)
    nxt = _next_run(schedule, from_dt=last_sent_at)
    return nxt is not None and now >= nxt


def _digest_tick(engine):
    """Send each org's knowledge digest once its configured cadence is due (#683 phase 7, spec
    requirement 5's digest half), then stamp `digest_last_sent_at` regardless of whether anything had
    changed (so the cadence keeps advancing - an empty digest is silently skipped, not sent as noise).
    Off (`digest_schedule == "off"`) by default; most orgs never enter the loop body at all."""
    from .db import User
    from .digest import build_digest
    from .notify import notify

    Session = sessionmaker(bind=engine)
    db = Session()
    now = datetime.now(timezone.utc)
    try:
        cfgs = (
            db.query(OrgSettings).filter(OrgSettings.digest_schedule.in_(("daily", "weekly"))).all()
        )
        for cfg in cfgs:
            if not _digest_due(cfg.digest_schedule, cfg.digest_last_sent_at, now):
                continue
            since = cfg.digest_last_sent_at or (now - timedelta(days=30))
            try:
                summary = build_digest(db, cfg.org_id, since, until=now)
                if not summary.is_empty:
                    admins = (
                        db.query(User)
                        .filter(
                            User.org_id == cfg.org_id, User.active.is_(True), User.role == "admin"
                        )
                        .all()
                    )
                    skills_total = summary.skills_learned + summary.skills_adopted
                    body = (
                        f"{summary.pages_changed} page(s) changed, {skills_total} skill(s) learned "
                        f"or adopted, {summary.promotions} promotion(s) between scopes."
                    )
                    for a in admins:
                        notify(
                            db,
                            user_id=a.id,
                            org_id=cfg.org_id,
                            kind="digest",
                            title="Knowledge digest",
                            body=body,
                            link="/wiki",
                        )
            except Exception:
                # One org's bad state must never stop the rest of this tick from being processed.
                pass
            # Always stamp, even on a build/notify failure or an empty window - otherwise a
            # persistent error (or a quiet org) would re-check this org every tick forever.
            cfg.digest_last_sent_at = now
            db.commit()
    finally:
        db.close()


def _backfill_review_outlines(engine):
    """Fill the agent outline for any pending review missing one (created while the
    backend was down, or before the gate existed). Best-effort; bounded per tick."""
    import json as _json

    from ..config import Config
    from ..inference.base import build_backend
    from ..wiki.review import outline_change
    from ..wiki.workspace import workspace_for

    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        pend = (
            db.query(WikiReview)
            .filter(WikiReview.status == "pending", WikiReview.outline == "")
            .limit(20)
            .all()
        )
        if not pend:
            return
        cfg_cache = {}
        for rev in pend:
            if rev.org_id not in cfg_cache:
                cfg_cache[rev.org_id] = (
                    db.query(OrgSettings).filter(OrgSettings.org_id == rev.org_id).first()
                )
            cfg = cfg_cache[rev.org_id]
            config = Config.from_env()
            if cfg:
                config.model = cfg.ollama_model or config.model
                config.base_url = cfg.ollama_url or config.base_url
            ws = workspace_for(
                rev.target_scope, team_id=rev.team_id, user_id=rev.proposed_by, org_id=rev.org_id
            )
            if not ws.exists():
                ws.init()
            o = outline_change(
                build_backend(config), ws, rev.slug, rev.content, scope=rev.target_scope
            )
            rev.outline = o.text
            if o.flags:
                rev.flags = _json.dumps(o.flags)
        db.commit()
    finally:
        db.close()


def _process_queued_uploads_tick(engine):
    """File any upload(s) saved while an org's local model was still downloading on first run
    (#683 phase 5), now that ``OrgSettings.local_model_pulling`` has cleared for that org.

    Queries by CURRENT state rather than tracking a separate "just turned falsy" transition: a
    ``QueuedUpload`` row can only be created while the flag is truthy (``wiki_upload``'s early
    check), so once the flag is falsy again, every remaining ``status="queued"`` row for that org
    is, by construction, one whose model just became ready - and a row is flipped to done/error
    (and its file removed) as soon as it is processed, so nothing here is ever reprocessed.
    Best-effort and bounded per tick, like ``_backfill_review_outlines``.
    """
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        pulling_orgs = {
            cfg.org_id
            for cfg in db.query(OrgSettings).filter(OrgSettings.local_model_pulling != "").all()
        }
        rows = db.query(QueuedUpload).filter(QueuedUpload.status == "queued").limit(20).all()
        for row in rows:
            if row.org_id in pulling_orgs:
                continue  # still downloading for this org - leave it queued for a later tick
            _process_one_queued_upload(db, row)
            db.commit()
    finally:
        db.close()


def _process_one_queued_upload(db, row: QueuedUpload) -> None:
    """Ingest one queued upload, mark it done/error, delete its stored file either way, and
    notify the uploading user. Mirrors ``wiki_upload``'s own read-then-summarise-then-propose
    steps (it cannot import that route directly - this module has no dependency on ``web.app`` at
    load time - so ``propose_wiki_write``/``_backend_from_cfg`` are imported lazily here, the same
    late-import pattern ``_event_tick`` already uses for the same reason)."""
    import shutil
    import tempfile
    from pathlib import Path

    from ..common.text import slugify
    from ..wiki.ingest import ingest as ingest_file
    from ..wiki.workspace import workspace_for
    from .app import _backend_from_cfg, _ensure_backend_ready, _wiki_page_url, propose_wiki_write
    from .notify import notify

    cfg = db.query(OrgSettings).filter(OrgSettings.org_id == row.org_id).first()
    stored = Path(row.stored_path)
    applied = None
    tmp_dir: str | None = None
    try:
        if not stored.is_file():
            raise FileNotFoundError(f"queued upload file is missing: {stored}")
        ws = workspace_for(
            row.target_scope,
            team_id=row.team_id,
            user_id=row.user_id if row.target_scope == "personal" else None,
            org_id=row.org_id,
        )
        if not ws.exists():
            ws.init()
        backend = _backend_from_cfg(cfg)
        proposal: dict = {}

        def _capture(title, page_md):
            proposal["title"] = title
            proposal["page_md"] = page_md

        # Ingest from a copy under the ORIGINAL filename, not the durable store's collision-safe
        # randomly-prefixed one: when the model's summary has no H1, ingest()'s own fallback title
        # is the source file's stem, and that random prefix must never leak into a page title.
        tmp_dir = tempfile.mkdtemp()
        source_copy = Path(tmp_dir) / row.filename
        shutil.copyfile(stored, source_copy)
        _ensure_backend_ready(backend)
        ingest_file(
            ws,
            source_copy,
            backend,
            on_write=_capture,
            vision_max_accuracy=bool(cfg and getattr(cfg, "vision_max_accuracy", False)),
        )
        slug = slugify(proposal["title"]) or slugify(Path(row.filename).stem) or "document"
        applied = propose_wiki_write(
            db,
            org_id=row.org_id,
            proposed_by=row.user_id,
            slug=slug,
            content=proposal["page_md"],
            target_scope=row.target_scope,
            team_id=row.team_id,
            source=f"Queued upload {row.filename}",
        )
        row.status = "done"
    except Exception as exc:
        row.status = "error"
        row.error = str(exc)[:300]
    finally:
        row.processed_at = datetime.now(timezone.utc)
        stored.unlink(missing_ok=True)
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    link = _wiki_page_url(row.target_scope, row.team_id)
    if row.status == "done":
        note = "" if applied else " It was flagged for review, so it's waiting in the review queue."
        notify(
            db,
            user_id=row.user_id,
            org_id=row.org_id,
            kind="wiki",
            title="Your queued document is ready",
            body=f"'{row.filename}' finished processing now that the local model is ready.{note}",
            link=link,
        )
    else:
        notify(
            db,
            user_id=row.user_id,
            org_id=row.org_id,
            kind="wiki",
            title="Your queued document could not be added",
            body=f"'{row.filename}' failed to process: {row.error or 'unknown error'}. Try uploading it again.",
            link=link,
        )


def _verify_agent_result(agent, result: str, db) -> None:
    """Advisory cross-check of an agent run's result against its mandate. Never raises; records a
    'needs review' flag when an independent (different-family) local model disagrees. See
    ``anthill.verify``."""
    try:
        from ..verify import crosscheck_for, verify
        from .db import OrgSettings

        cfg = db.query(OrgSettings).filter(OrgSettings.org_id == agent.org_id).first()
        model = (getattr(cfg, "ollama_model", "") or "") if cfg else ""
        url = (getattr(cfg, "ollama_url", "") if cfg else "") or "http://localhost:11434"
        verdict = verify(
            result or "",
            kind="task_result",
            goal=agent.mandate or "",
            crosscheck=crosscheck_for(url, model),
        )
        agent.verify_needs_review = bool(verdict.needs_review)
        agent.verify_reason = (verdict.reason or "")[:400]
        agent.verify_confidence = f"{verdict.confidence:.2f}"
    except Exception:
        pass  # advisory only - a verifier hiccup never breaks the agent run


def _remember_agent_outcome(agent, result: str, db) -> None:
    """Distil an agent run into a personal memory item so future chats/agents can recall it
    (best-effort; only on the personal plane, where personal memory exists)."""
    try:
        from .. import memory as mem
        from .db import MemoryItem

        first = (result or "").strip().splitlines()
        summary = f"Agent '{agent.name}': {first[0][:280]}" if first else ""
        from .memory_ops import auto_memory_on

        if not (summary and agent.created_by and auto_memory_on(db, agent.created_by)):
            return
        rows = (
            db.query(MemoryItem)
            .filter(MemoryItem.user_id == agent.created_by, MemoryItem.scope == "personal")
            .all()
        )
        vec = mem.embed_text(summary)
        if mem.is_new(summary, [r.text for r in rows]) and mem.is_semantically_new(
            vec, [mem.decode_vec(r.embedding) for r in rows]
        ):
            new = MemoryItem(
                org_id=agent.org_id,
                user_id=agent.created_by,
                scope="personal",
                kind="task_outcome",
                text=summary,
                embedding=mem.encode_vec(vec),
                source="agent",
                source_id=agent.id,
            )
            db.add(new)
            db.flush()
            from .memory_ops import maybe_corroborate

            maybe_corroborate(db, agent.org_id, new)
    except Exception:
        pass


def _start_agent_run(db, agent, started_at):
    """Open a history row in the ``running`` state before the agent runs, so the agent's page can
    show a live "running now" state (the detail page auto-refreshes until it settles). Committed by
    the caller. Returns the row (``None`` on a write hiccup - history is advisory, never fatal).
    Trigger is inferred from the schedule (a manual-only agent runs on demand, a scheduled one on
    its cadence)."""
    try:
        from .db import AgentRun

        run = AgentRun(
            org_id=agent.org_id,
            agent_id=agent.id,
            trigger="manual" if agent.schedule == "manual" else "scheduled",
            status="running",
            started_at=started_at,
            finished_at=None,  # a running row has no finish time yet
        )
        db.add(run)
        return run
    except Exception:
        return None


def _finish_agent_run(db, run, agent, *, started_at, result: str, error: str) -> None:
    """Close the run's history row with its outcome + the verifier verdict. Best-effort. Duration is
    computed from the in-memory ``started_at`` (the row's own started_at reads back tz-naive from
    SQLite, which would not subtract against a tz-aware 'now')."""
    if run is None:
        return
    try:
        finished = datetime.now(timezone.utc)
        run.status = "error" if error else "ok"
        run.result = (result or "")[:8000]
        run.error = (error or "")[:800]
        run.verify_needs_review = bool(agent.verify_needs_review)
        run.verify_reason = agent.verify_reason or ""
        run.verify_confidence = agent.verify_confidence or ""
        run.finished_at = finished
        run.duration_ms = int((finished - started_at).total_seconds() * 1000)
    except Exception:
        pass  # advisory history - never break the run on a recording error


def _distil_skill_from_agent(agent, result: str, db) -> None:
    """Propose-only skill distillation (#375): after a SUCCESSFUL agent run, distil a reusable skill
    and queue it as a pending ProposedSkill for human accept/reject - NEVER written live. Gated by
    OrgSettings.skill_autolearn; deduped against the owner's pending proposals. Best-effort, never
    raises - a distillation hiccup must not affect the run it came from."""
    try:
        from .. import planes
        from ..agent.skills import conform_name, distil_skill
        from ..config import Config
        from ..inference.base import build_backend
        from . import audit
        from .crypto import decrypt
        from .db import OrgSettings, ProposedSkill
        from .plane_routing import plane_inference

        if not (result and agent.created_by):
            return
        cfg = db.query(OrgSettings).filter(OrgSettings.org_id == agent.org_id).first()
        if not getattr(cfg, "skill_autolearn", True):
            return  # an admin paused skill auto-learning
        plane_inf = plane_inference(planes.normalize(agent.plane), cfg, decrypt=decrypt)
        audit.log_inference_call(
            db,
            plane_inf,
            org_id=agent.org_id,
            user_id=agent.created_by,
            surface="skill_distill",
        )
        config = Config.from_env()
        config.backend, config.base_url = plane_inf.backend, plane_inf.base_url
        config.model = agent.model or plane_inf.model
        if plane_inf.api_key:
            config.api_key = plane_inf.api_key
        cand = distil_skill(agent.mandate or "", result, build_backend(config))
        if not cand:
            return  # nothing worth generalising
        slug = conform_name(cand["name"])
        if not slug:
            return
        scope = {"org": "org", "team": "team"}.get(agent.plane, "personal")
        pending = (
            db.query(ProposedSkill)
            .filter(
                ProposedSkill.org_id == agent.org_id,
                ProposedSkill.created_by == agent.created_by,
                ProposedSkill.status == "pending",
            )
            .all()
        )
        if any(conform_name(p.name) == slug for p in pending):
            return  # already proposed - don't queue a duplicate
        db.add(
            ProposedSkill(
                org_id=agent.org_id,
                created_by=agent.created_by,
                agent_id=agent.id,
                scope=scope,
                team_id=agent.team_id if scope == "team" else None,
                name=cand["name"],
                description=cand["description"],
                when_to_use=cand["when_to_use"],
                instructions=cand["instructions"],
            )
        )
        db.commit()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass


def _agent_tick(engine):
    """One agent tick - run any active Agent that is due (its scheduled cadence, or a manual
    run-now which set next_run_at=now). Mirrors ``_tick`` for the persistent-agent surface."""
    from .agents_run import run_agent
    from .db import Agent

    Session = sessionmaker(bind=engine)
    db = Session()
    now = datetime.now(timezone.utc)
    due = (
        db.query(Agent)
        .filter(
            Agent.status == "active",
            Agent.next_run_at.isnot(None),
            Agent.next_run_at <= now,
        )
        .all()
    )
    for agent in due:
        agent.last_run_at = now
        agent.next_run_at = None  # clear now so an overlapping tick can't double-run it
        started = datetime.now(timezone.utc)
        run_row = _start_agent_run(db, agent, started)
        db.commit()  # publish the "running" row so the agent's page can show it live
        result, error = "", ""
        try:
            result = run_agent(agent, db) or ""
            agent.last_result = result[:4000]
            _verify_agent_result(agent, result, db)
            _remember_agent_outcome(agent, result, db)
        except Exception as e:  # incl. PlaneUnavailable when an org backend is down
            error = str(e)
            agent.last_result = f"ERROR: {e}"
            # a failed run produced no verdict; clear any stale flag so the UI is not misleading
            agent.verify_needs_review = False
            agent.verify_reason = ""
            agent.verify_confidence = ""
        agent.run_count += 1
        _finish_agent_run(db, run_row, agent, started_at=started, result=result, error=error)
        if not error:
            _distil_skill_from_agent(agent, result, db)  # propose a reusable skill (governed, #375)
        nxt = _next_run(agent.schedule, from_dt=now)  # None for "manual" (single run)
        if nxt:
            agent.next_run_at = nxt
        agent.updated_at = now
        db.commit()
        # Only a run that needs attention reaches the notification centre (#284): a flagged or failed
        # agent run pings its owner; a clean run stays quiet.
        if agent.created_by and (agent.verify_needs_review or error):
            from .notify import notify

            notify(
                db,
                user_id=agent.created_by,
                org_id=agent.org_id,
                kind="run",
                title=("Agent run failed" if error else f"{agent.name} needs your review"),
                body=(f"{agent.name}: {error}" if error else (agent.verify_reason or agent.name)),
                link=f"/agents/{agent.id}",
            )
    db.close()


def _sweep_stale_running_runs(engine) -> None:
    """Close any AgentRun/TaskRun still marked ``running`` at startup - it means the backend stopped
    mid-run, so that row would otherwise show a live "running" state (and auto-refresh) forever.
    Marked as an error so the history is honest. Never raises."""
    try:
        from .db import AgentRun, TaskRun

        db = sessionmaker(bind=engine)()
        try:
            changed = False
            interrupted_at = datetime.now(timezone.utc)
            for r in db.query(AgentRun).filter(AgentRun.status == "running").all():
                r.status = "error"
                r.error = (r.error or "interrupted - the backend stopped before this run finished")[
                    :800
                ]
                r.finished_at = r.finished_at or interrupted_at
                changed = True
            for r in db.query(TaskRun).filter(TaskRun.status == "running").all():
                task = db.get(ScheduledTask, r.task_id)
                _recover_interrupted_task_run(task, r, interrupted_at)
                r.status = "error"
                r.error = (r.error or "interrupted - the backend stopped before this run finished")[
                    :800
                ]
                r.finished_at = r.finished_at or interrupted_at
                changed = True
            if changed:
                db.commit()
        finally:
            db.close()
    except Exception:
        pass


def start_scheduler(engine=None, fast_interval_s: int = 10, slow_every: int = 6):
    """Start one scheduler thread and elect one scheduler process per database."""
    global _scheduler_thread
    with _scheduler_lock:
        if _scheduler_thread is not None and _scheduler_thread.is_alive():
            return _scheduler_thread
        _scheduler_thread = _start_scheduler(engine, fast_interval_s, slow_every)
        return _scheduler_thread


def _prepare_scheduler(engine):
    if engine is None:
        engine = get_engine()
        create_tables(engine)

    _sweep_stale_running_runs(engine)  # a run left "running" by a crash/restart can never finish

    try:
        from .imap_idle import start_worker

        start_worker(engine)  # no-op until an org enables IMAP
    except Exception:
        pass
    return engine


def _scheduler_loop(engine, fast_interval_s: int, slow_every: int):
    i = 0
    while True:
        try:
            _event_tick(engine)  # ~10s (or immediately on a push) - event-driven actions
        except Exception:
            pass
        if i % slow_every == 0:
            try:
                _tick(engine)  # ~60s - scheduled tasks
            except Exception:
                pass
            try:
                _agent_tick(engine)  # ~60s - persistent agents (the third surface)
            except Exception:
                pass
            try:
                _train_tick(engine)  # ~60s - BATCHED training trigger (queues runs)
            except Exception:
                pass
            try:
                _execute_tick(engine)  # ~60s - execute any queued training runs (off-thread)
            except Exception:
                pass
            try:
                _reap_tick(engine)  # ~10min-gated - terminate leaked RunPod training pods (#254)
            except Exception:
                pass
            try:
                _backfill_review_outlines(engine)  # fill missing agent outlines
            except Exception:
                pass
            try:
                _digest_tick(engine)  # ~60s-checked, daily/weekly-gated knowledge digest (#683)
            except Exception:
                pass
            try:
                _process_queued_uploads_tick(engine)  # file uploads queued during a model pull
            except Exception:
                pass
        i += 1
        # Wake early when a push source signals (webhook / IMAP), else tick on schedule.
        if _wake.wait(timeout=fast_interval_s):
            _wake.clear()


def _start_scheduler(engine=None, fast_interval_s: int = 10, slow_every: int = 6):
    if engine is None:
        engine = get_engine()
        create_tables(engine)

    if not _acquire_scheduler_process_lock(engine):

        def _wait_for_scheduler_lock():
            while not _acquire_scheduler_process_lock(engine):
                threading.Event().wait(fast_interval_s)
            _scheduler_loop(_prepare_scheduler(engine), fast_interval_s, slow_every)

        t = threading.Thread(
            target=_wait_for_scheduler_lock, daemon=True, name="anthill-scheduler-wait"
        )
        t.start()
        return t

    engine = _prepare_scheduler(engine)
    t = threading.Thread(
        target=lambda: _scheduler_loop(engine, fast_interval_s, slow_every),
        daemon=True,
        name="anthill-scheduler",
    )
    t.start()
    return t
