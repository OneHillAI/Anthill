# Spec: Tasks actions, creation paths and landmarks

Status: implemented. Lane: `pillar:feature`. Issues: #95, #91, #90. Builds on specs 92 (contrast) and 94 (narrow layout).

## Problem

- **#95.** The Tasks page offered two competing ways to create a task (a describe-it drafter and the form) and
  put the administrator defaults (step limit, retry budget) at the top, in developer wording.
- **#91.** Every viewer saw every row action. Actions the viewer may not use were refused silently. "Run now" and
  "Add follow-up" on a cancelled task brought it back without saying so. "Cancel" did not say what it does.
- **#90.** The pages had no `<main>` landmark, and several repeated controls ("Edit", "Now", "Queue", "Mark
  reviewed") had names that did not say which task they belong to.

## Requirements

### Creation paths (#95)
- One primary button, "New task", leads the page and opens the create dialog.
- The describe-it drafter stays, as a second option under the heading "Or describe it in words", with a
  secondary "Draft from description" button and the hint that "save as a task" in Chat does the same.
- The administrator settings (most steps per run, cloud retry budget) sit in a closed disclosure, "Task
  settings for administrators", in plain words, below the list. Members do not see it.
- The scope of each task is explained beside the word: "Only you", "Members of the project", "Everyone in your
  organisation".

### Actions (#91)
- A row shows an action only when the viewer may use it. Writers (owner, or an admin for an organisation task)
  see Edit, Run now, Add follow-up, Cancel future runs, Reactivate and Run again as the state allows.
  Project members who are not writers may run and queue follow-ups on a project task. Everyone who can see a
  task can open its result.
- Every action is named, with the task: "Run now: <title>", "Cancel future runs: <title>" and so on. A name
  starts with the button's visible text ("Mark reviewed: <title>", "Mark schedule reviewed: <title>").
- Titles say what is true. Run now: "Runs it once now. A date or schedule already chosen still applies."
  (a dated one-time task still runs at its date as well). Run again: its status goes pending, running, done.
- The result page shows "Run again" only to someone who may operate the task and only when it is not cancelled
  or running. The refusal notice has `role="alert"`.
- A cancelled task shows "Reactivate task" and nothing that runs it. `POST /tasks/{id}/reactivate` is the only
  way back (writers only). Run now and Add follow-up on a cancelled task are refused with the notice "That task
  is cancelled. Reactivate it first", and the ledger itself refuses them (`TaskCancelled`, decided from a
  fresh read of the task inside the mutation), so a task cancelled by another request in between is not
  brought back. There is no delete route.
- **Reactivate runs nothing now.** Cadence work still in the future resumes as it was. A cadence occurrence that
  fell due while the task was cancelled is skipped, not run: the next future slot of the schedule replaces it
  (`next_run_at` is set; a one-time task whose date has passed has no next slot and stays pending with no
  next run until Run now or an edited date). **Paused follow-ups and manual runs queued before the cancel are
  kept.** One that is already due does not run now and does not become a second run: its instructions are
  merged into the scheduled occurrence of the next slot, so a single run carries them (notice: "Follow-ups
  queued before it was cancelled will run with its next scheduled run"). When there is no scheduled run to
  join (a one-time task whose date has passed) it stays held, paused, and the notice says so ("held until you
  use Run now or Add follow-up"); that action resumes it, and it then runs as its own run, beside the manual
  one. The projection is refreshed before the commit.
- Reactivate is refused while a run that was cancelled is still running (`notice=still_cancelling`): that run
  finishes in the background, and reactivating under it would put the task in two states.
- Refusal codes: `not_allowed` (cannot change the task), `not_allowed_run` (cannot run it), `lost_access` (the
  task was no longer available to this person when the change was applied), `cancelled`, `not_cancelled`,
  `still_cancelling`. Add follow-up redirects like the other actions; it no longer answers 404.
- A run that was cancelled while running and then could not publish its result is recorded with the plain text
  "Cancelled while it was running. Its result was not kept."
- Cancel asks first; the dialog's buttons read "Cancel future runs" and "Keep task". The text says it stops
  future runs, that anything already done stays, that the history is kept and nothing is deleted, and,
  when a run is in progress, that the run is not interrupted: it finishes in the background and its result is
  not kept. (Checked in code: the cancel request is recorded and the claimed occurrence is closed; the scheduler
  never stops running work, and the unpublished result is dropped.)
- A refused action redirects to `/tasks?notice=<code>`. Only fixed codes map to text; a free-text value is
  ignored.

### Landmarks and names (#90)
- Every page that extends `base.html` has exactly one `<main>` (`id="main-content"`). Pages with their own
  layout are not covered.
- The task table and the page navigation have accessible names. Every visible control outside the dialog has a
  name from a label, `aria-label` or its own text, never from a placeholder.
- Each "Mark reviewed" button on the result page names its run and starts with its visible text ("Mark
  reviewed: run on <time>").
- A `.sr-only` utility class exists for text that only assistive technology reads.

## Acceptance criteria
- `tests/test_task_actions.py` covers the controls an owner sees for each task state, the controls for an
  admin on an organisation task, a read-only member, a project member and a peer who cannot see a solo task;
  that the labels name the task; the cancel confirmation wording with and without a run in progress; that
  cancelling does not stop a running task and its result is not published; that Run now and Add follow-up on
  a cancelled task are refused without reactivating it; that reactivate turns the schedule back on without
  running anything, is for writers only and says so when the task is not cancelled; that refused actions show
  a notice; and that a notice is a fixed message, never address text.
- `tests/browser/test_tasks_landmarks.py` drives Chromium: one `main`, the table and navigation names, no
  unnamed control, task-specific names on row actions, the follow-up panel state, run-specific names on the
  result page, the cancel confirmation wording, a read-only member without controls, the order (New task, then
  the drafter, then the list, then the settings), one primary button, the closed administrator disclosure,
  plain wording.
- Existing tests that relied on Run now or Add follow-up reactivating a cancelled task call the reactivate
  route first (a run that was cancelled while running must have finished). `scheduler._tick` changed: a run
  that lost its claim because it was cancelled is recorded with the plain text above; any other lost claim keeps
  its own text. `task_occurrences.reactivate`, `run_now` and `queue_input` changed as described. Changed tests:
  `test_prompt_queue`, `test_task_context`, `test_verify_task_results` and `test_migration_runner`.

## Known limits
- Pull request #98 (one-time scheduling) labels its action "Now". It must rename that to "Run now" / "Run
  again" to match this change, and decide whether Run now on a dated one-time task consumes the scheduled
  occurrence. Here it does not: the scheduled occurrence still runs at its date.
- A follow-up held on a one-time task whose date has passed waits for Run now or Add follow-up; nothing else resumes it.

## Not in scope
- No axe or other accessibility scanner (it needs a dependency in `pyproject.toml`, a protected path).
- No delete route for tasks.
