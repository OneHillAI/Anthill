# Spec: a chat turn embeds its question once

Status: implemented. Lane: `pillar:knowledge`. Builds on `docs/specs/ollama-served-embeddings.md`.

## Problem

Embeddings are served by Ollama's `/api/embed` (see the spec above), so every vector is an HTTP call to the
embedding model. One chat turn asked for the vector of the same question text four times:

1. the answer cache lookup (`SemanticCache.lookup`), through `safe_embed`;
2. memory recall (`recall_memory`), through `memory.embed_text`, when the user has any memory items;
3. retrieval in `ask_stream` / `ask`, through `safe_embed`;
4. the cache write (`SemanticCache.store`), through `safe_embed`.

On a small machine that also runs the chat model, each call is a chance for the runtime to swap models, and each
one is time the user waits.

## Requirements

1. `embedder.embed_recent(text)` returns `embed(text)` and remembers the last 16 results for 300 seconds. A
   second call for the same text, model and Ollama address is answered from memory. The remembered entry holds
   the embed function itself in its key, so a replaced function (a test double) never shares an entry with the
   real one.
2. `safe_embed` and `memory.embed_text` use it. Page embedding (`page_index`, `PageVectorIndex`) does not, so a
   burst of pages cannot push a question out and pages are not kept in memory twice.
3. A call that raises is not remembered; `safe_embed` still returns `None` when embeddings are unavailable.
4. Each caller receives its own copy of the vector. Changing it cannot change what the next caller gets.
5. The remembered vectors live in the process only. Nothing is written to disk.

## Known limits

- The 300 second limit and the 16 entries are starting points, not measured values.
- Other callers of `embed` (lifecycle evaluation, the agent's wiki search tool, the wiki agent) are unchanged.
- This removes calls. It does not say how long one call takes, which depends on the machine and on whether the
  embedding model is loaded.

## Acceptance

- One chat turn with a remembered memory item and an empty answer cache embeds the question once, driven through
  the real chat route with a counting fake embedder (`tests/test_question_embedded_once.py`). On the code before
  this change the same count is four.
- The reuse layer is covered: same text once, own copies, a failure not remembered, expiry, and the size limit.
