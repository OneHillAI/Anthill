# Spec: Training-pipeline hardening for the launch-critical live run

Status: implemented (2 of 3 gaps closed here; a 3rd is topology-scoped). Lane: `pillar:model`. Issue: #254.

## Problem

A pre-flight audit of the org LoRA pipeline (ahead of the launch-critical live run) found three code
gaps on the live (RunPod / onprem NVIDIA) path that a real run would hit, in this order:

1. **Base-model tag not resolvable by transformers.** `resolve_base_model` returns the served model as
   an Ollama-style tag (e.g. `qwen3:8b`). The PEFT/NVIDIA path passed it straight to
   `AutoModelForCausalLM.from_pretrained(base_model)` with no tag to HF-repo mapping (only the MLX path
   mapped tags), so a RunPod/onprem run fails at model load.
2. **A promoted model was registered but never served.** On a win, `execute_run` ran
   `ollama create org{id}-model-v{N}` and bumped `training_model_ver`, but never re-pointed the served
   model. `training_model_ver` is read only by CLI/metrics/templates/backup, never by inference/plane
   routing, so the run showed `promoted` while chat/agents kept using the old model.
3. **A run could be double-provisioned.** "Train now" spawns `run_scheduled` without the scheduler's
   `_executing_training` guard, and `execute_run` flipped status with a plain write (no atomic claim), so
   Train-now racing the 24h tick could launch two billable pods for one run.

## Changes

1. `trainer._hf_base_model(tag)` mirrors `_mlx_base_model`: maps common served tags (qwen2.5, qwen3,
   llama3.1/3.2, mistral, gemma2) to their HF repos, passes through anything that already looks like a
   repo/path, honours an `ANTHILL_HF_MODEL` override, and raises a clear `TrainerError` for an unmapped
   tag. `_train_peft` resolves the base model through it before `from_pretrained`.
2. `executor._activate_org_serving(cfg, model_name)` re-points the served model at the promoted tag on a
   win on the Ollama path - **guarded**: it sets `cfg.ollama_model` when there is no separate org
   endpoint, or `cfg.org_model` when the org endpoint is the same local Ollama the fine-tune was
   registered in (`org_model_endpoint` starts with `ollama_url`). For a **remote** serving endpoint
   (RunPod/vLLM) it changes nothing and records that the adapter must be deployed there to activate it -
   pointing `org_model` at a tag the remote endpoint lacks would break serving. The MLX path already
   re-points serving itself.
3. `execute_run` claims the run with a conditional `UPDATE ... WHERE status='scheduled'` and only the
   worker whose update flips the row proceeds; a losing worker bails without provisioning.

Not in this PR (topology / ops): deploying the LoRA adapter to a **remote** serving endpoint, and a
RunPod training-pod reaper + pod self-terminate for the launch-window / orchestrator-death leak edges.

## Acceptance criteria

- `_hf_base_model` maps the common tags, passes through repo ids, honours the override, and raises on an
  unmapped tag.
- After a winning run with no separate org endpoint, `cfg.ollama_model` is the promoted tag; with the
  org endpoint pointing at the local Ollama, `cfg.org_model` is; with a remote endpoint, neither changes
  and the note says so - all while still recording `promoted` + the version bump.
- A run already flipped to `running` is not executed a second time (no backend call).
- Covered by `tests/test_training_preflight_254.py`; existing `tests/test_training_execution.py` still
  passes (claim + promote/reject flow unchanged).
