# PR #636 / Tier 4: single-node multi-GPU (tensor-parallel) serving

## Why

`docs/specs/multi-gpu-tensor-parallel-serving.md` (this PR brings the spec in from the still-open,
unmerged `#636` branch alongside the implementation - see "About the spec" below): every provisioned
model is served on exactly one GPU. `vllm_startup_script` never passes `--tensor-parallel-size`;
`ProvisionSpec` has no `gpu_count`; the fit gate keys on one GPU's VRAM. The largest servable model is
capped at ~70B (4-bit, 80GB H100). The frontier-scale open models the product wants to offer (200B-1T
params) need 400-600GB+ of weights, only reachable by splitting one model across a multi-GPU node
(Lambda 8xH100, etc.) via vLLM's tensor-parallel mode.

## About the spec (a real, deliberate decision, not an oversight)

PR #636 is still open, unreviewed by a human (only an automated advisory review, recommending
"request-changes"), with no code. Per the user's explicit instruction, this PR builds against that draft
spec text as-is rather than waiting for #636 to merge first. This PR brings `docs/specs/
multi-gpu-tensor-parallel-serving.md` in itself (copied from the `model/multi-gpu-tensor-parallel-serving`
branch, status flipped to done once built) so this PR is self-contained and spec-driven per the intake
gate. **#636 itself will likely need to be closed by a human once this merges** - not something to do
unilaterally here.

## Grounding: what the spec's own design section gets slightly wrong or leaves unresolved

Independently verified against the real code (`origin/main`, post-#670) before writing this, not taken on
the spec's word:

1. **`servable_on_gpu(model, vram_gb)` does not exist anywhere** - confirmed via full-history grep across
   every branch. The spec's "coordinate with the catalog workstream" note describes work that never
   happened; there is no separate branch to consume. The one existing, real function is
   `sizing.servable_on_one_gpu(params_b, vram_gb, *, quantized=False, has_quant=False)` - single caller,
   `app.py`'s `_derive_council_member_selection`. This PR extends and RENAMES it to `servable_on_gpu`
   (adding `gpu_count: int = 1`): its old name asserts "single-GPU only" as an invariant this PR removes,
   and keeping a now-misleading name would be worse than the one-caller rename cost.
2. **`usable_gb()` is a shared low-level primitive** - also used by on-prem/Apple sizing
   (`max_params_b`, `fit_tier`, `recommend`, `recommend_by_family`, `onprem_council_fits`). Adding
   aggregate-VRAM semantics here as a new REQUIRED parameter would silently change behavior for every
   existing single-GPU/on-prem caller. Fixed by adding `gpu_count: int = 1` as an OPTIONAL parameter,
   threaded through `usable_gb` -> `max_params_b` -> `cloud_gpu_max_params`/`cloud_gpu_max_params_quantized`
   -> the renamed `servable_on_gpu` - purely additive, every existing call site's behavior is unchanged
   because it never passes the new argument.
3. **No attention-head-count data exists anywhere** (not in `sizing.Model`, not in `model_catalog.json`'s
   schema, not hardcoded) - a real gap for the spec's "gpu_count MUST evenly divide the model's attention-
   head count" requirement. **Also discovered**: `model_catalog.json` is an explicitly-labeled
   `"generated": "seed"` PLACEHOLDER catalog (its own note says "Refreshable via /models/refresh-catalog")
   - several of its entries (DeepSeek V4-Pro, GLM-5.1/5.2, Kimi K2.7, Qwen3.5/3.6, MiniMax M3) do not
   correspond to any real, currently-published model card as of this session. **Decision**: add the schema
   capability (`Model.num_attention_heads: int = 0`, 0 = unknown), make the divisibility check
   non-blocking when unknown (this PR does NOT invent specific head-count numbers for placeholder/seed
   catalog rows - that would be fabricating verifiable technical facts). vLLM itself refuses to start if
   `--tensor-parallel-size` does not divide head count, so Anthill's own pre-check is a nice-to-have
   fail-fast (avoids a wasted launch-and-poll cycle), not the only safety net - the real backstop is vLLM's
   own runtime refusal. A follow-up catalog-curation pass (populating real head counts once the catalog is
   refreshed with real current models) is out of scope here.
4. **No multi-GPU Lambda SKU table exists**, and none needs to be invented: the spec's own design section
   already names the simpler alternative - "parsed from `instance_type` where the SKU name encodes it,
   e.g. Lambda `gpu_8x_h100`." Lambda's real, documented naming convention is `gpu_{N}x_{gpu-model}`.
   **Decision**: derive `gpu_count` by parsing this pattern out of the admin's already-existing
   `org_lambda_instance_type` free-text field, rather than adding a second, separately-validated
   `gpu_count` input that could disagree with what Lambda will actually launch. This also satisfies
   requirement 4(b) (genuine multi-GPU node with NVLink) by construction: every instance type matching
   `gpu_{N}x_...` in Lambda's real catalog is one of their NVLink/NVSwitch multi-GPU SKUs, not a PCIe-
   spread box - there is no PCIe-only multi-GPU SKU with this name shape to accidentally match.
5. **No numeric per-run cost-cap mechanism exists in this subsystem** (`hosting/lambda_provision.py`,
   `hosting/provision.py`, `hosting/runpod_provision.py`) - only guaranteed teardown. A cost cap exists
   only in the separate training-run subsystem (`training/backends/`, `cloud/aws.py`). The spec's "Cost
   guardrails SHALL scale with GPU count... existing per-run cost cap... SHALL be preserved" is read as:
   the existing guaranteed-teardown guarantee continues to hold for a multi-GPU instance (it does,
   unchanged) - not as a mandate to invent a new billing-guard mechanism that has no precedent in this
   exact code path. Inventing one would be real scope creep beyond what this spec's own EARS requirements
   ask for.
6. **The Settings UI already has a permanent dead-end label** ("needs multi-GPU serving", disabled option)
   for any model above the single-GPU ceiling. Making that dropdown option live-recompute against a
   free-typed instance type is a nontrivial JS change. **Decision: out of scope for this PR.** The
   server-side fit gate (`_derive_council_member_selection`) is already the authoritative check today (its
   own docstring says so - "a direct POST bypasses [the JS graying]; this predicate is the server-side
   check") and the existing "custom model" text-entry escape hatch (`model == "__custom__"`) already lets
   an admin select a model the dropdown greys out; the updated server-side gate correctly accepts it once
   `gpu_count` is considered. A follow-up UX pass to make the dropdown itself live-recompute is a real,
   separate piece of work, not silently done here.

## What already exists and is REUSED

- `sizing.GPU_TIERS`/`GpuTier.vram_gb` - stays strictly **per-GPU**, per the spec's own design note;
  aggregate is computed as `gpu_count * vram_gb`, not a new tier row.
- `source.builtin_catalog()`'s `CatalogModel` projection and `_derive_council_member_selection`'s existing
  by-name lookup pattern - extended with the new `num_attention_heads` field, mirroring how `has_quant`/
  `gated` are already threaded from `sizing.Model` through to `CatalogModel`.
- `ProvisionResult`'s existing `detail` string field - the multi-GPU note ("tensor-parallel across N
  GPUs") is surfaced there, mirroring how the tunnel note was surfaced in #627; no new result field needed.
- The existing guaranteed-teardown pattern in `LambdaLiveProvisioner.provision()` (unchanged) - and,
  better than teardown, the head-count refusal happens BEFORE `client.launch()` (the model and the parsed
  `gpu_count` are both known pre-launch), so an invalid combination never creates a billable VM at all,
  rather than creating one and tearing it down.
- The existing "custom model" escape hatch in `_derive_council_member_selection` (`model == "__custom__"`).

## What's new

1. **`anthill/hosting/sizing.py`**: `usable_gb`/`max_params_b`/`cloud_gpu_max_params`/
   `cloud_gpu_max_params_quantized` each gain an optional `gpu_count: int = 1` parameter (aggregate VRAM
   = `gpu_count * vram_gb`; overhead subtracted as `gpu_count * _GPU_OVERHEAD_GB`, matching the spec's
   exact formula; KV stays a flat subtraction against the aggregate pool, not multiplied - a real serving
   pool, not per-GPU-independent). `servable_on_one_gpu` renamed to `servable_on_gpu`, gains `gpu_count`,
   docstring rewritten (drops the now-false "single-GPU only" claim). `Model` gains
   `num_attention_heads: int = 0` (wired into `_model_from_row`, additive, defaults to unknown for every
   existing/seed catalog row - none populated in this PR, see grounding note 3). New pure function
   `gpu_count_divides_heads(num_attention_heads: int, gpu_count: int) -> bool` (True when heads is 0/unknown
   - non-blocking - or when it actually divides evenly).
2. **`anthill/hosting/provision.py`**: `ProvisionSpec.gpu_count: int = 1` (new, defaulted, additive to a
   frozen dataclass - safe for every existing keyword-based construction site). `_gpu_phrase`/`_vllm_steps`
   mention "tensor-parallel across N GPUs" in the plan preview when `gpu_count > 1` (cosmetic; the planner
   builds human-readable text only, never real launch args). `plan_summary()` gains a `gpu_count: int = 1`
   kwarg, threaded into the `ProvisionSpec(...)` it builds.
3. **`anthill/hosting/lambda_provision.py`**: new `gpu_count_from_instance_type(instance_type: str) -> int`
   (regex `^gpu_(\d+)x_`, defaults to 1 on no match/blank). `vllm_startup_script` gains `gpu_count: int = 1`,
   appends `--tensor-parallel-size <gpu_count>` to the docker command only when `> 1` (no behavior change
   to the single-GPU path, matching the spec's own acceptance criterion). `LambdaLiveProvisioner.provision()`:
   derives `gpu_count` from the `instance_type` kwarg it already receives; when `> 1`, looks up the model's
   `num_attention_heads` (via `source.builtin_catalog()`, by name) and refuses BEFORE `client.launch()` if
   it's known and doesn't divide evenly - no billable VM created for this failure mode. Success `detail`
   mentions the tensor-parallel size.
4. **`anthill/web/provision_run.py`**: `_spec_from_cfg` derives `gpu_count` from
   `cfg.org_lambda_instance_type` (via the new helper) and threads it into the `ProvisionSpec(...)` it
   builds, for the fit-gate/plan-preview path. `_MemberRef` and `_mirror_lead_into_council` gain the
   equivalent for council reviewers (mirroring the existing `org_lambda_instance_type`/`instance_type`
   pattern exactly - no new DB column, no new Form field, no new per-reviewer template markup needed,
   since `gpu_count` is entirely DERIVED from data these two paths already carry).
5. **`anthill/web/app.py`**: `_derive_council_member_selection` gains an `instance_type_raw: str = ""`
   parameter (threaded from both call sites: the lead's `settings_org_post`, and each reviewer's loop),
   derives `gpu_count` the same way, and passes it into the renamed `sizing.servable_on_gpu(...)` call plus
   the new head-count-divisibility check - returning a distinct refusal reason
   (`"gpu_count_mismatch"`) when the latter fails, so the save route can show a clear message rather than
   folding it into the generic `"too_big"` reason. `_council_selection_tuple` (the re-save change-detection
   tuple) includes the derived `gpu_count`/`instance_type`, so changing the Lambda instance type on a
   re-save is correctly treated as a change requiring re-provisioning (it already carries
   `org_lambda_instance_type`, so this is likely already covered - verify during implementation rather than
   assume). `_org_provisioning_plan` threads `gpu_count` into `plan_summary(...)`.

## Explicitly out of scope

- Multi-*node* / pipeline-parallel serving, Kubernetes, Ray (per the spec's own scope).
- RunPod serverless multi-GPU workers (per the spec's own scope; RunPod is a different worker model).
- `datacrunch_provision.py`, OVHcloud, Scaleway - none has a working serving bootstrap yet (DataCrunch's
  own module docstring flags this as a separate, already-known, unresolved gap); nothing to wire
  `--tensor-parallel-size` into. The spec's own scope names them as "planned," not built here.
- Training/fine-tuning parallelism (unchanged, per spec).
- Populating real attention-head-count data into `model_catalog.json`'s current seed/placeholder rows (see
  grounding note 3) - a follow-up catalog-curation task, not fabricated here.
- Making the Settings UI's model dropdown live-recompute its grey-out against a typed instance type (see
  grounding note 6) - the server-side gate is authoritative and already correctly handles this via the
  existing custom-model escape hatch; the dropdown's own UX is a separate follow-up.
- A new cost-cap/billing-guard mechanism (see grounding note 5) - guaranteed teardown (existing) is
  preserved; no such numeric cap exists in this subsystem today to "scale."

## Acceptance criteria (from the spec, mapped to this change)

**Unit-verifiable** (extend `tests/test_hosting.py`, `tests/test_lambda_provision.py`,
`tests/test_hosting_provision.py`, `tests/test_org_provisioning.py`):

1. With `gpu_count > 1`, the generated startup script contains `--tensor-parallel-size <gpu_count>`; with
   `gpu_count == 1` (or omitted) it does not - no behavior change to the existing single-GPU path.
2. `gpu_count_from_instance_type`: `"gpu_8x_h100"` -> 8, `"gpu_1x_a10"` -> 1, `""`/no match -> 1.
3. The renamed `servable_on_gpu`: a 671B model at 4-bit is servable against 8x141GB (aggregate) and not
   against a single 141GB GPU; every existing `servable_on_one_gpu`-shaped single-GPU case is unchanged
   under the new name with `gpu_count` defaulted to 1.
4. `gpu_count_divides_heads`: divides evenly -> True; known heads that don't divide -> False; heads=0
   (unknown) -> True (non-blocking).
5. A Lambda provision with `gpu_count > 1` and a known, non-dividing head count is refused BEFORE
   `client.launch()` is ever called (no billable VM: `client.launched is None`).
6. Cost/teardown: guaranteed-teardown behavior is unchanged for a multi-GPU instance (existing tests still
   pass with `gpu_count` threaded through).
7. `_derive_council_member_selection`: accepts a large model when `gpu_count` (derived from a typed
   multi-GPU instance type) makes it fit aggregate VRAM; still rejects it with `gpu_count` absent/1; a
   `gpu_count`/head-count mismatch returns the new distinct reason code.
8. Every existing test in the four files above still passes with the new parameters present (run the FULL
   suite, not just new/changed tests - this session's own established lesson from #669/#670).

**Live-only** (requires a real multi-GPU Lambda node; record on first live run, per #254/#355/#627's
precedent):

9. An 8-GPU node serves a 600B+-class model via vLLM tensor-parallel and answers an org-plane chat turn
   over the (already-built, #627) secure tunnel.
10. Throughput is acceptable (confirms NVLink is actually present on the instance type used).

`ruff check`, `ruff format --check`, `mypy`, full test suite must pass. No em/en-dashes, no
TODO/FIXME/XXX markers.
