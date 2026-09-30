# Runbook: the first live LoRA run on RunPod (#254)

The pipeline that fine-tunes the org model on a rented RunPod GPU is code-complete and unit-tested behind
an injected client, but the parts that touch a real RunPod account have never spent real money. This
runbook is the checklist for the **first real run** - the launch-critical validation tracked by #254.
Expect a fix-and-retry loop; that is the point of doing it deliberately, once, with the guardrails on.

The whole path is: **approve gold -> Train now -> a GPU pod trains -> the adapter comes back -> eval-gate
-> promote + serve on a win -> the pod is torn down.** Cost is capped and a reaper backstops any leak.

## 0. Preconditions (once)

- **RunPod account + key configured as the org's serving provider.** Training reuses the serving RunPod
  account - there is no separate token. Settings -> Organization: choose RunPod, paste the API key. That
  stores `org_provision_key_enc`, which both serving and training use.
- **Gold to train on.** Training only runs on **gold** examples (admin-approved / thumbs-up). Confirm you
  have enough: `anthill export-training --quality gold` prints the count and whether it is
  `ready_to_train`. The eval-gate needs at least a handful of distinct held-out instructions to trust a
  promotion; a first model (no incumbent) is exempt but still needs gold to train on.
- **SSH key for the pod.** `ANTHILL_TRAINING_SSH_KEY` must point at the private key that reaches the pod
  over SSH (the trainer ships the scrubbed gold and runs the container over SSH).
- **Pod image.** Defaults to `runpod/pytorch:2.2.0-py3.10-cuda12.1.1`
  (`ANTHILL_RUNPOD_POD_IMAGE` to override). The image must have Docker + an NVIDIA runtime so
  `containerized_train` can run the trainer container.
- **Cost cap.** The per-run worst-case cost is capped (default $25, `aws_max_cost_usd`) and refused before
  any pod launches. Max runtime is `aws_max_runtime_min` (default 120, hard ceiling 24h).

## 1. Preflight (each run)

1. **Validate the backend (no GPU launched).** On `/training`, run the connectivity/credentials check
   (POST `/settings/training/test`). It confirms the RunPod key is present and set to reuse the cloud
   account; it does **not** claim a live-verified GPU - that is what this run is for.
2. **Confirm no leftover pods.** `anthill train-reap --dry-run`. On a clean account it prints "nothing to
   reap". If it lists a pod, clear it first: `anthill train-reap` (or `--min-age 0` to reap immediately on
   a known-idle account). You want to start from zero so you can see exactly what this run creates.
3. **Sanity-check the base model maps to a Hugging Face repo.** The PEFT/NVIDIA path needs the served tag
   (e.g. `qwen3:8b`) mapped to an HF repo (`trainer._hf_base_model`, added in #578). The common tags are
   mapped; an unmapped tag raises a clear error at load. `ANTHILL_HF_MODEL` overrides.

## 2. Initiate

Click **Train now** on `/training` (or the Tuning tab / the personalize page), which POSTs `/training/run`
and executes the run in the background. This is the step you run when you are ready to spend.

(You can also let the 24h scheduler fire it, but for a deliberate first run, Train now is the one to use.)

## 3. Watch

- **In-app:** `/training` shows readiness + status. The `TrainingRun` row moves
  `scheduled -> running -> promoted | rejected | failed`, with an `eval_note`.
- **On RunPod:** the dashboard shows one pod named `anthill-train-<base_model>` appear, train, and
  disappear. If it lingers after the run ends, see step 5.
- **First-run unknowns to confirm** (isolated in `runpod_train._RealRunpodPodClient`, like the serving
  provisioner was): the exact pod input fields on `podFindAndDeployOnDemand`, the SSH port shape in
  `runtime.ports`, and whether the chosen image actually runs the trainer. These are the most likely
  fix-and-retry points.

## 4. Verify a win actually serves

On a winning eval, the run shows `promoted` and `training_model_ver` bumps. Confirm the promoted model is
actually **served**, not just registered (the #578 fix):

- No separate org endpoint -> `cfg.ollama_model` is now `org<id>-model-v<N>`.
- Org endpoint is the same local Ollama -> `cfg.org_model` is.
- A **remote** serving endpoint (RunPod/vLLM) -> nothing is repointed by design, and the `eval_note` says
  the adapter must be deployed there to activate it. Deploying the adapter to a remote endpoint is the
  other deferred #254 item; until then, serving stays on the previous model.

Then ask the served model something and confirm you get the new behaviour.

## 5. Teardown + leak check

- A healthy run tears its pod down in a `try/finally`, on success and failure alike.
- The **reaper** is the backstop for the edges that skip that teardown (the orchestrator dies mid-run, or
  a pod is half-created then errors): it runs automatically every ~10 min and terminates any
  `anthill-train-*` pod that has outlived a run, never a healthy in-flight one. After the run, confirm
  clean: `anthill train-reap --dry-run` should show nothing. If it reaped something, that is a leak the
  run hit - note the circumstances on #254.
- Regardless, glance at the RunPod dashboard to confirm no pod is still running and billing.

## Still open after this run (track on #254)

- **In-pod self-terminate** - a pod that shuts itself down after its max runtime even if the orchestrator
  never returns (the reaper covers the account side; this covers the pod side). Needs the live pod image.
- **Remote-endpoint adapter deploy** - pushing a promoted LoRA adapter to a remote serving endpoint so a
  win on that topology actually serves.
