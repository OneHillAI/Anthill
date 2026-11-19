# Spec: answers read only items that are active and approved

Status: implemented. Lane: `pillar:knowledge`. Issue: #277, step 1 (slice 1b) of the knowledge base update, epic #270. Builds on
`docs/specs/item-records-and-inventory.md` (the records). Architecture: `anthill-internal/knowledge-pillar/architecture.md`.

## Problem

Records (#272) give every source and page a review state and a lifecycle state, but nothing reads them. Retrieval takes whatever is on
disk: every kept text under `text/` is indexed and searchable whatever became of the upload that made it, and every file under `wiki/`
is ranked. An item that is waiting for a decision, was rejected, or has been removed can still shape an answer, and nothing on the
screen says so.

## Requirements

1. **One rule** (`items.is_retrievable`). An item is retrievable when its lifecycle state is `active` and its review state is
   `checked` or `unchecked`. `proposed`, `rejected`, `superseded`, `archived`, `trashed` and `purged` items are not. An item with no
   record is retrievable, as before records existed.
2. **Two places decide, and both ask the rule.** `items.page_file_retrievable(path)` for a page (from its record in the front matter,
   remembered until the file changes; a damaged state is not retrievable) and `items.hidden_sources(root)` for the originals of a
   workspace (the stored names whose record says not retrievable or cannot be read; remembered until the folder of records changes).
   `Workspace.retrievable_pages()` is the pages an answer may read.
3. **Every read path asks.** Page ranking by embedding, the keyword fallback and the Meilisearch path (`wiki/ask.py`: only retrievable
   pages are ranked, and the vector index is not pruned of a hidden page's row); the document passages (`wiki/doc_context.py`, with
   `DocIndex.search(exclude=...)` leaving hidden documents out of the query itself, so a hidden document never takes the place of a
   visible one); `ask()` and `ask_stream()` and so plain chat, the blocking path, the chat widget and the node agent; the agent
   tools `search_wiki` and `read_wiki` (`agent/tools.py`), which also serve chat agent mode, scheduled tasks, persistent agents and A2A; the
   external MCP tools that read the org wiki, which call `ask()`; and the semantic answer cache (`wiki/cache_freshness.py`: an answer
   that cited a hidden page or document is skipped, kept for a chat where it still holds, and a hidden document never counts as a
   new document that could answer).
4. **States move when a decision is made.** A page that lands in the workspace marks the original it was made from `checked` (also
   from `rejected`). Rejecting a review, or choosing "keep the existing page" for a held duplicate (the same route), marks the original
   `rejected`, unless a page made from the same file already stands: one file uploaded twice is one original. Uploading a rejected
   file again makes it `proposed` again. An upload that failed or timed out stays `proposed`.
5. **Old installs are not changed on their own.** An original made before records existed has no record and is read as before; the
   command `anthill knowledge inventory --apply` (#272) gives it one, from the review rows, and is the way to bring an old install
   up to date. Nothing in this change writes a record or a file at start-up or on a page view.
6. **A person who does nothing is unaffected.** On an install with no records every answer reads what it read before, nothing is blocked
   and nobody is asked for anything.
7. No model is called, and the prompt text is built exactly as before for items that are retrievable.

## Known limits

- An original with no record is read, so on an install that never runs `anthill knowledge inventory --apply` an old upload that was
  waiting or rejected is still read. Uploads made from now on are recorded and covered. Telling people about the command, or running it
  for them, is left out on purpose: it would add a screen and a manual step for a leftover that may be rare.
- The read path judges a page from its two state lines. A record that is damaged elsewhere (an impossible `origin`, say) does not make
  a page unreadable to answers; the inventory reports it as quarantined.
- A record or sidecar edited in place by hand is noticed when the folder next changes or after a restart. Records are written by
  replacing the file, which the check sees at once.
- A Meilisearch index may still hold a hidden page; it is mapped to pages through the retrievable list, so it is never read.
- The cross-node answer index that an organisation can run (`central_index`) is not covered here.
- Trash, restore and purge of an item (#278) and the answer manifest (#281) are separate slices; here nothing yet sets an item to
  `trashed`, so a page or original is hidden by a state set through the records.

## Acceptance

- The rule for every state, for no record, and for a damaged one; the page check follows a change to the file; a damaged sidecar hides
  its original (`tests/test_only_approved_items_reach_answers.py`).
- On one workspace holding an approved document with its page, a document whose upload is waiting, one that was rejected, a failed
  upload, a trashed page, a page with no record and a document with no record: the held, rejected and failed documents and the trashed
  page never reach a prompt, a tool result or a cached answer, on `ask_stream`, `ask`, the page ranking, the keyword fallback, the agent
  tools, the cache judge and the chat route; the approved and the unrecorded ones do.
- A hidden document that would rank first does not take a visible one's place.
- Rejecting a review marks its original rejected (through the real route); a second copy of a file does not hide the first; a new upload
  of a rejected file is proposed again; a page that lands approves a rejected original.
- Measured on one Mac with a generated wiki of 1,000 pages that all have records: the check adds 51 ms to the first question after the
  app starts (4 ms afterwards, against 1.8 ms for listing the pages), and the originals' check 20 ms (0 ms afterwards); a wiki of 1,000
  pages with no record 17 ms.
