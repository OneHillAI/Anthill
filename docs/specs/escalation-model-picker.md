# Spec: escalation model picker

Status: implemented (backend + minimal UI); UI polish and the OpenRouter/Runware add are follow-ups
Lane: `pillar:model`
Relates to: `#854` (escalation returned a reasoning model's chain-of-thought), `docs/specs/escalation-models-non-reasoning.md`, `#770` (the escalation attachment).

## 1. Problem

The escalation attachment had no model picker: one curated model per provider, hardcoded in
`_INFERENCE_PROVIDERS`. When the curated model was bad (slow, dropped, or a reasoning model that dumps
chain-of-thought, `#854`), the only fix was a code change and a release. Users could not choose, and
could not reach a stronger or faster model their provider offered.

## 2. Design

- `OrgSettings.escalation_model` (`VARCHAR(120)`, default `""` = the provider's curated default).
  Added via `_ensure_columns` (SQLite `ADD COLUMN`), so existing installs migrate on next boot.
- New endpoint `POST /settings/escalation/discover-models`: lists the attached provider's live
  `/v1/models` (reusing `anthill.hosting.endpoint.list_models`), annotated by
  `_annotate_escalation_models`: drops non-chat ids (embeddings, rerankers, audio, guards), marks the
  provider's curated default `recommended`, and flags known reasoning/slow families `caution`. Ordered
  recommended, then plain, then cautioned. The account's API key (freshly typed, else the saved one)
  authenticates the call; the key is never echoed back.
- `_build_attachment_backend` uses `cfg.escalation_model` when set, else `prov["escalation_model"]`.
- `_apply_escalation_attachment` stores `escalation_model` (threaded from the attach form through
  `_apply_solo_compute`); it is reset on a provider switch or clear, and a blank value on a mode-only
  re-save keeps the existing choice.
- UI (`_inference_provider.html`): a `<select name="escalation_model">` plus a "Load models" button
  after the key input; small self-contained JS calls the discovery endpoint and populates the list
  with recommended/caution labels. This UI is intentionally minimal - the Council folds it into the
  design system.

## 3. Guard: caution, not hard hide

`escalation-models-non-reasoning.md` says escalation models should be non-reasoning instruct models.
The guard here is a surfaced CAUTION rather than a hard hide, because reasoning behaviour is
PROVIDER-dependent and cannot be reliably detected from `/v1/models` metadata: the same model id is
clean on one provider and dumps chain-of-thought on another. Benchmark (2026-09-30, same NDA prompt):

| provider / model | latency | behaviour |
|---|---|---|
| Groq gpt-oss-120b | 1.7s | clean (reasoning in a separate channel) |
| Groq qwen3.8-27b | 1.5s | clean |
| Infercom Llama-3.3-70B-Instruct | 2.5s | clean |
| Infercom DeepSeek-V3.1 / V3.2 | 157s / timeout | unusable (too slow) |
| Berget gemma-4-31B-it | 9.0s | clean |
| Berget Kimi-K3 (2800B) / GLM-5.3-Flash (320B) | 6.1s / 4.8s | dumps chain-of-thought |

So no provider offers a *usable frontier* open model today; the practical best is a fast instruct
model (Groq gpt-oss-120b, Infercom Llama-3.3-70B). The picker surfaces the whole catalogue with these
cautions so the user chooses with eyes open, and the `#821` visible-failure path covers a model that
later fails at call time.

## 4. Verification

Live-tested on a throwaway server with the real provider keys: the migration adds the column;
discovery works for Groq/Berget/Infercom; annotation flags the right families (Berget -> Kimi/GLM
cautioned, Infercom -> DeepSeek/MiniMax cautioned, Groq -> none, gpt-oss recommended); model
resolution uses the picked model when set and the curated default otherwise; the Settings page renders
the picker; the escalation test suite passes.

## 5. Follow-ups

- Fold the picker UI into the design system (Council).
- Add **OpenRouter** and **Runware** as escalation providers, each with a short remark that they proxy
  CLOSED/frontier models (GPT, Claude, Gemini), unlike the open-weight-only Berget/Groq/Infercom -
  this is where a genuine frontier escalation model would come from (founder direction, 2026-09-30).
- This branch is cut off `main` before the Groq-preferred re-curation; merge that in on landing.
