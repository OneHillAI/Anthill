# Spec: Task run history pagination

Status: implemented. Lane: `pillar:feature`. Issue: #96.

## Problem

The task result page lists only the 25 newest runs and says nothing about the cut-off. The heading shows the
task's `run_count`, which can differ from the runs actually recorded, and older runs cannot be reached.

## Requirements

- Page the run history 25 runs at a time with a `?page=` parameter, using a bounded offset query. Never load
  an unbounded result set.
- Order newest first. A run in progress is stored with its claim time as its finish time, so it is already the
  newest; a stored empty finish time, which the app does not create, is ordered first as a guard. The rest are
  ordered by `finished_at`, then by `id` descending, so runs that finished at the same instant keep a fixed
  order. In a history that is not changing, paging through shows every run exactly once. Paging is by offset,
  so a run that finishes between two page loads shifts the later pages by one.
- The page states one run count, the real number of recorded runs (a count of `TaskRun` rows for the task,
  not `run_count`), in the "Runs" summary and in the heading. When more than one page exists it reads "showing A to B of N runs"; otherwise "N runs" or
  "1 run".
- Show "Newer runs" and "Older runs" links and "Page X of Y" only when there is more than one page.
- A page below 1 shows page 1. A page above the last shows the last page. A non-integer is rejected by the
  route (HTTP 422).
- The newest run is expanded on page 1 only. Older pages open collapsed.
- The live auto-refresh keeps the current page. No change to how runs are recorded.

## Acceptance criteria

- 0 runs: "0 runs" and "No runs recorded yet", no pager.
- 1 run: "1 run", no pager.
- Exactly 25 runs: "25 runs", all shown, no pager.
- 26 runs: page 1 shows the 25 newest with "showing 1 to 25 of 26 runs" and an Older link; page 2 shows the
  oldest one with "showing 26 to 26 of 26 runs" and a Newer link.
- 60 runs across three pages list every run exactly once, newest first, including when all finished at the
  same instant.
- A run in progress is the first run on page 1, expanded, and is absent from page 2.
- Out-of-range pages land on the nearest real page.

## Proof

- `tests/test_task_history_pagination.py` covers the cases above, the page-size constant, and that the total
  comes from recorded runs and not from `run_count`.

## Why offset paging

The history is paged by offset, as the Tasks list is, with a bounded query and a total count so the page can
say "Page X of Y" and "showing A to B of N". Facts that bound the cost of that choice:

- A task has at most one running run: `task_occurrences.claim` does not claim a task that already has a running
  run. A run's row is created when it is claimed, so it is already the newest and finishing does not move any
  row. Only a claim between two page loads (a scheduled run, or a manual Run now) adds a row and can shift the
  later pages by one run.
- Keyset paging (a cursor on `finished_at` and `id`) would remove that shift, but it would need an extra count query
  for each page to give a page number or a position in the total, and it would make this page behave
  differently from the Tasks list. It can replace the offset later without changing what the page shows.
