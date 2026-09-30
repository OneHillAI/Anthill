# Spec: Task schedule timezones

Status: implemented. Lane: `pillar:feature`. Issue: #778.

## Problem

Task wall-clock schedules were interpreted as UTC even when a user created them in another timezone. A
browser in `Europe/Madrid` that created "Daily at 09:00" therefore scheduled 09:00 UTC, which is 10:00
or 11:00 in Madrid depending on daylight saving time. Tasks did not store or display the intended
timezone, and daily or weekly intervals could shift local time across a DST boundary.

## Policy

- Manual and Chat task creation submit the browser's IANA timezone from
  `Intl.DateTimeFormat().resolvedOptions().timeZone`. A confirmed one-time Chat schedule creates exactly
  one immediate manual occurrence.
- `ScheduledTask.timezone` stores the validated IANA name. A missing value becomes an empty legacy
  marker whose behavior remains UTC; a supplied invalid name is rejected.
- Task wall-clock calculations use Python's standard-library `zoneinfo`. Daily, weekly, weekday, and
  explicit `HH:MM` schedules are calculated in the task timezone and converted back to UTC for storage.
  `ScheduledTask.schedule_anchor` preserves the nominal local wall time independently of a DST-gap-adjusted
  UTC occurrence. A repeated wall time during a fall-back transition uses fold 0, the first occurrence,
  while a spring-forward gap never shifts the nominal anchor used by later runs. A durable ordered
  `TaskOccurrence` ledger owns each scheduled, manual, or queued input batch;
  claims execute globally by `(due_at, id)`, including multiple due occurrences for one task and duplicate
  input strings. `TaskRun.occurrence_id`, `TaskRun.scheduled_for`, and `TaskRun.claimed_inputs` bind a
  started attempt to that exact occurrence. Cancellation first atomically marks the task cancelled, claims
  require a non-cancelled task, and a claim that serialized first is marked cancellation-requested. An
  occurrence ownership CAS finalizes before result, verifier, history, run-count, or memory writes publish
  in the same transaction; a stale attempt publishes none of them and closes as an error. Interrupted
  attempts retry the same occurrence, while stale attempts cannot finalize it after a retry has claimed it.
  `ScheduledTask.next_run_at`, `interrupted_run_at`, `interrupted_inputs`, and `queued_inputs`
  remain compatibility projections only. `TaskRun.cancel_requested` prevents crash recovery from replaying a
  cancelled occurrence while preserving the active execution lease and pausing later work. Recurrences stay anchored to
  the scheduled occurrence rather than the delayed scheduler tick or task completion, and missed occurrences
  advance to the next future time. Hourly schedules remain elapsed one-hour intervals, so timezone does not
  affect them. Hourly and one-time schedules do not show a timezone.
- Tasks UI edits preserve the task's recorded timezone even when the browser has moved to another
  timezone. A caller that explicitly changes the timezone through the route preserves the nominal local
  wall-clock time and weekly weekday anchor while recalculating calendar schedules, including explicit
  fixed-time and fixed-weekday schedules. A timezone-only edit leaves the existing due instant unchanged
  only for hourly and one-time schedules. During an active manual or queued run, a schedule edit replaces
  or removes the independent future scheduled occurrence immediately. Only an active scheduled occurrence
  defers that cadence change to its finalization. A named timezone cannot be
  explicitly cleared; omitted edit fields still preserve it, and legacy tasks that were already empty remain
  supported. Run-now and queue recover a missing legacy anchor from prior run, last-run, or creation
  timestamps before repurposing the next-run field.
- The Tasks page and Chat scheduling surfaces state the timezone for calendar cadences. Existing
  timezone-less calendar tasks display and continue to run in UTC.
- Only the documented task schedule grammar is persisted. Invalid route or A2A input is rejected, while
  legacy case and surrounding whitespace are canonicalized on edit without shifting the due instant. A
  malformed legacy value stops cleanly instead of raising during task finalization.
- Ambiguous overdue legacy recurring times are preserved both as immediate work and as local
  time/weekday cadence evidence. The migrated task receives a separate schedule **needs review** flag
  that result verification cannot clear; a schedule or timezone edit, or **Schedule reviewed**, clears it.
- The versioned ledger migrations are idempotent over partially materialized state, reproject legacy
  compatibility fields, archive orphaned task-run history, and give upgraded databases the same task-run
  foreign keys and occurrence indexes as fresh databases.
- Task-result attention notifications and memory-promotion pushes are dispatched only after the task
  outcome and occurrence finalization commit; a rolled-back outcome sends no notification.
- Agent and digest scheduling remain unchanged because they do not pass a task timezone.

## Acceptance criteria

- Creating a recurring task records a valid IANA timezone; later Tasks UI edits preserve it.
- Manual and `/chat/schedule` creation use the same timezone behavior, and a Chat one-shot runs
  immediately exactly once.
- "Daily at HH:MM" runs at that local wall-clock time in at least two IANA timezones.
- Daily and weekly schedules preserve their nominal local wall-clock anchor across DST transitions and gaps.
- Explicit timezone changes preserve fixed and fixed-weekday wall-clock times in the new timezone.
- Invalid timezone input cannot reach `ZoneInfo` during a scheduler tick, and named zones cannot be cleared.
- Invalid schedules are rejected before persistence and cannot strand a task run during finalization.
- A delayed scheduler tick does not move a daily or weekly task's calendar time.
- Existing tasks without a timezone retain their UTC behavior after the ledger migration; ambiguous
  overdue recurring times preserve their apparent cadence and are visibly flagged for review.
- The Tasks list and creation UI show which timezone applies to calendar-based schedules.

## Proof

- Scheduler unit tests pin UTC instants in `Europe/Madrid` and `America/New_York`, including spring DST
  transitions and a nonexistent local time.
- Route tests cover manual creation, explicit timezone edits for fixed and fixed-weekday schedules, Chat
  creation, task-list rendering, and old-schema migration.
- Scheduler tick tests verify that recurring tasks are re-armed in their stored timezone without losing
  their nominal wall-clock anchor, including migration-era interrupted runs and later queued reruns.
- Occurrence-ledger tests cover interrupted, queued, and Run Now batches surviving independently; global
  due ordering; duplicate and cancellation-marked migration input ownership; cancellation/claim ordering;
  active-run schedule edits; input-bearing cadence replacement; queue/cancel serialization; atomic stale
  outcomes; concurrent compatibility materialization; exact interrupted-history matching; ambiguous overdue
  cadence review flags; post-commit promotion pushes; cancelled cadence recovery; orphan-history archiving;
  schema/index parity; blank migrated claims; and stale finalizers.
- A Playwright test creates a task in one configured browser timezone, edits it from another, and checks
  that the rendered Tasks form preserves the task's displayed and persisted timezone.
