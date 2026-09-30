# Spec: Task queue row span

Status: implemented. Lane: `pillar:feature`. Issue: #779.

## Problem

The expanded follow-up queue row on the Tasks page spans six cells while the task table has seven columns. This leaves an empty gap in the Actions column.

## Requirements

- The expanded queue row spans every Tasks table column, including Actions.
- The queue form remains usable at desktop and narrow responsive widths.
- A rendered-page or browser regression test fails if the queue-row span differs from the table header count.

## Proof

- `tests/test_tasks_scale.py::test_expanded_queue_row_spans_the_task_table` renders the Tasks page and compares the queue cell's `colspan` with the number of table headers.
