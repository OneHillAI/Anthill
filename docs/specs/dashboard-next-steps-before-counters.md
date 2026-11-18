# Dashboard: next steps before counters

Lane: `pillar:platform`

## Problem

A new account's dashboard showed four counters, all 0 (Answers given, Training examples, Wiki entries, Memory
items). Counters carry meaning only after something has happened, so for a new user the row said nothing and sat
where a next action belonged.

## Fix

While all four counters are 0, the dashboard shows "Try this next" with three cards instead of the counter row:
"Add a document" (links to the wiki), "Ask a question" (links to chat) and "Create a task" (links to tasks). As
soon as any counter is above 0 the four counters appear as before and the cards go away. The checklist above
and the two cores below are unchanged.

This replaces the earlier rule that a fresh install "honestly reads 0".

## Acceptance criteria

- With no answers, training examples, wiki pages or memory items, the dashboard has "Try this next", the three links,
  and none of the four counter labels.
- With any of them above 0, the dashboard shows the four counter labels and no "Try this next".
- `tests/test_dashboard_org_setup.py`.
