# Spec: each answer records which items it was given, and the chat shows them

Status: implemented. Lane: `pillar:knowledge`. Issue: #281, step 1 of the knowledge base update, epic #270. Builds on
`docs/specs/item-records-and-inventory.md` (the records). Architecture: `anthill-internal/knowledge-pillar/architecture.md`.

## Problem

An answer saved only two comma separated lists, the page names (`ChatMessage.wiki_slugs`) and the document names
(`ChatMessage.doc_sources`), each cut at 500 characters. They say nothing about which version of a page it was, and two items
with the same name in different spaces look the same. A person asked for a record of which items a chat used.

## Requirements

1. **A record on each answer.** `ChatMessage.manifest` (a new text column, added by the existing column top-up) holds JSON,
   built by `anthill/wiki/answer_record.py` from what the turn gave the model: the retrieved pages (as paths, so each is tied
   to the space it was read from) and the document passages. For each page: slug, title, space, id, version, content hash,
   origin and review state. For each document: its stored name, its name as uploaded, the page numbers the passages came from,
   space, and the id, version and hash of its record. An item with no record has an empty id and version 0. Never the text of a
   page or a passage. At most 50 items are kept, with the number left over (`more`). One record is about 330 bytes per item.
2. **It only records.** The record is made at the point where the prompt's context is built (`ask()`, `ask_stream()`, new
   argument `on_record`) and nothing in it changes what the model is given: the prompt is byte for byte what it was. No model is
   called. An answer served from the cache has only its list of sources, so its items are looked up from that list in the
   workspaces of the chat, the first that holds each.
3. **Panel under each answer.** "What this answer was given (N)" lists each item: the title as a link to the page in its own
   space, or to the kept text of the document; the space; the version (a page) or the page numbers (a document); and an
   "AI-written, unchecked" label on a page a model wrote and nobody has checked (it still answers). It replaces the two plain
   lines "Pages in context" and "Documents in context" for an answer that has a record. The wording is "given", never "caused"
   or "cited". The panel is one template (`_answer_record.html`) used by the saved chat and by the answer as it arrives.
4. **Used in this chat.** The chat page lists the items used across all its answers once each, with how many answers used
   them. It is worked out from the saved records when the chat opens; nothing more is stored.
5. **Old answers and other routes.** An answer without a record shows its plain lists as before. Plain chat and the blocking path
   record. A question that used nothing, a failed turn and an answer made by code record nothing. The older events
   `wiki_slugs` and `documents` are still sent for other clients.
6. **A person who does nothing is unaffected.** Nothing is asked, nothing is blocked, and answers are as they were.

## Known limits

- **Given means placed in the reference.** The record lists the pages and passages that were put in the context. A prompt that
  is shortened afterwards to fit a small model window is not tracked, and the record does not say which of the items the answer
  relied on. The panel says so in its tooltip.
- **All retrieved pages are listed**, including one the earlier "Pages in context" line left out as a weak match. The earlier
  line showed a filtered list of sources; this one shows what the model was given.
- **A document passage is linked as the whole kept text, not as a position in it.** The record keeps the document's page
  numbers, not the character span of each passage, because the list of sources an answer carries (and the answer cache) holds
  page numbers only. The text route serves plain text and has no way to open at an offset.
- **A cached answer is recorded after the fact.** If one slug is in two spaces of the chat, the first is named.
- **An item whose own record is damaged** is listed with an empty id; the inventory reports the damage.
- Agent mode, scheduled tasks, the Slack and Discord widget and the MCP tools are not recorded here. Items that matched and were
  left out, the spaces a session could read, a portable view, a "has it changed since" check and a "Mark as checked" button are
  not built: each can be added when a person needs it.

## Acceptance

All in `tests/test_answer_record.py`, plus the older source and document tests moved to the panel:

- Every field of a page and of a document item, on the streaming and the blocking answer; the record is the set of items in
  the prompt, and a page that was not given is not recorded; a question that used nothing records nothing.
- The prompt is byte for byte the same with and without a recorder, on both routes.
- No page or passage text is in the stored record; an item with no record and one with a damaged record are listed; 60 pages
  give 50 items and "10 more", in the record and in the panel.
- Two pages with one name in two spaces are told apart by id and by link; a cached answer is recorded from its sources; the
  unchecked label follows the review state.
- Through the chat: the record is saved, the panel shows after a reload with working links, version and page numbers and the label;
  "Used in this chat" counts each item once with its answers; the live answer carries the same panel; an old answer shows its plain
  lists; a failed turn and a question nothing answers save no record; an unreadable stored record is an empty one.

## Measured

On a generated wiki of 1,000 pages, one Mac: a streamed turn took a median 28.7 ms without the record and 29.4 ms with it
(7 runs each, fake model, word-hash embeddings); building the record alone takes 0.43 ms for one page and one document, and
stores 650 bytes.

## Not verified here

Nothing has been run on a real install or in a browser, only through the test client. The panel's look (spacing, dark theme) has
not been seen on screen.
