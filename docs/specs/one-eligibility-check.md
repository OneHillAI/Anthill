# Spec: one rule decides what may be read, on every route that reads knowledge

Status: implemented. Lane: `pillar:knowledge`. Builds on `docs/specs/only-approved-items-reach-answers.md` and
`docs/specs/records-required.md`: an item is read only when it has a valid record that says active and approved.

## Problem

The rule applies on the main chat, the agent tools, the widget and the cached-answer check. This step brings the remaining
routes that read knowledge under it: the command that builds a document from the wiki, the replay of earlier turns of a chat,
the MCP cache and memory tools, the organisation's shared answer index, and the search index.

## Requirements

1. **The command-line document command** (`anthill generate`) reads `retrievable_pages()`, not every page.
2. **Replay of earlier turns.** When a chat's earlier turns are given to the model again, an answer whose record names an item
   that may no longer be read (removed, waiting, rejected, or gone) is left out, together with the question it answered
   (`answer_record.stale_turns`, used by the web chat and the Slack path). An answer with no record, made before records, is kept.
   An item whose wiki cannot be found is treated as not readable.
3. **The MCP cache and memory tools are not offered** (`exposed_resources` returns only the wiki tool), and a direct call to them
   returns "(this tool is not available)". The connector page says the two options are not available in this version.
4. **The organisation's shared answer index is not asked for answers** unless an operator sets `ANTHILL_ORG_INDEX_ANSWERS=1`.
   Publishing is unchanged.
5. **Removing a page also removes it from the search index** when one is in use (`meili.delete_pages`).

## Known limits

- Memory recall is unchanged in this step and is replaced in a later step.
- The replay filter works from an answer's record, so it applies to chats from now on; an answer saved before records existed is
  replayed as before.
- The search index is a copy of the pages. Answers do not read it directly; its hits are mapped through the pages that may be read.
- The Slack path shares the replay filter with the web chat; it is not run end to end in a test.
- With `ANTHILL_ORG_INDEX_ANSWERS=1` the shared answer index behaves as before this step.

## Acceptance

`tests/test_one_eligibility_check.py` seeds a document and page that were removed, and a document and page that are waiting, each
with a unique phrase, and checks that the phrase never reaches what the model is given, an answer, a tool result or a command's
prompt, on: streaming chat (also the widget and Slack, which share it), the blocking answer, the agent tools `search_wiki` and
`read_wiki` (also tasks), the MCP wiki tool, and the command-line document command; that an item in a wiki the chat does not
use is not read; that the readable item still is; that the MCP cache and memory tools are not offered and answer nothing; that the
shared index is not asked unless the flag is set; that removing a page removes it from the search index; that earlier turns using a
removed item are not replayed; and that the connector page does not offer the two tools.

## Not verified here

Not run on a real install, with a real MCP client or against a real search index. Routes not listed above were not tested.
