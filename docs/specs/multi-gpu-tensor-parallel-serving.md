# Spec: single-node multi-GPU (tensor-parallel) serving

Status: done (Lambda only). Built for `hosting/lambda_provision.py`; OVHcloud/Scaleway remain planner
stubs with no live `provision()` to wire up, and `hosting/datacrunch_provision.py` has no confirmed
serving-bootstrap mechanism yet (a separate, already-known gap) - neither has anything to add
`--tensor-parallel-size` to. Grounding decisions made during implementation, so a later session doesn't
re-litigate them:

- `gpu_count` is DERIVED by parsing Lambda's own `gpu_{N}x_...` instance-type naming convention
  (`gpu_count_from_instance_type`), not a separate admin-set field - this also satisfies the "genuine
  multi-GPU node with a fast interconnect" requirement by construction, since every instance type shaped
  this way in Lambda's real catalog is an NVLink/NVSwitch node, never PCIe-spread.
- Attention-head-count data (`Model.num_attention_heads`) is NOT populated for `model_catalog.json`'s
  current seed/placeholder rows - several of that catalog's largest entries do not correspond to any
  real, currently-published model card, and inventing specific head-count numbers for them would be
  fabricating unverifiable facts. The divisibility check is non-blocking when unknown (0); vLLM's own
  runtime refusal is the actual backstop, not this pre-flight check. A follow-up catalog-curation pass
  can populate real values once the catalog is refreshed with real current models.
- "Cost guardrails SHALL scale with GPU count" is read as: the existing guaranteed-teardown guarantee
  (unchanged) continues to hold for a multi-GPU instance - there is no other numeric cost-cap mechanism
  in this subsystem (`hosting/lambda_provision.py`/`hosting/provision.py`/`hosting/runpod_provision.py`)
  to scale, and inventing one was out of scope.
- The Settings UI's model-dropdown grey-out ("needs multi-GPU serving") still reflects only the
  single-GPU ceiling - it is NOT made to live-recompute against a typed instance type. The server-side
  fit gate (`_derive_council_member_selection`) is authoritative and already correctly accepts a
  multi-GPU-eligible model via the existing "custom model" escape hatch; the dropdown's own UX is a
  separate follow-up, not built here.

Lane: `pillar:model`
Relates to: [`llm-endpoint-secure-transport.md`](llm-endpoint-secure-transport.md) (the secure-transport fix
applies unchanged to a multi-GPU node - it is still a bare VM), and `hosting/sizing.py` +
`hosting/model_catalog.json` (the capability gate, owned by the catalog workstream).

## Problem

Anthill serves every provisioned model on a **single GPU**. `vllm_startup_script`
(`hosting/lambda_provision.py`) launches

    docker run -d --gpus all -p 8000:8000 vllm/vllm-openai:latest --model '<model>' --port 8000 --api-key '<key>'

with **no `--tensor-parallel-size`**, so vLLM uses one GPU even on a multi-GPU box (`--gpus all` exposes the
devices; without the flag vLLM defaults to tensor-parallel size 1). `ProvisionSpec` (`hosting/provision.py`)
has no `gpu_count`; it sizes one GPU via `gpu_vram_gb` or an explicit `instance_type`. The fit gate
(`sizing.cloud_gpu_max_params` / `cloud_gpu_max_params_quantized` / `model_fits_vram`) keys on a single
`vram_gb`.

The hard consequence: the largest servable model is capped by **one GPU's memory** - about 70B at 4-bit on
an 80GB H100, ~225B at 4-bit on a 141GB H200. The frontier-scale open models the product wants to offer -
DeepSeek 671B, Kimi K2 (~1T), GLM (~744B), Llama 405B - need **400GB-600GB+ of weights** and cannot be
served on any single GPU. They require tensor-parallel across the GPUs of one node (e.g. 8x H100 = 640GB,
8x H200 = 1128GB).

Without this, the "model x cloud" offering can only ever list ~70B-class models. This spec makes
frontier-scale open models servable, which is the whole point of offering a sovereign alternative to the
closed frontier APIs.

## Scope

**In:** single-node, multi-GPU tensor-parallel serving on the bare-VM (`vpc`) tier - Lambda today, and the
planned OVHcloud / Scaleway provisioners. This is the 90% case: one instance with 2/4/8 GPUs and NVLink.

**Out:** multi-*node* serving (pipeline-parallel across machines, Kubernetes, Ray) - a much larger change,
not needed to reach 1T-class models, which fit one 8-GPU node. Also out: RunPod **serverless** multi-GPU
(a different worker model; tracked separately). Training/fine-tuning parallelism is unchanged.

## Requirements (EARS)

- `ProvisionSpec` SHALL carry the node's GPU count, and the serving launch SHALL pass
  `--tensor-parallel-size <gpu_count>` to vLLM whenever `gpu_count > 1`.
- The capability/fit gate SHALL size against **aggregate** node VRAM
  (`gpu_count * per_gpu_vram - gpu_count * per_gpu_overhead - kv`), not a single GPU's VRAM, so a model is
  offered on a (model, provider) pair iff it fits the node the provider can actually launch.
- The provisioner SHALL only set a tensor-parallel size that vLLM accepts: `gpu_count` MUST evenly divide
  the model's attention-head count (vLLM constraint - in practice 2, 4, or 8), and the instance MUST be a
  genuine multi-GPU node with a fast interconnect (NVLink/NVSwitch), not GPUs spread across PCIe on a slow
  bus. WHERE these do not hold, provisioning SHALL refuse with a clear reason rather than launch a broken
  or pathologically slow server.
- Cost guardrails SHALL scale with GPU count (an 8-GPU node is ~8x the hourly cost); the existing per-run
  cost cap and guaranteed teardown SHALL be preserved for the larger instance.
- Secure transport ([`llm-endpoint-secure-transport.md`](llm-endpoint-secure-transport.md)) SHALL apply to
  the multi-GPU node unchanged - it is still a bare VM with a public IP.

## Design

### Provisioning side (this workstream / my lane)

1. **`ProvisionSpec.gpu_count: int = 1`** (`hosting/provision.py`), threaded from a new
   `OrgSettings.org_gpu_count` (or parsed from `instance_type` where the SKU name encodes it, e.g. Lambda
   `gpu_8x_h100`). `gpu_vram_gb` stays the *per-GPU* target; aggregate is `gpu_count * gpu_vram_gb`.
2. **vLLM launch** (`vllm_startup_script` and any Scaleway/OVH equivalent): append
   `--tensor-parallel-size <gpu_count>` when `gpu_count > 1`. `--gpus all` already exposes every device.
   Keep `--api-key`. Transport is orthogonal to GPU count: the secure-transport work
   ([`llm-endpoint-secure-transport.md`](llm-endpoint-secure-transport.md) - refuse-by-default is already
   enforced in `lambda_provision.py`; the secure channel itself is still to be implemented) applies to the
   multi-GPU node unchanged. Do not couple the two changes.
3. **Instance selection:** the admin's model choice sets the required aggregate VRAM (via the gate below);
   the provisioner picks the smallest self-serve multi-GPU instance meeting it, or honours an explicit
   `instance_type`. The concrete per-provider SKU list (which multi-GPU nodes are self-serve vs
   quota-gated) comes from the provider research run, not this spec.

### Capability gate (catalog workstream / coordinate)

The fit predicate must move to aggregate VRAM. `sizing.cloud_gpu_max_params*` / `model_fits_vram` (and the
catalog session's `servable_on_gpu(model, vram_gb)`) SHALL be called with **node-aggregate** usable VRAM,
and per-GPU overhead (`_GPU_OVERHEAD_GB`, ~5GB) SHALL be multiplied by `gpu_count`. MoE models still gate on
**total** `params_b` (all experts resident), not `active_b`. This is a shared source of truth - the picker
and the provisioner must agree on the same aggregate-VRAM predicate.

### Coordination note

The capability-gate change (aggregate VRAM, overhead * gpu_count) straddles the catalog workstream's
`servable_on_gpu` and this provisioner change. Land the predicate signature first
(`servable_on_gpu(model, aggregate_vram_gb, gpu_count)` or equivalent) so both sides consume one function.

## Acceptance

**Unit-verifiable** (extend `tests/test_lambda_provision.py` + a sizing test, model-free with an injected
client):

- With `gpu_count > 1`, the generated launch command contains `--tensor-parallel-size <gpu_count>`; with
  `gpu_count == 1` it does not (no behaviour change to the existing single-GPU path).
- The fit gate accepts a 671B 4-bit model against an 8x141GB node and rejects it against a single 141GB
  GPU.
- A `gpu_count` that does not divide the model's head count is refused with a clear reason.
- Cost cap and guaranteed teardown hold for the multi-GPU instance (a failed provision leaves no billable
  node).

**Live-only** (requires a real multi-GPU node; verify on a first live run and record it, per #254/#355):

- An 8-GPU node serves a 671B-class model via vLLM tensor-parallel and answers an org-plane chat turn.
- Throughput is acceptable (NVLink present; tensor-parallel is not bottlenecked on a slow bus).

## Out of scope

- Multi-node / pipeline-parallel serving, Kubernetes, Ray.
- RunPod serverless multi-GPU workers.
- Training/fine-tuning parallelism.
- Choosing the per-provider multi-GPU SKUs - that is the provider research deliverable.
