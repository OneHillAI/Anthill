# Spec: the Metrics page shows real chat latency, or says it is not measured

Status: implemented. Lane: `pillar:platform`. Issue: #157.

## Problem

The Metrics page showed `Median latency 0ms` and `p95 0ms` after chats that each took several seconds. The chat
stream recorded every answer with `duration_ms=None`, the summary dropped those rows, and the percentile of an
empty list was 0. The page presented an unmeasured value as a measurement, which makes the other figures look
untrustworthy too (#157).

## Requirements

1. Each chat answer records its duration in milliseconds, timed from the start of the stream to the point the
   answer has been fully sent, just before `[DONE]`. A cache hit records the time its turn really took, which is short but not zero: it still includes retrieval.
2. A turn that fails to generate, and a turn refused before it starts, record nothing, as before.
3. `metrics.summary` returns `p50_ms`, `p95_ms` and `avg_latency_ms` as `None` when no row in the window has a
   duration. A duration of 0 is a measurement and is counted; rows with no duration are ignored.
4. The Metrics page shows `-` with "not measured yet" when `p50_ms` is `None`, and the figures as before otherwise.
5. Rows already in the database keep a NULL duration. There is no backfill.

## Known limits

- The figure is the whole turn as the server sees it (retrieval, any web steps, generation and saving), not
  time to first word, and not what the browser saw.
- The 30 day window mixes old rows without a duration and new rows with one. The percentiles use only the new
  ones, so they cover fewer answers than `Queries` counts until the old rows age out.
- `/api/metrics` still stores whatever `duration_ms` a node sends, including none.

## Acceptance

- A chat turn through `/chat/{id}/stream` leaves one `query_metrics` row whose `duration_ms` is the measured
  elapsed time, not NULL.
- With only NULL durations, `summary` gives `None` for the three latency figures and the page renders `-` and
  "not measured yet", with no `0ms`.
- With some durations, `summary` and the page show the real median and p95; a NULL row does not pull them down.
- Tests: `tests/test_metrics.py` (model-free).
