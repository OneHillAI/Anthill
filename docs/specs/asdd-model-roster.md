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

> Amendment (2026-10-02): provider and ids. The provider is Infercom (Munich, EU tier) again, not Berget.
> On 2026-10-02 Berget's public model list did not offer gpt-oss-120b, Llama 3.3 70B or Mistral Medium 3.5
> (nor does Infercom's list offer Mistral Medium 3.5), so the roster could not run there. Infercom's EU tier
> offers gpt-oss-120b and Gemma 4 31B. The roster becomes: reviewer = gemma-4-31B-it; documentation =
> gemma-4-31B-it (the docsync workflow runs on the same single ASDD_MODEL); test_author = test_runner =
> gpt-oss-120b (Infercom's Llama 3.3 70B is in its non-EU tier); interaction = gemma-4-31B-it. Ids are the
> provider's raw ids, with no vendor prefix. The 2026-07-28 reason to leave Infercom, a catalogue heavy in
> Chinese-origin models, is met by the model rule, not the provider rule: no Chinese-origin model is used in
> any role. The founder chose Infercom on 2026-10-02 and, after a 35-case bake-off of the review pipeline
> (real merged PRs, injected defects, harmless controls), Gemma 4 31B as the review and documentation model.
> All candidates caught the injected defects. gpt-oss-120b recommended request-changes on 17 of 18 merged
> PRs and on every harmless control, Gemma 4 31B on 3 of 18 and none, at about EUR 0.004 per review. None of
> the candidates named the one real UI defect in the sample, which CI's browser job caught. The review gate
> and docsync call the single model named by ASDD_MODEL; reading the per-role ids in those workflows is
> still follow-up.

> Amendment (2026-10-03): the council's result must say what actually happened (R4). On two real runs the
> lead, a reasoning model, spent its whole token budget on hidden reasoning and returned nothing; the script
> handed back proposal 1 as the "synthesis" under a "verify passed" header although no test runner was wired,
> and wrote it to the knowledge lens as a verified exemplar.

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
  gpt-oss-120b; reviewer = gemma-4-31B-it; documentation = gemma-4-31B-it; interaction = gemma-4-31B-it.
  All provisioned agents are non-Chinese open weights (Google, OpenAI), EU-hosted on Infercom (Munich, EU
  tier), so the fleet is both sovereign and free of the banned Chinese-origin models. Opus differs from
  every provisioned model, so heterogeneity holds.
- THE roster SHALL NOT use any Chinese-origin model in any role (the hard rule in the 2026-07-28 amendment).

### R2 - Reflect bring-your-own developer
- THE `.asdd.yml` SHALL state that the developer is BYO and not project-provisioned, and that only the
  governance/support models are provisioned.

### R3 - Satisfy `developer != every test model`
- THE roster SHALL make `models.developer` differ from both `models.test_author` and `models.test_runner`
  (Opus != gpt-oss-120b), so `scripts/check-models.sh --strict` passes, and the reviewer SHALL differ from
  the developer for independence. When the developer is the council (R4), EVERY council model SHALL differ
  from the test models and the reviewer (Opus, Gemini, GPT-5.6 differ from gpt-oss-120b and Gemma by name).
  Known limits: GPT-5.6 and gpt-oss-120b are both OpenAI models (test roles), and Gemini 3.1 Pro and
  Gemma 4 31B are both Google models (reviewer). Lineage independence is not met for those pairs. It is
  accepted until the roster's provider offers an EU-hosted open model outside the council's three vendors.

### R4 - The optional developer council
- THE `.asdd.yml` MAY declare a `dev_council` block. When present, the developer role is a frontier
  multi-model council (2 to 5 models: Opus and Gemini 3.1 Pro propose, GPT-5.6 synthesises as lead) that
  proposes, cross-critiques, and verifies against the change's acceptance criteria on models distinct from
  the council, always returning one result.
- The council SHALL run on runware (frontier), wired via the per-member `ASDD_MODEL_URL__COUNCIL_<i>` /
  `ASDD_RUNTIME_TOKEN__COUNCIL_<i>` env, kept separate from the shared Infercom vars. The scripts read
  only these per-member names (the local `~/anthill-keys/runware.env` uses them). In CI, the runtime check
  fills each member's names from that member's own repo setting if set, else from the single council pair
  `ASDD_MODEL_URL__COUNCIL` / `ASDD_RUNTIME_TOKEN__COUNCIL`, so one provider and one key are entered once,
  not once per member. Only the workflow's env does this fallback; the lookup in the scripts is unchanged.
- The lead SHALL NOT be Opus while Opus is also the Claude Code interface (an operator arbitrating its own
  council is an echo chamber); GPT-5.6 is the independent arbiter.
- The council's full-fidelity transcript SHALL be captured to the private operate repo as the distillation
  corpus; the content-safe ledger records the audit trail separately (digests, never the drafted code).
- The council's result SHALL say what happened. When the lead returns no synthesis, the result header, the
  transcript (`lead_failed`, the fallback proposal's model) and the audit record (verdict `error`,
  `lead_failed`) SHALL say so and name the proposal used instead. When nothing verified the result, the
  header SHALL say NOT VERIFIED and the audit verdict SHALL be `unverified`, never `pass`. A proposal or
  synthesis stopped at the token cap (finish_reason `length`) SHALL be flagged `truncated` in the transcript
  and named in the header. A fallback proposal, an unverified result or a cut-off synthesis SHALL NOT be
  written to the knowledge lens as a council-synthesis exemplar.
- THE `dev_council` block MAY set `reasoning_effort`, sent on every council call. A reasoning model can spend
  the whole `max_tokens` on hidden reasoning and answer nothing: measured 2026-10-03 on Runware, GPT-5.6 on
  the beta-release-lane change used 8000 of 8000 tokens on reasoning and returned no content, while with
  `reasoning_effort: low` it used 766 and returned the full draft. A call that ends that way is not retried
  at the same budget.

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
  **Infercom** (EU-sovereign, Munich, OpenAI-compatible at `https://api.infercom.ai/v1`; EU tier only).
  Needs Infercom's endpoint, `ASDD_MODEL` = `gemma-4-31B-it`, and the key set as the repo `ASDD_MODEL_URL` and
  `ASDD_MODEL` variables and the `ASDD_RUNTIME_TOKEN` secret (owner).
- **Consuming the roster in the pipeline** (the review gate reading `models.reviewer` instead of a single
  `ASDD_MODEL`; a model-based tester agent) - a follow-up once the provider is live.
- **"ASDD with Goose"** (Decision 3: Goose recipes + MCP extension + installer) - the framework/Goose
  workstream, not Anthill.
- The **normative** BYO-developer edit to `standards/spec-driven.md` - the framework workstream
  (OneHillAI/ASDD).
