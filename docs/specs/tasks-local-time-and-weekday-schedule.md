# Spec: Local-time run timestamps + a weekdays schedule for tasks/agents

Status: implemented. Lane: `chore` (UX polish). Issue: #394.

## Problem

Two small task/agent issues:

1. **Run timestamps shown in UTC.** "Last run" / finished-at were rendered with server-side `strftime`
   on the stored (UTC) datetime, so a task that ran minutes ago looked hours old to a user in another
   timezone.
2. **"every weekday" collapsed to "Daily".** The NL task drafter (`taskgen.normalize_schedule`) mapped
   any "day" phrasing to `daily`, silently dropping a weekday-only constraint - so "every weekday at
   9am" would also run on weekends.

## Policy

- **Local-time timestamps.** The server can't know the viewer's timezone, so run-time cells emit the
  UTC time as a machine-readable `data-utc="<ISO-8601 UTC>"` (via the `utc_iso` Jinja filter) plus a
  `"... UTC"` text fallback. A small `base.html` script rewrites every `[data-utc]` element to the
  browser's local time on load. JS off -> the honest "UTC"-labelled server time still shows. Applied to
  the Tasks list, Agents list, and agent run history.
- **A weekdays schedule.** The schedule vocabulary gains `weekdays` and `HH:MM weekdays` (Mon-Fri only):
  - `taskgen`: the drafter prompt offers them, and `normalize_schedule` preserves a weekday constraint
    (and parses `9am`/`9 pm`), so "every weekday at 9am" -> `"09:00 weekdays"`, "each business day" ->
    `"weekdays"`.
  - `scheduler._next_run`: `weekdays` and `HH:MM weekdays` advance to the next Mon-Fri, skipping
    Sat/Sun.
  - The Tasks/Agents schedule dropdowns list "Weekdays (Mon-Fri)" and "Weekdays at 09:00", and the
    drafted-value humanizer labels `HH:MM weekdays` as "Weekdays at HH:MM".

## Acceptance criteria

- Task/agent run timestamps carry a `data-utc` ISO string and render in the viewer's local timezone
  (server fallback is UTC-labelled).
- `normalize_schedule` returns `"09:00 weekdays"` for "every weekday at 9am", `"weekdays"` for "each
  business day"/"Monday to Friday", and does not confuse "weekly" with weekdays.
- `_next_run("weekdays")` and `_next_run("HH:MM weekdays")` never return a Saturday or Sunday; plain
  `daily` is unchanged.
- Covered by `tests/test_taskgen.py`.
