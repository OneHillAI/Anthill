# Spec: the Agents pages must render once an agent has a due time

Status: implemented. Lane: `pillar:platform`.

## Problem

Found in v0.12.7 QA on the signed app: create an agent, press "Run now", and both `GET /agents` and
`GET /agents/<id>` answer 500 from then on ("can't compare offset-naive and offset-aware datetimes").
The process stays up, but the whole Agents section is unusable.

Run now stores `next_run_at = datetime.now(timezone.utc)`. SQLite hands `DateTime` columns back naive,
so on the next request the list (`agents_home`) and detail (`agent_detail`) pages compared a naive
`next_run_at` with an aware `datetime.now(timezone.utc)` while deciding whether an agent is live, and
Python raises `TypeError` for that comparison. It was invisible to the suite because no test combined an
active agent, a set `next_run_at`, and a page fetch on a fresh session.

## Fix

- `anthill/web/app.py`: a small `_as_utc()` helper treats a stored naive datetime as UTC (which is how it
  was written), and both live checks compare `_as_utc(a.next_run_at)` with the aware "now".
- Two regression tests in `tests/test_agents_surface.py`: pages render and show the agent as live right after
  Run now; a due time in the future renders and is not shown as live.

## Deliberately not touched

- Other places that compare a stored datetime with "now" (the scheduler compares in SQL, and the other
  helpers already normalise). A repo-wide sweep is a separate change.
