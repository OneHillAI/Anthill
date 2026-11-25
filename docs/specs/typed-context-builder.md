# Spec: one typed context builder, and an answer record made from it

Status: proposed (build plan step 2.1). Lane: `pillar:knowledge`. Builds on `docs/specs/answer-manifest.md` (the record of what an
answer was given), `docs/specs/stable-prompt-prefix.md` (the order of the answer prompt) and `docs/specs/records-required.md` (every item
has a record and an id).

## Problem

Knowledge reaches a model through several code paths that each build their prompt in their own way: the answer path (`anthill/wiki/prompts.py`,
used by chat, the blocking answer, the widget, Slack, the MCP wiki tool and the node agent), the web-search messages
(`anthill/search/web.py`), the agent executor (agent mode, scheduled tasks, persistent agents; wiki pages arrive as tool results), the
council, and the command-line document command. In the answer path the text of a prompt is made first and the record of what it
contained (`answer_record.build`) is made beside it from the pages and passages that were found. The two can drift apart, a block of
text has no identity of its own, and the other paths record nothing.

## Requirements

1. **Typed blocks.** A prompt is built from blocks, each with a type: `instruction` (the system message), `rules` (principles), `profile`,
   `core_page` (reserved for the step that replaces memory), `memory` (until that step), `page` (a page or part of a page),
   `document_passage` (a passage of a kept original), `history_turn`, `question`, and `web_evidence` (a page fetched from the web).
2. **Every knowledge block carries an item id.** A `page`, `document_passage`, `core_page` or `memory` block holds the id, version, hash
   and space of the item it came from, and where in it (the slug, or the page number and span). A block that cannot name its item is
   refused: it is not given to the model and the refusal is logged. A `web_evidence` block carries its address and the time it was fetched
   and is always marked untrusted; it is not an item.
3. **One builder.** `build(blocks, window)` returns the messages and the record of what the prompt contains, from the same blocks, so the
   record cannot say something the prompt did not hold. The record has the shape of `docs/specs/answer-manifest.md`, with the route
   and the model added. It lists every knowledge block as an item (as above) and every other block that carries guidance or conversation
   as a block of its own kind: the rules, the profile and the earlier turns, each with its type, a hash and its length and no text.
   Showing these blocks to the person, and storing the record for every route, is step 2.2; this step makes the record complete so that
   step has nothing to reconstruct.
4. **The text does not change.** For the same inputs the builder produces the messages that `answer_question` produces today, byte for
   byte, in the order of `docs/specs/stable-prompt-prefix.md`: system, rules and profile, earlier turns, the reference for this
   question, the question. Every knowledge block stays a user-role, fenced, untrusted block. Item ids are carried in the structure and
   are **not** written into the prompt text, so the stable start of the prompt costs nothing extra.
5. **The fit to the window** (`fit_prompt`) works on the blocks as it does on the text today: the instruction, rules, profile and the
   question are fixed cost; the reference and the earlier turns are shortened as before; a prompt that fits is not shortened.
6. **Routes move over in slices, and "every route" is claimed only when the last slice is in.**
   - 2.1a: the builder and the answer path (chat, blocking answer, widget, Slack, MCP wiki tool, node agent).
   - 2.1b: the agent executor: its first messages, and the results of `search_wiki` and `read_wiki`, become typed blocks with ids, so agent
     mode, tasks and persistent agents have a record.
   - 2.1c: the web-search messages, the council and the document command.
7. Retrieval, the pack budget, memory, history summarising and the trust level of each block are unchanged.

## Slice b: the agent executor

What an agent run reads and is given becomes typed blocks with item ids, and the run has a record made from them.

1. `search_wiki` and `read_wiki` make a `page` block for every page they return, with the item of that page. A page that cannot name its
   item is refused and logged: the tool tells the model "page not found" (read) or leaves the page out of the list (search), as it does for
   a page that is not eligible. What the tools return to the model is otherwise the same text as before.
2. The executor keeps the blocks of the current run and clears them when a run starts. `record()` returns the record of the run: the pages
   found or read (a page found and then read is one item), the rules (`principles`) and the context the run was started with (memory,
   profile, and for a task the files attached to it) each as a block with its type, a hash and its length, and no text; and the route
   (`agent`) and the model.
3. Chat agent mode stores the record with the answer and shows the answer panel, as plain chat does. Scheduled tasks and persistent agents
   have the record on their result; storing and showing it for them is step 2.2.
4. Not in this slice: the web results (`web_search`, `fetch_url`) are not items and are slice c; the system text of an agent run and its
   skills are not recorded; the tool results kept in a long run's earlier steps are what the model was given at the time, and a step that
   the compaction later shortens is still listed.

## Known limits

- Whether to show a short item tag to the model, and what that costs the stable start of the prompt and the answer, is a measurement for
  later with the evaluation set (`docs/specs/knowledge-eval-set.md`); this step does not show ids.
- In slice a the answer path receives the memory only as text, so the memory is recorded as a block without an item (type, hash and
  length). It gets an id when memory rows reach the builder, or goes when memory is replaced (step 2.3).
- The earlier turns in the record are the turns the chat passed in, before the prompt is shortened to fit. A turn the fit drops is
  still listed. Slice a records what was placed in the reference, not what the fit kept.
- In an agent turn that takes several steps, tool results accumulate; the record keeps the first 50 items and says how many more, as the
  answer record does.
- This step produces the complete record; it does not by itself show the rules, the profile or the earlier turns to the person or
  store the record on routes that do not store one today. That is step 2.2, and this spec does not close it.
- Prompts that do not carry knowledge (review, verification, triage, summarising for history) are not part of this.

## Acceptance

- A golden test: for fixed inputs the builder's messages equal today's `answer_question` messages, byte for byte, with and without
  rules, profile, memory, pages, passages and history.
- Building a knowledge block with no item id is refused and logged, and the block does not reach the messages; a `web_evidence` block is
  always untrusted.
- The record is derived from the blocks: for fixed blocks, the record names exactly the knowledge, rules, profile and earlier-turn blocks in
  the messages, and nothing else; the non-item entries hold a hash and a length and never the text.
- Per slice, a route test calls each route in it and checks that every knowledge block in the prompt has an id and that the record matches
  the prompt. The step is complete, and "every route" may be written, only when 2.1c is in and its test covers every route.
- A user who does nothing sees no change in answers or speed; the time the builder adds per turn at 1,000 pages is measured and reported.

## Not verified here

This is a spec; nothing is built. The cost of showing item ids to the model, and the effect on answer quality, are not measured.
