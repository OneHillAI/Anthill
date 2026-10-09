# Spec: The model window is set explicitly and the reference is shortened to fit it

Status: implemented. Lane: `pillar:knowledge`. Part 1 of issue #109.

## Problem

Anthill never told Ollama how large a window to use, so Ollama chose its own (4,096 tokens on the measured
machine). When the wiki pages found for a question did not fit, Ollama dropped the oldest whole message, which
after the system message is the reference block, and the model answered without the wiki, fast and wrong.
Meanwhile `OllamaBackend.context_window` reported the model's trained maximum (262,144 for qwen3.5:9b), so the
budgets in `anthill/inference/context.py` assumed a window about 64 times larger than the one in use.

## Requirements

- Every request to Ollama (chat, streaming, forced-JSON, with confidence, and tool calls) carries `num_ctx`.
  `ANTHILL_NUM_CTX` sets it, else Ollama's own `OLLAMA_CONTEXT_LENGTH` (a whole number of at least 4,096 in
  either case; anything else is ignored). With neither set, the default is 8,192, or on a Mac with enough
  memory what Ollama itself would choose (32k from 32 GiB, 256k from 64 GiB; Ollama documents 4k, 32k and 256k
  for under 24, 24 to 48 and 48 GiB or more of video memory, and a Mac's video memory is a share of its
  memory), so the window is never knowingly lower than Ollama's own. A constructor argument wins over the
  environment. The value is capped at the model's trained maximum once that is known (it comes from a cached
  `/api/show` read, so building a request makes no network call).
- `OllamaBackend.context_window()` reports the window in use: the smaller of the trained maximum and the
  requested window, or the requested window when the trained maximum cannot be read.
  `trained_context_window()` keeps the old meaning (the trained maximum, 0 when unknown).
- A fit guard (`anthill/inference/fit.py`) shortens a wiki prompt that does not fit, in this order. It applies
  wherever a prompt is built from wiki pages: `ask()`, its hardened retry, `ask_stream()` and the web answer
  path (`search_and_answer`).
  1. The system message and the question are never changed.
  2. The reference is cut from its end, down to a floor of about 1,500 tokens. Pages are put in the reference
     in the order retrieval ranked them (best first within each wiki), so the last, least relevant page goes
     first, and what comes first (the principles, the profile and the best page) survives.
  3. Older conversation turns are dropped, oldest first.
  4. Only if it still does not fit, the reference goes below its floor.
- The estimate is 3 characters per token for Latin-script text, with more tokens per character for other scripts
  (about a token a character for CJK, Thai, Lao, Myanmar and Khmer, and 1.5 characters per token for Greek,
  Cyrillic, Arabic, Hebrew and Indic text). About 2,048 tokens of the window are kept free for the answer, which
  is less than the longest answer allowed (4,096 tokens), so a very long answer can still push the start of the
  prompt out of the window while it is written.
- A leading conversation summary (made by a model call) is kept until every other older turn has gone.
- A prompt that already fits is unchanged byte for byte. The text that wraps the reference is the same as before.
- A backend that does not report its window (a cloud or OpenAI-compatible endpoint) is not touched: the guard
  does not guess a window for it.

## Known limits and behaviour to know about

- The cap at the trained maximum comes from a cached read, so a model trained for less than the requested window
  is sent the requested window the first time and its own size afterwards; on a path that does not read the
  trained maximum first that is one extra model reload.
- Another program that talks to the same Ollama with a different `num_ctx` makes the model reload each time the
  window changes between them.
- The window requested is the same on every call, but a different value in `ANTHILL_NUM_CTX` after a restart
  loads the model again.
- On a machine where Ollama's own default is larger than ours (a Windows or Linux machine with 24 GiB or more of
  video memory, where Anthill cannot read the video memory), the default here is 8,192, lower than Ollama's.
  `ANTHILL_NUM_CTX` or `OLLAMA_CONTEXT_LENGTH` raises it. Not measured: the packaged app's pinned Ollama
  (0.30.10); the documented defaults above are those of Ollama's documentation.
- The budgets for history and agent compaction in `inference/context.py` now follow this window: at 8,192 they
  are at their floors, so a long chat summarises its older turns at about 24,000 characters. See the update in
  `docs/specs/per-model-context-budget.md`.
- A page whose text the guard cut out entirely is still reported as a source for the answer.
- The cache warm-up (`lifecycle/ask_shim.py`) is fitted the same way.

## Measured memory

`ollama ps` size of the loaded model on an Apple M4 with 16 GB, Ollama 0.24.0, after one short request at each
window:

| Model | 4,096 | 8,192 | 16,384 |
|---|---|---|---|
| qwen3.5:9b | 8.60 GB | 8.73 GB | 9.07 GB |
| mistral:7b | 5.14 GB | 5.96 GB | 7.58 GB |
| gemma3:4b | 4.31 GB | 4.40 GB | 4.56 GB |

Going from the previous default (4,096) to 8,192 costs 0.1 to 0.8 GB. These are the only versions measured: the
desktop app pins Ollama 0.30.10, which was not measured, and other Macs and other models may differ.

## Acceptance criteria

- A request to Ollama carries `num_ctx`, the same value for chat, streaming, forced-JSON and tool calls.
- With a reference longer than the window, the prompt that reaches the model still contains the question and as
  much of the reference and the history as fits. A fact at the start of an over-long reference reaches the model
  on the local, streaming and web paths, including the web path of a real chat, where the backend is wrapped for
  the model override or Thinking.
- Short prompts are unchanged byte for byte.
- The window and its memory cost are measured and stated (the table above).

## Not in this change

Shortening a page to its best passages, which also makes the first word sooner, is Part 2 of #109. The guard
here only makes sure that what is sent fits.

## Proof

- `tests/test_ollama_num_ctx.py`: `num_ctx` on every payload, the environment overrides and their fallbacks,
  Ollama's own variable, the memory tiers on a Mac, the cap at the trained maximum, and no network call when
  building a request.
- `tests/test_context_budget.py`: the trained window is read and cached, the window in use is the smaller of the
  two, and the requested window is the fallback.
- `tests/test_prompt_fit.py`: the fit rules and their order; a prompt that fits is unchanged and the wrapper text
  is unchanged; the start of an over-long reference reaches the model through `ask()`, `ask_stream()`,
  `search_and_answer` and `ask(web_search=True)` through the override wrapper; a leading summary is dropped last;
  a window at or below the answer reserve; other scripts; a backend with no known window is left alone.
