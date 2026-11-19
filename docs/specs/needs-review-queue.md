# Spec: one "Needs review" queue for everything waiting for a decision

Status: implemented. Lane: `pillar:knowledge`. Builds on `knowledge-guidance-reconciliation.md` (the inbox) and
`149-duplicate-uploads.md` (held uploads).

## Problem

What waited for a decision was shown in four places with four counts: a badge on the Wiki tab (organisation
reviews, admins only), a "Review" tab inside the Wiki menu (personal or organisation, only sometimes there), a
"Suggestions" tab with its own total, and a badge on Knowledge in the rail. The same item appeared in several of
them, so "1", "1" and "2" could all mean the same two things, and a held upload could be decided in a place the
person had not been told about.

## Fix

1. The Knowledge bar has one tab, "Needs review" (it was "Suggestions"). Its page lists everything waiting:
   uploads held for a decision, flagged pages, pages and skills the AI proposed, and (for an admin) the
   organisation's. The route stays `/knowledge/suggestions`.
2. The decision pages `/wiki/review` and `/wiki/review/personal` stay as they are (text, comparison, the buttons)
   but are part of that tab: they show the Knowledge bar with "Needs review" active and a link back to the list.
   Each item opens at its own card (`#review-<id>`).
3. There is one count, the same on the "Needs review" tab and on Knowledge in the rail, for every role. The
   Wiki tab carries no badge and the Wiki menu has only Pages, Principles and Files.
4. Wherever a message said "review queue" it now says "Needs review". Links to the decision pages still work.

## Acceptance

- A page with a waiting item shows the same number on the tab and in the rail; the Wiki tab shows none.
- `/wiki/review` and `/wiki/review/personal` show "Needs review" as the active tab.
- The Wiki menu has no Review tab; an item's "Open" or "Decide" leads to its own card.
- Approving, rejecting and keeping both behave exactly as before.
