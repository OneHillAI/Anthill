# Tasks: PR #636 / Tier 4 single-node multi-GPU tensor-parallel serving

## Build steps

1. `anthill/hosting/sizing.py`:
   - `usable_gb(mem_gb, *, kind, context_k, concurrency, gpu_count: int = 1)`: for `kind == "gpu"`,
     `max(0.0, (mem_gb - gpu_count * _GPU_OVERHEAD_GB) - kv)`. `kind == "apple"` branch unaffected
     (gpu_count is meaningless there; do not thread it through that branch).
   - `max_params_b(..., gpu_count: int = 1)`: forward to `usable_gb(..., gpu_count=gpu_count)`.
   - `cloud_gpu_max_params(vram_gb, *, gpu_count: int = 1)` / `cloud_gpu_max_params_quantized(vram_gb, *,
     gpu_count: int = 1)`: forward `gpu_count`, call sites pass `vram_gb` as the PER-GPU value; the
     aggregate math happens inside via the `gpu_count` forwarding, not by pre-multiplying at the call site
     - i.e. `cloud_gpu_max_params(vram_gb=80, gpu_count=8)` internally computes against
     `mem_gb=8*80=640` GB less `8*_GPU_OVERHEAD_GB` overhead. Confirm the exact mem_gb computation point
     (likely inside `max_params_b` or a small helper) before writing - do not pass a pre-multiplied
     `vram_gb` into a `gpu_count=1` call by mistake (double-counts or under-counts overhead).
   - Rename `servable_on_one_gpu` -> `servable_on_gpu`, add `gpu_count: int = 1`, update its docstring
     (remove "Serving is single-GPU only - the provisioners never set tensor-parallel").
   - Add `Model.num_attention_heads: int = 0` (wire into `_model_from_row`: `int(row.get(
     "num_attention_heads") or 0)`). Do NOT populate values in `model_catalog.json` (seed/placeholder
     catalog, see proposal.md's grounding note 3).
   - Add `gpu_count_divides_heads(num_attention_heads: int, gpu_count: int) -> bool`: `True` if
     `num_attention_heads <= 0` (unknown, non-blocking) or `gpu_count <= 1` or
     `num_attention_heads % gpu_count == 0`.
2. `anthill/hosting/source.py`: `CatalogModel` gains `num_attention_heads: int = 0`;
   `builtin_catalog()` threads `num_attention_heads=m.num_attention_heads` through, mirroring the
   existing `gated`/`has_quant` construction.
3. `anthill/hosting/provision.py`: `ProvisionSpec.gpu_count: int = 1`. `_gpu_phrase`/`_vllm_steps`: when
   `spec.gpu_count > 1`, mention "tensor-parallel across {gpu_count} GPUs" in the `launch_instance`/
   `load_model` step text (cosmetic only - confirm exactly which step reads `_gpu_phrase` before editing).
   `plan_summary(..., gpu_count: int = 1)`, threaded into the `ProvisionSpec(...)` it builds.
4. `anthill/hosting/lambda_provision.py`:
   - New `gpu_count_from_instance_type(instance_type: str) -> int`: `re.match(r"gpu_(\d+)x_", instance_type
     or "")`, group(1) as int if matched, else `1`.
   - `vllm_startup_script(model, api_key, *, authorized_keys_line: str = "", gpu_count: int = 1) -> str`:
     append `--tensor-parallel-size {gpu_count}` to the docker command only when `gpu_count > 1` (confirm
     exact insertion point relative to `--model`/`--port`/`--api-key` - order likely doesn't matter to
     vLLM's arg parser, but keep it readable).
   - `LambdaLiveProvisioner.provision()`: after resolving `instance_type` (existing logic, unchanged),
     compute `gpu_count = gpu_count_from_instance_type(instance_type)`. If `gpu_count > 1`: look up the
     model's `num_attention_heads` (via `source.builtin_catalog()`, match by `spec.model` - confirm
     whether `spec.model` at this point is the display name or the resolved HF id/tag; `_spec_from_cfg`
     resolves via `source.servable_id` BEFORE constructing `ProvisionSpec`, so `spec.model` is likely
     already the HF id, not the catalog display name - the lookup-by-name path may need the ORIGINAL
     display name threaded through instead, or a lookup-by-hf_id fallback; resolve this exactly during
     implementation, do not guess). If known and `not gpu_count_divides_heads(heads, gpu_count)`: return
     `ProvisionResult(ok=False, status="error", detail=...)` BEFORE calling `client.launch()` - no
     instance created, nothing to terminate. Otherwise proceed exactly as today, passing
     `gpu_count=gpu_count` into `vllm_startup_script(...)`. Success `detail` mentions
     `"tensor-parallel across {gpu_count} GPUs"` when `> 1`.
5. `anthill/web/provision_run.py`:
   - `_spec_from_cfg`: add `gpu_count=lambda_provision.gpu_count_from_instance_type(cfg.org_lambda_instance_type)
     if cfg.org_provider == "lambda" else 1` (or equivalent - keep it provider-gated, matching how
     `org_lambda_instance_type` itself is only meaningful for Lambda) into the `ProvisionSpec(...)` call.
   - `_MemberRef`: no new property needed if `gpu_count` is purely derived from `org_lambda_instance_type`/
     `org_lambda_ssh_keys`-style `provider_config` data already exposed via the existing
     `org_lambda_instance_type` property - confirm whether a derived (non-stored) `org_gpu_count` property
     is still useful for callers, or whether callers should call `gpu_count_from_instance_type` directly
     on `.org_lambda_instance_type`. Prefer NOT adding a stored field - keep gpu_count fully derived.
6. `anthill/web/app.py`:
   - `_derive_council_member_selection(..., instance_type_raw: str = "")`: derive `gpu_count` via the new
     helper; when checking fit, call the renamed `sizing.servable_on_gpu(catalog_params, tier.vram_gb,
     gpu_count=gpu_count, quantized=..., has_quant=...)`; when `gpu_count > 1`, also check
     `sizing.gpu_count_divides_heads(<model's num_attention_heads>, gpu_count)` (resolve the by-name
     catalog lookup for `num_attention_heads`, mirroring the existing `catalog_params`/`quant_models`
     lookups already in this function) - return a new reason `"gpu_count_mismatch"` on failure, distinct
     from `"too_big"`.
   - Both call sites (`settings_org_post`'s lead call, and the reviewer loop) pass their respective
     `org_lambda_instance_type`/`reviewer_lambda_instance_type_{i}` value as the new
     `instance_type_raw` argument.
   - `_council_selection_tuple`: verify it already includes `org_lambda_instance_type` (likely does, since
     that's an existing saved field) - if so, no change needed there for change-detection; confirm rather
     than assume.
   - `_org_provisioning_plan`: thread `gpu_count=lambda_provision.gpu_count_from_instance_type(instance_type)
     if cfg.org_provider == "lambda" else 1` into `provision.plan_summary(...)`.
7. Tests:
   - `tests/test_hosting.py`: extend the "cloud GPU tiers" section - `usable_gb`/`cloud_gpu_max_params*`
     with `gpu_count` (aggregate math correctness, matching the spec's own 671B-on-8x141GB /
     rejected-on-1x141GB acceptance example); `servable_on_gpu` under its new name (keep every existing
     `servable_on_one_gpu`-shaped assertion, just renamed, plus new multi-GPU cases);
     `gpu_count_divides_heads` (divides, doesn't divide, unknown-heads).
   - `tests/test_lambda_provision.py`: `gpu_count_from_instance_type` unit tests;
     `vllm_startup_script`'s new `gpu_count` kwarg (flag present/absent); a `provision()` test asserting
     the tensor-parallel flag reaches the launched startup script for a `gpu_8x_h100`-shaped
     `instance_type`; a test asserting a known head-count mismatch refuses BEFORE `client.launch()` (assert
     `client.launched is None`).
   - `tests/test_hosting_provision.py`: `_gpu_phrase`/`_vllm_steps`/`plan_summary` gpu_count-aware text,
     if added.
   - `tests/test_org_provisioning.py`: extend the fit-gate POST tests with a multi-GPU-instance-type case
     that makes an otherwise-too-big model acceptable, and a case where a stated instance type implies a
     `gpu_count` that doesn't divide a (test-fixture) known head count.
   - Run the FULL suite at the end, not just these four files - #669/#670's own lesson from this session.
8. `docs/specs/multi-gpu-tensor-parallel-serving.md` (brought into this PR, see proposal.md): flip
   `Status: proposed` to `Status: done`, add a short note on the grounding decisions (attention-head data,
   instance-type-derived gpu_count, cost-guardrail interpretation, UI scope) so a later session doesn't
   re-litigate them.
9. `changelog.d/636.added.md`.
10. `ruff check`, `ruff format --check`, `mypy`, full `pytest` - must be green (not just new/changed files).

## Explicitly out of scope

Same as proposal.md's "Explicitly out of scope" section - multi-node/K8s/Ray, RunPod serverless
multi-GPU, DataCrunch/OVH/Scaleway, training parallelism, catalog head-count curation, UI dropdown
live-recompute, a new cost-cap mechanism.
