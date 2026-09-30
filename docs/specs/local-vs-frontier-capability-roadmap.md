# Roadmap: closing the local-vs-frontier capability gap

Status: proposed (this is a synthesis/handover, not a single implementable unit - see "How to use this
doc" below)
Lane: `pillar:model`
Relates to: [`multi-gpu-tensor-parallel-serving.md`](multi-gpu-tensor-parallel-serving.md) (PR #636, open,
no code yet), [`llm-endpoint-secure-transport.md`](llm-endpoint-secure-transport.md) (PR #627, spec
merged, channel itself NOT built - see status below), [`chat-depth-autoroute.md`](chat-depth-autoroute.md),
[`model-benchmark-picker.md`](model-benchmark-picker.md), [`eval-gate-min-heldout.md`](eval-gate-min-heldout.md).

## The problem

Anthill's own claim is that local-first self-hosting is categorically the best tier - full privacy, full
LoRA fine-tuning, full wiki hosting - and every other tier trades some of that away for more capability.
The concrete complaint that produced this doc: **large open-weight models (200B-1T params) cannot be
served on the hardware a typical org already owns**, and the two mitigations built so far (rent a cloud
GPU in the org's own account, or use serverless scale-to-zero) each have a real cost - rented-cloud is not
yet privacy/capability-equivalent to local, and serverless has a cold-start UX/cost problem on anything
but small models.

This doc is a handover for review: five independent perspectives were asked to each argue a distinct way
to close or soften this gap, grounded in the actual codebase (not hypothetical architecture). Every
repo-specific claim below was independently verified against `origin/main` before being written down here
(file:line citations are real, checked on 2026-07-18). One finding (Tier 1, MoE-aware local serving) rests
on external community reports that were NOT independently reproduced - it is flagged as such throughout
and should not be treated as fact until validated hands-on.

## How to use this doc

This is a **roadmap/synthesis**, not one spec to implement wholesale. It:
1. States the current, verified status of the two specs already in flight (#627, #636) - both are
   earlier-stage than their PR numbers might suggest; read this before assuming either is "done."
2. Lays out five new angles, tiered by value/confidence/effort, each with concrete file/module pointers.
3. Flags exactly what remains unverified so the reviewing session doesn't build on an untested premise.
4. Ends with open decisions that are the founder's / reviewing session's call, not pre-decided here.

Each tier below is sized to become its own follow-up spec once prioritized - this doc is the map, not the
territory.

## Status check: the two specs already in flight

**PR #636 - multi-GPU tensor-parallel serving.** Open, **not merged, no code on `main` yet**
(`ProvisionSpec` has no `gpu_count` field; `ProvisionSpec.gpu_count` and `--tensor-parallel-size` do not
exist on `main` as of this doc). This is the spec that lets a rented 8-GPU node (Lambda 8xH100, Verda
8xH200) serve the 200B-1T giants at all. Nothing below changes its scope; it stays the prerequisite for
"rent a bigger cloud node" to reach the frontier tier.

**PR #627 - secure transport for a bare-VM endpoint.** The **spec is merged** into `docs/specs/`, but the
actual fix it specs - an SSH tunnel or pinned-TLS channel so a rented Lambda/Verda endpoint isn't served
in cleartext - is **not built**. What IS live on `main` (`anthill/hosting/lambda_provision.py`) is only the
narrower refuse-by-default posture from the follow-up PR #630: provisioning refuses a plaintext endpoint
by default and requires the conscious opt-in `ANTHILL_ALLOW_INSECURE_LLM_ENDPOINT` to proceed insecurely.
So today, a rented endpoint is either refused or cleartext-with-a-warning - never actually encrypted. This
is directly relevant below (Tier 4): "your own rented account is basically local" is not yet true on the
privacy axis either, pending this spec's real implementation.

## New findings, tiered by value and confidence

### Tier 0 - ship now, independent of everything else, high confidence

- **A real correctness bug**: `OpenAICompatBackend.__init__` (`anthill/inference/openai_compat.py:16`)
  defaults `timeout=120.0`. A cold start on a large rented model can exceed two minutes, so a cold Org-plane
  request may **hard-fail** with a timeout rather than eventually succeed - independent of any UX/pre-warming
  work below, this is a plain bug. Verified: the constructor signature and the timeout value are exactly as
  stated.
- **Solo settings contradicts a decision the Org plane already made.** `tests/test_hybrid_retired.py`
  confirms the org plane already retired the paid third-party inference-vendor fallback from the UI
  ("no Settings card, no Metrics tile... simply unreachable"), and `anthill/hosting/tiers.py:8-9` already
  encodes the right vocabulary (`sovereignty: "green"` for on-prem/VPC, `"amber"` for neocloud - "your own
  account and your own model, but inference transits a third party's shared GPUs"). But
  `docs/specs/intelligence-settings.md` / `personalize.html`'s Solo picker still lists "Inference provider
  (per-token)" as a peer card next to Local/VPC - the exact framing the org plane already rejected. Fix:
  port the green/amber vocabulary into Solo and kill or clearly re-scope that roadmap card.

### Tier 1 - the highest-leverage finding, but UNVERIFIED, spike before promising anything

Several of the biggest open-weight models are **Mixture-of-Experts**: DeepSeek (~671B total, ~37B active),
Kimi K2 (~1T total, ~32B active), GLM. Only the active experts do compute per token - the rest of the
weights need to be memory-resident, not fast-compute-adjacent. Community reports (NOT independently
reproduced by this review) put DeepSeek R1 at roughly 17-20 tok/s on a single 512GB Mac Studio via mlx-lm,
which reportedly already supports these architectures - genuinely usable chat speed, if true. Kimi K2
reportedly needs two Mac Studios via mlx-lm pipeline-parallelism.

Anthill's own `anthill/hosting/sizing.py` does not distinguish active-vs-total params anywhere in its fit
math (`cloud_gpu_max_params`, `cloud_gpu_max_params_quantized`, `resident_gb` all key off one `params_b`) -
so today's capability gate may be **under-representing** what a Mac Studio can actually serve. If this
holds up, "large models can't run locally" is partly a sizing gap, not a hardware wall, for the MoE subset
of frontier models (which includes several of the biggest ones).

**Action, not a claim**: run a hands-on spike - actually load and serve a MoE model locally via mlx-lm on
real hardware, measure real tokens/sec, before this appears in any user-facing copy or before
`sizing.py`/the catalog's local-fit gate is changed to reflect it. This is the single most valuable thing
to verify next, because a positive result reframes the whole problem.

### Tier 2 - reduce how often the expensive/cold tier is even needed

Anthill already has most of the pieces for a confidence-based router that keeps most traffic on the local,
continuously-tuned model and escalates only genuinely novel/complex queries:

- `escalate_org` (the chat-stream parameter) already exists as, per `docs/specs/chat-depth-autoroute.md:37-38,51`,
  "a latent API capability... a future multi-model world could use," with "fully automatic model-size
  routing" explicitly named and explicitly deferred - not built, but designed-for.
- `anthill/agent/intent.py:404`'s `looks_deep()` is an existing, conservative multi-hop-complexity
  detector, reusable as one routing signal.
- `anthill/web/db.py:677`'s `TrainingExample.task_type` column already exists and is currently unused for
  routing/specialization - it enables per-task-type LoRA adapters (several small specialists instead of
  one generalist local model) as a follow-on.

Recommended new module: `anthill/routing/confidence.py` - a domain-fit signal (cosine similarity of the
incoming query to the org's gold instructions, reusing the embedder `evaluate_models` already uses) plus
`looks_deep()`, wired into the existing but-unused `escalate_org` parameter and
`anthill/web/plane_routing.py`'s `plane_inference`. This is mostly composition of parts that already exist.

Caveat, stated plainly: this reduces *frequency* of needing the giant tier for the median query; it does
not raise the local model's actual reasoning ceiling, and will under-flag a novel-sounding-but-actually-hard
query as "seen this shape before." Not a substitute for the giant tier on genuinely novel work.

### Tier 3 - soften the pain when the giant/cold tier IS needed

**Status: done (narrowed from this section's original framing after two rounds of re-verification - see
the PR this shipped in).** A STATIC admin-set warm-worker count for RunPod is real now (`workersMin` in
`runpod_provision.py` is no longer hardcoded to 0); TIME-OF-DAY scheduling (warm during usage hours, scale
to zero overnight) remains a separate, larger follow-on, not built here. The "local-fallback UX" half was
re-scoped twice: a TRUE team/org-plane chat genuinely has no fallback (the org wiki is hosted at the same
place as the org model - verified, not assumed), so its existing "disabled, here's why" behavior is
correct and untouched. A member's PRIVATE chat inside an org (Solo plane, own local wiki, ephemeral -
`docs/specs/one-model-per-account-solo-in-org.md`) already fell back to the local model automatically,
verified in `plane_routing.py` - the actual gap was that this silent fallback was never shown to the user;
that visibility is what shipped, not a new interactive prompt.

- **Cash an IOU the UI already writes.** `anthill/hosting/provision.py:255` (`_serverless_steps`) already
  shows the admin the promised step "Configure scale-to-zero with an optional warm pool to bound cold
  starts" - verified this text exists on `main` today. No such warm-pool mechanism exists anywhere in
  `ProvisionSpec`, `RunpodLiveProvisioner`, or `web/provision_run.py`. Building scheduled min-replicas
  (keep 1 warm during an org's actual usage hours, scale to zero overnight) both cuts cold starts AND cuts
  cost, since Verda's confirmed billing (per active minute, including spin-up/spin-down -
  `docs/specs/verda-serverless-containers-provisioner.md`) charges for every unnecessary wake.
- **A local-fallback draft-answer UX already exists, wired to the wrong scenario.** `chat.html` (around
  lines 465-502) already implements a prompted (never silent) choice - "use your local model now, or wait
  for it to reconnect" - but it's wired to the always-on VPC-tier reconnect case, which has no cold start.
  The actual cold-start pain lives on the Org-plane neocloud path, which today just blocks sending with a
  static banner. Extending the existing pattern to that scenario is smaller than building new UX.

### Tier 4 - the two specs already in flight, reinforced as prerequisites

Nothing new here - the review's independent conclusion is simply that #627's real secure channel and #636's
multi-GPU serving remain the actual prerequisites for "rent your own account" to become privacy- and
capability-equivalent to local, respectively. See "Status check" above for exactly what's built vs. not.

### Tier 5 - narrow, real, defer

> Status: done. See PR #661 Tier 5. Shipped as configuration + capacity estimation + TCP reachability
> display only - Anthill never launches, SSHes into, or manages any `rpc-server`/`llama-server` process;
> the admin sets those up themselves. Two corrections to this section's original framing, verified
> against real code before building: (1) `anthill/mesh_auth.py` is NOT mTLS - it is a shared-secret
> bearer token (`require_mesh()`/`mesh_headers()`, an `Authorization: Bearer <ANTHILL_MESH_TOKEN>`
> header); there are no certificates anywhere in this codebase. (2) Even corrected to "bearer token,"
> nothing from the mesh is reusable here - `require_mesh()`/`mesh_headers()` protect HTTP endpoints, and
> llama.cpp's `rpc-server` protocol is not HTTP, so there is no HTTP surface on the worker side for a
> bearer token to gate. Separately, no new `anthill/inference/cluster.py` backend was needed either:
> llama-server started with `--rpc host:port,...` still exposes the identical OpenAI-compatible `/v1`
> API to a client, so the existing "connect a server you already run" path (`org_model_endpoint` ->
> `OpenAICompatBackend`) already handles the pool's actual chat traffic unmodified once pointed at the
> main node. See `docs/specs/661-tier5-distributed-pooling.md` for the full spec.

Pooling an org's own multiple machines (2-4 Mac Studios/workstations on a LAN, via llama.cpp's `rpc-server`
or `exo`) to push the local ceiling from ~70B to roughly ~150B is real, shipping technology, but the
network-hop penalty between machines means it should be pitched as a moderate ceiling lift for orgs that
already own several boxes, not a path to 1T-class models on a small team's hardware. It composes with Tier
1 for Kimi-K2-class models (Tier 1's own estimate needs two Mac Studios regardless). Anthill's existing
`anthill/orchestrator/`/`anthill/node_agent/` mesh (`anthill/mesh_auth.py`, `NodeRegistry.route()`) is a
task-distribution mesh (one node = one whole model, verified: `route()` matches on exact model-string
equality) - architecturally the wrong shape for splitting one model's weights across machines, and (per
the status note above) not actually mTLS as originally described here, nor reusable regardless. Training
is a non-issue in this design - it stays single-node exactly as it is today.

## Recommended build order

1. Tier 0 (the timeout bug + the Solo-settings inconsistency) - ship immediately, independent of the rest.
2. Tier 1's spike (validate MoE-aware local serving on real hardware) - before anything else, because a
   positive result changes the priority of everything below it.
3. Tier 2 (confidence router) - composes with any Tier 1 outcome, reduces reliance on the expensive tier
   regardless of what Tier 1 finds.
4. Tier 3 (scheduled pre-warming + extend the existing local-fallback UX to the Org-plane cold-start case).
5. Tier 4 - finish #627's real channel and #636's multi-GPU serving (already specced, just needs building).
6. Tier 5 (distributed local pooling) - narrow, defer.

## Open decisions (not pre-decided here)

- Whether to greenlight the Tier 1 hands-on spike, and on what hardware.
- Which tiers to actually schedule next, and in what order relative to #627/#636's own implementation work.
- Whether Tier 2's confidence router and Tier 5's distributed pooling warrant their own dedicated specs
  now, or should wait until Tier 1's result is known.
