# Spec: the item page shows where an item was used

Status: implemented. Lane: `pillar:knowledge`. Issue: #363. Builds on `item-page.md` (#315), `answer-record-everywhere.md`
(build plan step 2.2, the lookup in `anthill/web/used_in.py`) and `answer-manifest.md`. Part of the knowledge update,
epic #270. It only reads.

## Problem

The item page says what an item is and where it came from, but not whether it is ever used: "did my AI use this page?"
had no answer. A first version of the screen searched the stored answer records itself and was removed in review.
Step 2.2 now keeps the record of an answer for chats, Slack, task runs and agent runs and gives a lookup of the answers
whose record lists an item; this is the screen for it.

## Fix

1. A "Used in" section on the item page lists the answers the viewer may open whose record lists the item, newest
   first: their own chats and the runs of the tasks and agents they created. The section says so.
2. A row has the title as a link (to the chat at that answer, to the task's result, or to the agent), where it ran
   (chat, task or agent), the day, "given version N" (the version the answer was given), and where the item stands now
   in words: "as it was then", "has changed since", "no longer available to answers" (removed, waiting for a decision
   or rejected), "its wiki is not on this machine". When the record kept no hash to compare, nothing is said about it.
3. The first 20 rows are shown, then "and N more". An item no answer used says "None of your answers has been given
   this yet."
4. An item with no record has no id to look for: "This page has no record yet, so where it is used is not tracked."
5. The wording is "used in" and "given", never "caused" or "relied on". Nothing is changed by looking and nothing
   new is stored.

## Acceptance

- A page used by a chat answer, a task run and an agent run shows each row with the right link, route word, version
  and standing.
- Another person's chat or run is never shown; a page nobody used and a page with no record say so.
- The standing words are right for unchanged, changed, gone and not here; unknown says nothing.
- The list is cut at 20 and counted. A document has the section too.
- Looked at on a running copy at desktop widths, light and dark.

## Not here

The lookup itself and what it covers (Discord, the widget, the MCP wiki tool, the node agent and the command line keep no
answer, so no record); a version list; mobile layouts.
