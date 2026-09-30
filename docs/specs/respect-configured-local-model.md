# Spec: Chat must serve the user's configured local model

Status: implemented. Lane: `pillar:model`. Issue: #413 (freeze - direction 1; also covers direction 2).

## Problem

On the founder's 16 GB Mac, chat "took ages" and the whole app froze during generation. The configured
local model was `mistral-nemo:12b`, but the model actually resident during chat was `qwen3:14b` - a
*larger* model than configured. The chat route builds a `TaskRouter` that picks a model by task type from
a fixed preference list (GENERAL prefers `qwen3:8b -> qwen3:14b`, REASON prefers `deepseek-r1 -> qwen3:14b`)
and returns the largest *installed* preferred model - overriding the configured `ollama_model`. So a 14B
loaded on a memory-pressured Mac, saturating unified memory and freezing the WebView + backend.

The `TaskRouter` already supports a hard `pinned_model` (its own comment: "force the configured model
rather than have the router load an oversized one, e.g. qwen3:14b when qwen3:8b was configured"), but it
was only fed from the `ANTHILL_FORCE_MODEL` env var - never from the user's configured model.

## Requirements

- A local chat turn must serve the user's **configured** model (`OrgSettings.ollama_model`), not a larger
  installed one. For every non-vision task, the configured model is used.
- Vision still routes to a vision model (a text model can't do vision).
- If the configured model isn't installed, fall through to normal task routing (don't 404).
- The org/cloud (openai) backend serves its own provisioned model and is **unaffected**.

## How

The chat route constructs `TaskRouter` only for the Ollama backend and pins it to the configured model.
The existing `pick()` logic then returns the pinned model for non-vision tasks when it is installed, and
falls through otherwise. OpenAI-compatible chat routing is owned by
[`757-mlx-chat-streaming.md`](757-mlx-chat-streaming.md).

## Acceptance criteria

- With `mistral-nemo:12b` configured and `qwen3:14b` also installed, every non-vision task serves
  `mistral-nemo:12b` (covered by `tests/test_router_pin.py`).
- The OpenAI-compatible backend receives its configured endpoint model without a `TaskRouter` (covered
  by [`757-mlx-chat-streaming.md`](757-mlx-chat-streaming.md)).

## Related (not in this change)

Direction 3 (the verifier holding a second large different-family model resident under memory pressure)
and the memory-pressure guard for it are a separate follow-up. Direction 4 (no web + multi-step for simple
questions) is already handled by the chat depth router (#421).
