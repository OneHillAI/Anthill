# Spec: Resolve a task result that needs review

Status: implemented. Lane: `pillar:feature`. Issue: #93.

## Problem

A task result the verifier does not trust is marked "Needs review" on the Tasks list and in run
history. There is no way for a person to say they have looked at it. The warning stays until a later
run happens to replace it, and nothing records who accepted the result.

The verifier verdict is evidence. Acknowledging a result must not rewrite that evidence as a pass.

## Requirements

- A flagged result SHALL expose "Mark reviewed" only to a user who may edit the task (its creator,
  or an admin for an org-plane task).
- The action SHALL record who reviewed that result and when.
- Acknowledging the latest flagged result SHALL clear the Tasks-list warning.
- The original verifier verdict SHALL stay on the run: `verify_needs_review`, `verify_reason`, and
  `verify_confidence` are not rewritten to look successful.
- A flagged run that has been acknowledged SHALL stay visible in run history and be labelled reviewed.
- Acknowledging an older run SHALL NOT clear a warning owned by a newer completed run. The check is
  made when the acknowledgement is saved, so a run that finishes after the page was shown cannot be
  cleared by a stale form.
- A flagged task that predates run history SHALL still be acknowledgeable. That acknowledgement is
  stored on the task, keeps the verifier text, and does not invent a run. It does not apply once a
  completed run exists.
- The action SHALL be a POST protected by a signed token bound to the user, the task, and the exact
  run. A missing, forged, expired, or swapped token is rejected. A user who cannot see the task is
  rejected. A shared read-only viewer, including a project member who may run the task but not edit
  it, is rejected.
- Existing flagged rows SHALL keep their verifier verdict when the review columns are added. Those
  columns start empty, so a legacy flag still needs review.

## Non-goals

- Changing how the verifier decides a result needs review.
- Clearing the notification bell.
- Letting a project operator who cannot edit the task acknowledge it.

## Proof

- `tests/test_task_result_review.py` covers the column migration, startup upgrade, authorization,
  CSRF, latest-result acknowledgement, older-run isolation, and legacy flagged tasks with and
  without a run.
