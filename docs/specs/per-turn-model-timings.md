# Spec: record timing and cached-token counts on every model call

Status: implemented. Lane: `pillar:model`. Issue: #219.

## Problem

Anthill cannot say how long a model call took or whether the engine reused the start of the prompt. In
`anthill/inference/ollama.py`, `last_stats` is filled only by the non-streaming path and nothing reads it. The
streaming path, which answers every chat turn, ignores the final counters, and `chat_with_tools` records
nothing. The OpenAI-compatible backend keeps nothing either. Later speed work (fit estimates, an engine
upgrade, prefix reuse) has no numbers to start from.

## Requirements

1. A `TurnStats` record (`anthill/inference/stats.py`) holds numbers and the model name only: model, window
   (`num_ctx`), prompt tokens, cached tokens, prefill milliseconds, decode tokens and milliseconds, load
   milliseconds, time to first word, and total milliseconds. It never holds prompt, answer or wiki text.
2. A figure the engine did not report is `None`, never 0. A missing cached-token counter is not "nothing was
   cached". A value that is not a non-negative number is treated as not reported.
3. `OllamaBackend.last_turn` and `OpenAICompatBackend.last_turn` hold the last call's `TurnStats` on every path:
   streamed, not streamed, and with tools. It is `None` while a call runs, after a failed call, after an empty
   answer, and when a stream is abandoned before its final chunk.
4. Ollama: read from the final object (`prompt_eval_count`, `prompt_eval_cached_count`, `prompt_eval_duration`,
   `eval_count`, `eval_duration`, `load_duration`, `total_duration`, nanoseconds). OpenAI-compatible: read
   `usage` (`prompt_tokens`, `completion_tokens`, `prompt_tokens_details.cached_tokens`) and `timings`
   (`prompt_ms`, `predicted_ms`) when the server sends them, including from a streamed usage chunk that has no
   `choices`. Anthill's own clock fills the total, and the time to first word on a streamed call.
5. `last_stats` keeps its meaning (non-streaming Ollama only).
6. The model's answer call in a normal chat turn is stored on its `query_metrics` row, in new nullable integer
   columns with the same names as the `TurnStats` fields. A cache hit, a fixed answer and a turn that escalated
   leave them NULL. Existing rows stay NULL; the columns are added on startup.
7. The chat page shows one muted line next to the local/remote badge: the time to first word and tokens per
   second, whichever are known (for example `first word 2.4 s, 15 tokens/s`; under a second it is in
   milliseconds). Nothing is shown when neither is known.

## Known limits

- Recorded for the normal chat answer only. Agent steps and web research make several calls; the backends hold
  the last one, but the chat route does not store them.
- The speed line is live only. It is not stored with the message, so a reloaded conversation does not show it.
- `prompt_eval_cached_count` was checked on Ollama 0.40.7. The pinned 0.30.10 was not checked and may not report
  it, in which case cached tokens are `None`.
- Anthill does not add `stream_options.include_usage` to OpenAI-compatible streaming requests, because not every
  endpoint accepts it. A server that does not send usage on its own gives a streamed record with only the
  times Anthill measured.
- The first-word time is measured from sending the request to the first token, so it includes a model load.
  The speed line in the chat page uses it as it is.
- The page has no "details" disclosure; the line sits in the existing row under the answer.

## Acceptance

- `tests/test_turn_stats.py` (new, model-free), with payloads recorded from a live Ollama 0.40.7: each path
  fills `last_turn`; absent fields are `None`; an odd value is not a measurement; the record and the metric
  table cannot hold prompt or answer text; a failed call and an abandoned stream leave `None`.
- A chat turn through `/chat/{id}/stream` sends the speed line and stores the counters on one `query_metrics`
  row; a turn with no stats sends no line and stores NULLs.
- `tests/test_chat_confidence.py`, `tests/test_inference_opt.py` and `tests/test_metrics.py` still pass.
- Checked by hand against a live Ollama 0.40.7 (qwen2.5:3b): a repeated prompt reported 23 of 24 prompt tokens
  as cached.
