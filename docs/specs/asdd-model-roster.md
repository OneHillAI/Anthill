# Spec: ASDD model roster (BYO developer)

Status: proposed (roster expanded to the six-role schema, 2026-07-22)
Lane: `pillar:platform`
Implements: OneHillAI/ASDD PR #21 handoff, Decision 2 (`docs/handoffs/2026-07-13-byo-developer-and-goose.md`)
Relates to: `.asdd.yml`, [`.github/asdd/`](../../.github/asdd), OneHillAI/ASDD `.asdd.example.yml` `models:` schema

> Amendment (2026-07-22): the roster was expanded from three roles (developer, tester, reviewer) to the
> six-role schema the ASDD v0.3.0 kit uses. `tester` splits into `test_author` and `test_runner`, and
> `documentation` and `interaction` are named. R1 and R3 below describe the current schema; the split is
> a naming and coverage change, the heterogeneity invariant is unchanged.

> Amendment (2026-07-28): two changes. (a) HARD RULE, no Chinese-origin models in any role (DeepSeek, GLM,
> Kimi, MiniMax, Qwen are out), regardless of hosting, code-behaviour and poisoned-weights risk. The
> MiniMax assignments are swapped to non-Chinese open weights on Berget, mirroring the asdd-run fleet:
> reviewer openai/gpt-oss-120b, test_author and test_runner meta-llama/Llama-3.3-70B-Instruct,
> documentation mistralai/Mistral-Medium-3.5-128B, interaction google/gemma-4-31B-it. The provider moves
> from Infercom (whose catalog is Chinese-model-heavy) to Berget (Sweden). (b) The optional developer council is added (R4): the developer role may be a frontier
> multi-model council on runware, with its transcripts captured privately as a distillation corpus.

## 1. Introduction

The ASDD handoff (ASDD PR #21) fixed the operator model: in ASDD the **developer agent is bring-your-own**
- a contributor connects their own coding agent to build a change; the deployment does not run a standing
developer. The deployment provisions only the **governance and support** agents (reviewer, tester,
documentation, intake), all on open models, so `developer != tester` is satisfied by the project's tester
differing from the BYO developer.

This spec records that roster in Anthill's `.asdd.yml`, the Anthill-implementation workstream's first
step. It is config of record; it does not repoint the live review gate (that is provider-gated - see
Out of scope).

## 2. Requirements

### R1 - Record the roster, conforming to the standard schema
- THE SYSTEM SHALL add a `models:` block to `.asdd.yml` with `developer`, `test_author`, `test_runner`,
  `reviewer`, `documentation`, and `interaction`, per the standard's `.asdd.example.yml` schema (read by
  `scripts/check-models.sh`).
- THE roster SHALL be: developer = Opus 4.8 (**BYO**, or the council in R4); test_author = test_runner =
  meta-llama/Llama-3.3-70B-Instruct; reviewer = openai/gpt-oss-120b; documentation =
  mistralai/Mistral-Medium-3.5-128B; interaction = google/gemma-4-31B-it. All provisioned agents are
  non-Chinese open weights (Meta, OpenAI, Mistral, Google), EU-hosted on Berget (the asdd-run provider), so
  the fleet is both sovereign and free of the banned Chinese-origin models. Opus differs from every
  provisioned model, so heterogeneity holds.
- THE roster SHALL NOT use any Chinese-origin model in any role (the hard rule in the 2026-07-28 amendment).

### R2 - Reflect bring-your-own developer
- THE `.asdd.yml` SHALL state that the developer is BYO and not project-provisioned, and that only the
  governance/support models are provisioned.

### R3 - Satisfy `developer != every test model`
- THE roster SHALL make `models.developer` differ from both `models.test_author` and `models.test_runner`
  (Opus != Llama), so `scripts/check-models.sh --strict` passes, and the reviewer SHALL differ from the
  developer for independence. When the developer is the council (R4), EVERY council model SHALL differ from
  the test models (Opus, Gemini, GPT-5.6 all differ from Llama).

### R4 - The optional developer council
- THE `.asdd.yml` MAY declare a `dev_council` block. When present, the developer role is a frontier
  multi-model council (2 to 5 models: Opus and Gemini 3.1 Pro propose, GPT-5.6 synthesises as lead) that
  proposes, cross-critiques, and verifies against the change's acceptance criteria on models distinct from
  the council, always returning one result.
- The council SHALL run on runware (frontier), wired via the per-member `ASDD_MODEL_URL__COUNCIL_<i>` /
  `ASDD_RUNTIME_TOKEN__COUNCIL_<i>` env, kept separate from the shared Berget vars.
- The lead SHALL NOT be Opus while Opus is also the Claude Code interface (an operator arbitrating its own
  council is an echo chamber); GPT-5.6 is the independent arbiter.
- The council's full-fidelity transcript SHALL be captured to the private operate repo as the distillation
  corpus; the content-safe ledger records the audit trail separately (digests, never the drafted code).

## 3. Design

The `models:` block is data of record. The live review runtime is wired separately through the repo's
`ASDD_MODEL_URL` / `ASDD_MODEL` variables and the `ASDD_RUNTIME_TOKEN` secret; the concrete provider model
IDs and endpoint are set once the provider is chosen. This keeps the roster reviewable in-repo while the
credentials stay owner-managed secrets.

## 4. Tasks

- [x] `.asdd.yml`: add the `models:` roster + the BYO note (R1, R2, R3).
- [x] `.asdd.yml`: expand to the six-role schema (test_author / test_runner / documentation / interaction).
- [x] `scripts/check-models.sh`: refresh to the six-role-aware checker (still accepts the legacy `tester`).
- [ ] `docs/SYSTEM_IMPACT_LOG.md` entry.

## 5. Out of scope (provider-gated or another workstream)

- **Repointing the live review gate** to the reviewer model - a repo variable/secret change. Provider =
  **Berget** (EU-sovereign, OpenAI-compatible at `https://api.berget.ai/v1`; the asdd-run provider,
  non-Chinese models). Needs Berget's endpoint + key set as the repo
  `ASDD_MODEL_URL` var + `ASDD_RUNTIME_TOKEN` secret (owner).
- **Consuming the roster in the pipeline** (the review gate reading `models.reviewer` instead of a single
  `ASDD_MODEL`; a model-based tester agent) - a follow-up once the provider is live.
- **"ASDD with Goose"** (Decision 3: Goose recipes + MCP extension + installer) - the framework/Goose
  workstream, not Anthill.
- The **normative** BYO-developer edit to `standards/spec-driven.md` - the framework workstream
  (OneHillAI/ASDD).
