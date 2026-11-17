# Spec: a chat turn ends when its answer ends

Status: implemented. Lane: `pillar:knowledge`.

## Problem

Every few exchanges Anthill extracts durable memory from the conversation (`_distil_memory_from_chat`). The
extraction is a full, non-streaming call to the chat model. The chat stream ran it after the answer had been
written and before it sent `[DONE]`, inside the async handler, so:

- the page, which finishes the answer (copy and rating controls, final rendering) and stops showing the Stop
  button only on `[DONE]`, kept showing a running turn for the length of an extra model call. On a local model that
  call is slow, and a follow-up typed in the meantime was queued, not sent;
- the call was synchronous, so it blocked the event loop and every other request the server had to serve.

## Requirements

1. The stream sends `[DONE]` as soon as the answer, its stored message, the training record and the metric are done.
   Memory extraction is not part of the turn.
2. Extraction runs after the response has been sent, as a background task of that response, on its own thread and
   its own database session. It never uses the request's session.
3. What is extracted, when it runs (the same throttle: about every three exchanges, only for a turn that did not
   fail and did not produce code-made text), the pause setting (`auto_memory_off`) and the failure behaviour
   (best effort, any error is swallowed) are unchanged.
4. Memory extracted from a turn can reach the next answer a little later than before. A question asked before the
   extraction finishes does not see it.

## Known limits

- On a single local model a question asked while the extraction runs can still wait behind it. That was already so,
  and the wait no longer comes before the previous answer is shown as complete.
- If the client disconnects before the stream ends, the extraction for that turn is not scheduled; the next turn's
  throttle picks the messages up, because the mark only moves when an extraction ran.
- Moving extraction to idle time, with a cost cap, is part of the shared background scheduler
  (Knowledge roadmap item K15) and is not done here.

## Acceptance

- Driven at the ASGI boundary, the memory extractor has not started when `[DONE]` is sent, runs afterwards, runs off
  the event loop's thread, and the memory item it returns is stored (`tests/test_chat_turn_end.py`). The test
  fails on the code before this change.
