# Inference-provider path, mandatory org wiki hosting, and council-draft training capture

## Why

Three connected gaps, worked through with the founder across several turns:

1. `docs/specs/model-onboarding-and-sovereignty.md` R7 says the inference-provider path (a vetted,
   EU-sovereign open-weight host like Berget) should exist "now, as an Advanced/secondary option," but
   nothing implements it - the Solo "Inference provider (per-token)" card that once existed was a pure
   UI placeholder, never wired to a backend (confirmed via git history), and the separate `anthill/
   hybrid/` system is an unrelated per-turn paid-escalation fallback, not a selectable primary backend.
2. R7/R8 also claim "there is NO training/knowledge flywheel through a third-party host" and cap this
   path at Partial ownership - **this is now factually wrong**: `anthill/training/collect.py`'s
   `record_example()` already captures every chat turn's Q&A pair regardless of backend. The real gap
   isn't "no flywheel is possible," it's that (a) nothing distinguishes training-eligible captures
   (self-hosted/open-weight) from ones a third-party's own terms may restrict (closed-model API
   outputs), and (b) the council's own reasoning process (each member's draft, any critique) is
   computed and then discarded - only the flattened final answer is ever captured.
3. Wiki hosting: `OrgSettings.wiki_hosting`/`wiki_vpc_url` already exist and already work correctly when
   an admin fills them in, but nothing tells an org it needs to - an org on a backend with no always-on
   host of its own (inference-provider, or a neocloud like RunPod) can silently sit at the default
   `wiki_hosting="local"`, which for a genuinely multi-person org means the wiki lives wherever the
   admin's own device happens to be, not somewhere the whole team can actually reach it. A **solo**
   account is unaffected - their own device already is their always-on host.

Founder's explicit framing, verified against real code and external sources rather than assumed:

- **Model origin is not the security concern; ownership is.** A Chinese-origin open-weight model run on
  your own hardware or your own cloud VPC is fine "as long as it delivers the performance" - you own
  the model and the data either way. Jurisdiction of a rented VPC (US vs EU) is a secondary concern for
  most orgs, not a primary one - "you still own the model, you still own the data, nobody is capturing
  it." This directly revises R6's original "EU-sovereign host = better security posture" framing, which
  was about routing a Chinese-origin model's *inference* through a vetted EU host as a privacy
  trade-off, not about which cloud VPC to rent.
- **Provider choice should lead with ease/speed/terms, not jurisdiction.** Researched head-to-head
  (ClusterMAX 2.0, Nov 2025 industry ranking; current pricing and API docs, checked live not assumed):
  RunPod is the best fit for the *primary* recommended cloud VPC provider - fastest self-serve
  onboarding, a purpose-built vLLM quick-deploy path, scale-to-zero pricing that fits bursty org usage
  (~$1.89/hr Secure Cloud vs. Lambda's ~$2.79-2.89/hr always-on-only for an 80GB-class GPU), and CPU-only
  pods on the same account - which can also host the org's always-on wiki/backend instance, closing gap
  3 above without a second provider. Lambda stays a solid secondary (already integrated, good for a
  plain always-on SSH-key VM). DataCrunch/Verda's serving-bootstrap gap, treated as a hard blocker in
  earlier work this session, is confirmed **stale** - `startup_script_id` is genuinely documented and
  SDK-supported today (checked against `api.datacrunch.io`/`api.verda.com` docs and the current Python
  SDK) - so it stays a live, real option (cheapest of the three), just not the default, given its
  Bronze-tier reliability track record (same tier as RunPod; none of the three is top-tier).
- **Berget specifically checked, not assumed**: real, EU-sovereign (Sweden), GDPR-compliant, serves
  Llama/Mistral/Gemma/gpt-oss/GLM over an OpenAI-compatible API. Their terms state they never store
  prompt or output content at all (stronger than a retention window), and the customer retains all
  title/IP rights in "Customer Data." No clause restricts using served outputs to train another model
  (unlike OpenAI's/Anthropic's terms, which explicitly do) - confirmed via `ownershipindex.ai`'s sourced
  entry for Berget (Grade B, 70.4, "Substantial" ownership), which flags this specific point as
  undocumented-but-unrestricted rather than either explicitly permitted or forbidden.

## What this change adds

1. **A Berget quick-connect option**, reusing the EXISTING "connect a server you already run" mechanism
   (`org_model_endpoint`/`org_model_key_enc` -> `OpenAICompatBackend`) that already works for both solo
   (their own personal single-user org) and true multi-user orgs - no new backend class, no new
   Provisioner, matching the same reasoning that closed out Tier 5. A labeled card pre-fills Berget's
   known endpoint and shows the models it actually serves; the admin still enters their own API key.
2. **A wiki-hosting reminder** for an org (not solo) using the manual-connect path with `wiki_hosting`
   still at its default "local" - a banner, reusing the existing `_compute_banner.html` convention,
   pointing at the Wiki tab. Not a hard block (the underlying save logic is unchanged and already
   correct) - a visibility fix, matching this session's established "make existing behavior visible,
   don't add new gating" pattern from the #661 Tier 3 work.
3. **A `training_eligible` flag on `TrainingExample`**, defaulted `True`, set `False` specifically when
   an answer's contributing model(s) were reached through a third-party closed-model API path (the
   `anthill/hybrid/` escalation providers - anthropic/moonshot/deepseek's *direct* APIs carry their own
   provider terms) as opposed to a self-hosted or open-weight-via-inference-provider path. This does not
   change what gets captured, only whether it is marked usable for later fine-tuning.
4. **Council draft capture**: `CouncilResult` gains `proposals: list[tuple[int, str]]` (the actual draft
   text per member, already computed by `run_council()`, previously discarded) instead of only
   `proposer_indexes`. A new nullable `council_drafts` TEXT column on `TrainingExample` stores this (JSON
   array of `{member_index, text}`) when the answer was synthesized from 2+ members. Critique-text
   capture, tool-call traces, a PII-scan flag, and a dedup hash are real, valuable next steps identified
   by the research (Anthropic's Constitutional AI and OpenAI's PRM800K both treat critique/revision text
   as first-class trainable data, not scratch work) but are **explicitly deferred** past this pass -
   flagged honestly rather than rushed in under time pressure.
5. **RunPod promoted to the primary recommended cloud VPC provider** in the provider picker's ordering/
   annotation; Lambda stays a clearly-marked secondary; DataCrunch/Verda stays available, annotated as
   the cheapest option rather than "not fully wired up" (its bootstrap claim was the stale part, not its
   functionality - correcting an earlier session's now-outdated finding).
6. **`docs/specs/local-vs-frontier-capability-roadmap.md` R6/R7/R8 and `model-onboarding-and-sovereignty.md`
   R6/R7/R8 corrected in place**: drop the "no training flywheel" and "EU-sovereign-host-for-jurisdiction"
   framing; state the real trade-off (partial model ownership, full data/wiki ownership, training-data
   banked but the training_eligible flag governs what's actually usable).

## Explicitly out of scope (this pass)

- Critique-text capture, tool-call-trace capture, automated PII scanning, and dedup hashing for training
  examples - real next steps per the research, not built now.
- A new `Provisioner`/automated provisioning path for Berget - this is a manual-connect quick-fill, not
  automated infrastructure provisioning (Berget has no VM/instance concept to provision; it's a stateless
  API).
- Hard-blocking a save when an org's wiki_hosting is still "local" - a visibility banner only.
- Migrating any existing `anthill/hybrid/` provider entries or reviving the retired hybrid-fallback UI.
- Re-deriving `wiki_hosting`'s save logic - unchanged; only a new banner reads it.

## Acceptance criteria

1. A Berget quick-connect card appears in Settings > Organization's "Connect a model server you already
   run" section (both solo and true-org accounts reach it via the same route); clicking it pre-fills the
   endpoint field; saving still goes through the existing, unmodified `/settings/organization/connect`
   validation flow.
2. An org (not solo) with `wiki_hosting == "local"` sees a banner pointing at the Wiki tab; a solo
   account never sees it; an org that has already set `wiki_vpc_url` never sees it.
3. `TrainingExample` gains `training_eligible` (default True) and `council_drafts` (nullable) columns;
   `record_example()` accepts both as optional keyword args, defaulting to today's behavior when omitted.
4. `CouncilResult` carries `proposals` (member index + draft text); the chat call site that invokes
   `record_example()` after a council-synthesized answer passes them through as `council_drafts`.
5. The provider picker shows RunPod annotated as the recommended default; Lambda as a secondary; DataCrunch/Verda
   no longer marked "not fully wired up" (its bootstrap mechanism is confirmed live) but still marked
   as not the default.
6. `ruff check`, `ruff format --check`, `mypy`, full test suite pass with zero failures.
