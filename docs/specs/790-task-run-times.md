# Spec: Task run times

Status: implemented. Lane: `pillar:feature`. Issue: #790.

## Problem

The Tasks list shows only the last run, and the result page renders timestamps as server-formatted values. Users cannot reliably tell when a task will run next or read all task times in their own timezone.

## Requirements

- Show each task's next run alongside its schedule when a future occurrence exists.
- Show clear states for actively running, completed, cancelled, and unscheduled tasks without a future occurrence.
- Derive `Running now` from an active `TaskRun`, not the potentially stale `ScheduledTask.status` projection.
- When a task is active and also has an independent future occurrence, show that real next-run time instead of `Running now`.
- Apply the existing [`data-utc` browser-localization contract](tasks-local-time-and-weekday-schedule.md#policy), including its readable UTC fallback, to task last-run, next-run, and run-history timestamps.
- Do not change scheduling or persistence behavior.

## Proof

- `tests/test_tasks_scale.py` covers future next-run markup, explicit states, active-run leases, and active tasks with an independent future occurrence.
- `tests/test_task_run_history.py` covers localizable last-run, next-run, and history timestamps plus active-run precedence on the result page.
