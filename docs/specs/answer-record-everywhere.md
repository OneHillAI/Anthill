# Spec: the answer record on every route that keeps an answer, and "used in"

Status: proposed (build plan step 2.2). Lane: `pillar:knowledge`. Builds on `docs/specs/answer-manifest.md` (the record of what an answer
was given), `docs/specs/typed-context-builder.md` (the record is made from the blocks the prompt holds) and
`docs/specs/answer-panel-other-blocks.md` (the panel shows the rules, profile, earlier turns and web pages of a record).

## Problem

Since step 2.1 every route that builds a prompt can say what it gave the model, but the record is kept with the answer on one family of
routes only: web chat (plain, web-search and agent mode), through `ChatMessage.manifest`. The answers of scheduled tasks and persistent
agents are kept with no record (`TaskRun`, `AgentRun`), and the answers of the Slack route are saved as chat messages without one. Nothing answers the opposite question either: "which answers used this page or document?". A person who
removes or corrects an item cannot see what depended on it, and a person looking at an item cannot see where it has been used.

## Requirements

1. **Routes that keep an answer keep its record.**
   - Slack: the shared org-plane answer function returns the record with the answer, and the Slack route stores it with the saved assistant
     message (`ChatMessage.manifest`), as web chat does.
   - Scheduled tasks and persistent agents: a `record` text column on `TaskRun` and `AgentRun` (added with the existing `ALTER TABLE`
     helper, empty for older runs) holds the record of the run (`AgentResult.record`, from step 2.1b). It is stored for a run that ends with
     an answer and not for one that failed.
   - The record is stored with the answer, is local, and goes when the chat or the run history goes. It holds ids, versions, hashes, spaces,
     addresses and lengths, never the text of an item.
2. **Routes that keep no answer have nothing to attach a record to.** Discord and the web widget (which use the same org-plane function but
   keep no message), the MCP wiki tool, the node agent and the command line return an answer without keeping it. They are not given a store in this step; the spec says so and does not claim a record for them.
3. **"Used in".** One function takes an item id and a viewer and returns the answers whose record lists that item, newest first, each as
   the route (chat, task, agent), the title or goal, the time, the version of the item the answer was given and whether that version is
   still the current one. Only answers the viewer may already open are returned: their own chats, and the runs of tasks and agents they may
   already see. Nothing here widens who sees an answer.
4. **Has the item changed since?** A pure function takes a record and the workspaces and says, for each item: unchanged (same id and content
   hash), changed, gone, or not held here. It makes no model call. It is what the "used in" result and a later panel use to say
   "2 of 5 items have changed since this answer".
5. **Words.** Every string this step adds says "given" or "used in", never "caused" or "because of".
6. **Nothing is asked of the person.** A person who never opens a new screen gets the same answers, at the same speed.

## Acceptance

- A Slack answer is stored with a record that lists the pages it was given and equals what the prompt held.
- A task run and an agent run store the record of the run; a failed run stores none; an older run reads as having none.
- "Used in" for a page returns the chat message, the task run and the agent run that were given it, none from another person's chat, and
  none that did not use it; an item that was given twice to one answer yields one answer.
- "Changed since" reports unchanged, changed (the page was edited after the answer), gone (the page was removed) and not held, with no model
  call.
- The record added to a task or agent run costs less than five milliseconds at fifty items (measured and reported in the PR).
- The full suite passes; the tests check behaviour (a stored record, a returned answer), not structure.

## Not in this step

- The screen for "used in" and the story of an item are made by the UI/UX session once this data exists (Library, step 4.3).
- A record for Discord, the widget, the MCP wiki tool, the node agent and the command line: they keep no answer.
- A saved-answer cache entry still gets its record after the fact from its sources; it goes with the saved answers (step 2.4).
- Re-running an answer with a model: the record names the items an answer was given and their versions; whether an older version can be
  shown again depends on page history (step 3.3).

## Known limits

- "Used in" scans the stored records. It is measured at one thousand answers in the PR; an index is added only if the scan is too slow.
- A record lists what was placed in the prompt, not what the fit to the model's window kept (see `typed-context-builder.md`).
- Slack, Discord, the widget, the MCP wiki tool and the node agent share the answer path; only the stored record of Slack is tested here.
