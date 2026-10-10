# Spec: arbitrary one-time task scheduling

Lane: `pillar:feature`. Issue: #88.

## Problem

The manual Tasks form cannot schedule a one-shot at an arbitrary future local date and time.

## Requirements

- Run once offers Run now or Choose a future date and time, using a native datetime-local input.
- Future dates use the task's validated IANA timezone (legacy blank means UTC). Convert to UTC
  and store a scheduled occurrence; `next_run_at` remains its compatibility projection.
- Reject malformed, impossible, past, DST-gap and DST-ambiguous local times with a clear error
  before persisting changes. Do not silently shift a one-shot to another local time.
- Editing shows the pending scheduled occurrence in its recorded timezone, not the browser's
  timezone or an unrelated manual/queued occurrence. Preserve seconds on round-trip.
- Replacing a pending date uses the existing ledger mutation/CAS path. Reject one-shot rescheduling
  while a scheduled run is active. Manual and queued occurrences remain independent.
- Run now creates only one immediate occurrence. A completed task's ordinary edit does not rerun
  it; use its Now button or explicitly choose a new future date.
- Existing callers omitting the new fields retain their behavior. Recurring and Chat creation
  are unchanged. Successful one-shot completion creates no recurrence.

## Acceptance criteria

- Route tests cover creation and editing across multiple timezones, UTC fallback, invalid values,
  switching to now, and unchanged round-trips without duplicate occurrences.
- Scheduler tests run a future one-shot at its due instant, clear `next_run_at`, and prove a later
  tick does not run it again.
- Browser tests cover choice visibility, validation feedback, and editing from a different timezone.

This extends `docs/specs/778-task-schedule-timezones.md`: future one-shots show a timezone and
an explicitly submitted future local time is interpreted in that timezone. Timezone-only legacy
edits without the new fields continue to preserve the existing UTC instant.
