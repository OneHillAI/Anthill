# Spec: escalation models must never leak reasoning into the visible answer

Status: implemented
Lane: `pillar:model`
Relates to: `#854` (Escalation to Berget returns raw chain-of-thought), `#820` (stale escalation model 404), `#821` (escalation attachment: stale model, silent failures, uncapped output).

## 1. Problem

The optional escalation attachment ("Check with {Provider}") answers one turn on a curated hosted
model and shows that model's output to the user verbatim. The curated `escalation_model` for Berget
was `Qwen/Qwen3.8-27B-FP8`, a reasoning model. Berget returns the model's chain-of-thought AS
`message.content` (there is no separate reasoning field and no `<think>` tags to strip), so the
"expert" answer was the model thinking out loud rather than an answer, and with the escalation token
cap it hit `finish=length` mid-thought, i.e. no conclusion at all, after 30-40 seconds. The founder
hit this live on v0.12.0.

Repro (question "does winter start on Nov 21 this year?", `max_tokens=512`, `temperature=0`, live
Berget `/v1/chat/completions`, 2026-09-29):

| model | time | result |
|---|---|---|
| `Qwen/Qwen3.8-27B-FP8` (reasoning) | 41.6s | `finish=length`, 512 tokens of raw chain-of-thought, no answer |
| `Qwen3.8-27B` + `chat_template_kwargs {enable_thinking: false}` | 18.2s | clean answer, still slow |
| `google/gemma-4-31B-it` (instruct) | 2.3s | clean, correct answer |
| `mistralai/Mistral-Small-3.2-24B-Instruct-2506` (instruct) | 5.4s | clean answer |

## 2. Constraint

**Corrected 2026-09-29** (live-testing Groq surfaced that the original framing below was too broad -
see section 3): the actual requirement is that a curated `escalation_model`'s `message.content` must
never contain reasoning/chain-of-thought - being architecturally a "reasoning model" is not itself
disqualifying if the provider's API keeps reasoning in a separate field. `anthill/inference/
openai_compat.py` already reads only `message.get("content")` and never a `reasoning`/
`reasoning_content` field, so any provider that separates the two is already safe with today's code -
no per-provider special-casing needed.

Originally (pre-live-testing) this spec required every `escalation_model` in `_INFERENCE_PROVIDERS`
to be a non-reasoning instruct model outright, because Berget's Qwen3.8-27B-FP8 returns its
chain-of-thought AS `message.content` with no separate field and no tags to strip - for a provider
that conflates the two like that, avoiding reasoning models entirely really is the only fix, since
disabling reasoning per request is provider/model-specific
(`chat_template_kwargs.enable_thinking` for Qwen, `reasoning_effort` for gpt-oss) and cannot be
applied uniformly across providers. That fallback (pick an instruct model) still applies to any
provider that, like Berget, conflates reasoning into `content`.

## 3. Decision

- **Berget**: `escalation_model` -> `google/gemma-4-31B-it` (instruct; live-verified, 2.3s, clean
  answer). Berget conflates reasoning into `content` (no separate field), so per section 2 this
  provider needs a genuinely non-reasoning model.
- **Infercom**: `escalation_model` -> `gemma-4-31B-it`. Live-verified 2026-09-29 with a real Infercom
  key on the founder's own repro question ("does winter start on Nov 21 this year?", `max_tokens=512`,
  `temperature=0`): `finish_reason: "stop"`, `usage.completion_tokens_details.reasoning_tokens == 0`
  (the API itself confirms zero reasoning spent), 1.27s total, complete and correct answer. Also
  matches Infercom's own model table (`docs.infercom.ai/en/models/infercomcloud-models`), which labels
  `MiniMax-M2.7`/`gpt-oss-120b` "Text, Reasoning" and `gemma-4-31B-it` "Text, Vision".
- **Groq**: `escalation_model` left **unchanged** at `openai/gpt-oss-120b` - correctly so. Live-tested
  2026-09-29 with a real Groq key on the same repro question: the response carried `message.content`
  (a complete, clean, well-formatted, correct answer - astronomical vs meteorological winter, a dated
  table for 2026) and a *separate* `message.reasoning` field (213 tokens of chain-of-thought), never
  conflated. `finish_reason` was `"length"` (reasoning + content together used the full 512-token
  budget), but the visible content was already complete when generation stopped - only a trailing
  citation clause was clipped, not the substantive answer. Since this codebase already discards
  `reasoning` and reads only `content` (section 2), Groq's gpt-oss family was never actually broken -
  the original concern was architectural-analogy speculation ("also a reasoning model, like Qwen3.8"),
  disproven by the live test. Self-service Llama 3.1 8B / Llama 3.3 70B were checked via this same
  key's `/v1/models` and confirmed genuinely unavailable ("Enterprise" tier was accurate) - they were
  never a real option regardless.

## 4. Verification

All three providers are now live-verified against real account keys and the founder's own repro
question (max_tokens=512, temperature=0):

| provider | model | finish_reason | time | reasoning leaked into content? |
|---|---|---|---|---|
| Berget | `google/gemma-4-31B-it` | stop | 2.3s | no (genuinely non-reasoning) |
| Infercom | `gemma-4-31B-it` | stop | 1.27s | no (`reasoning_tokens: 0`) |
| Groq | `openai/gpt-oss-120b` | length* | 1.4s | no (separate `reasoning` field, discarded by our own parsing) |

\*Groq's `length` reflects the combined reasoning+content token budget, not a truncated answer - see
section 3.

## 5. Future work

Groq's reasoning-token overhead (213 of 512 tokens on this test) leaves less effective budget for
`content` than a pure instruct model gets, which could truncate a longer or more complex answer more
than this one test question did. Worth revisiting if that's observed in practice - e.g. a higher
`max_tokens` specifically when a provider is known to return separate reasoning, or a
`reasoning_effort`-style parameter where the provider supports one - but not warranted from a single
successful test alone.

A runtime guard (reject a curated model whose response conflates reasoning into `content`) would make
the corrected section-2 constraint self-enforcing instead of relying on manual verification per
provider, but is out of scope here.
