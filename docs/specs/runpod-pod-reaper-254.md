# Spec: RunPod training-pod reaper

Status: implemented. Lane: `pillar:model`. Issue: #254 (the deferred ops item from
`training-preflight-254.md`).

## Problem

A LoRA fine-tune on the RunPod backend rents a GPU **pod** (`training/backends/runpod_train.py`).
`RunpodTrainer.train()` tears that pod down in a `try/finally`, so a healthy run never leaves a billable
GPU. But two edges escape that guarantee, both flagged by the #254 pre-flight audit:

1. **Orchestrator death mid-run.** If the process running `train()` is killed between the pod launch and
   the `finally` (a crash, an OOM, a container redeploy, a machine reboot), the `finally` never runs and
   the pod keeps billing until a human notices.
2. **Partial launch.** `client.launch()` sits just *outside* the `try/finally` (it has to - there is no
   pod id to terminate until it returns), so a launch that creates a pod and then raises before returning
   the id leaks it.

Nothing on the account terminates such a pod, so a rented GPU can bill indefinitely.

## Requirements

- A reaper lists the account's training pods and terminates any that have outlived a run, closing both
  leak edges.
- It must **never** terminate a healthy in-flight training pod. The threshold is derived from whether a
  run is currently active, so a live run's pod is always outside the reap window.
- Pure and injectable, like `RunpodTrainer`: the reaping logic takes a `RunpodPodClient`, so it is
  unit-tested with a fake client - no network, no live account, no spend.
- Best-effort: it never raises into its caller; a provider hiccup or a per-pod terminate failure is
  recorded and the sweep continues (terminate is idempotent, so the next sweep retries).
- Runs automatically on a slow cadence (the scheduler) **and** on demand (a CLI command), scoped to orgs
  that actually train on RunPod (backend `endpoint` + provider `runpod`) with a key set.
- A reaped leak is recorded in the audit log and surfaced to the org's admins through the notification
  centre - a leaked GPU is money.

## How

- **Identity by name.** Every training pod is named `anthill-train-<base_model>`
  (`runpod_train.TRAIN_POD_PREFIX`, used both where the name is built and where the reaper matches). The
  reaper matches that prefix, so it only ever touches Anthill's own training pods.
- **Age by uptime.** `_RealRunpodPodClient.list_pods()` returns each pod's `runtime.uptimeInSeconds` as
  `age_minutes`. A pod still provisioning has no uptime and reads as age 0, so a just-launched pod is
  never mistaken for a leak.
- **Safe threshold (`reaper.plan_max_age`).** With a run active, only a pod older than the longest run
  the org could configure (`max_runtime` clamped to 24h, + a 60m margin) is reaped - beyond any healthy
  run. With no run active, any training pod is orphaned, so it is reaped after a short 15m grace (which
  covers the brief window where a pod is launched but its `TrainingRun` row has not flipped to
  `running`).
- **`reaper.sweep(client, active_run, run_max_min)`** applies that threshold via `reap_orphaned_pods`.
- **Scheduler:** `scheduler._reap_tick` runs the sweep for each RunPod org, time-gated to ~10 minutes so
  the RunPod API is not hit every tick; a reap audit-logs `training.pod_reaped` and notifies the admins.
- **CLI:** `anthill train-reap [--dry-run] [--min-age N] [--org ID]` runs the same sweep on demand. It is
  the pre-run cleanup and check tool. `--min-age` overrides the safe threshold (e.g. `--min-age 0` reaps
  every leaked pod now, for a known-idle account).

The complementary in-pod self-terminate (a pod that shuts itself down after a max runtime even if the
orchestrator never comes back) needs the live pod image and is validated on the first real run; it is
tracked with the other first-run validation items in `docs/RUNPOD_LIVE_RUN_RUNBOOK.md`. The reaper stands
alone without it.

## Acceptance criteria

- `reap_orphaned_pods` terminates only `anthill-train-*` pods at least `max_age_minutes` old, ignores
  other pods and young ones, and records per-pod terminate errors without stopping the sweep.
- With a run active and `max_runtime` 120, a 150m pod is kept and a 200m pod is reaped; with no run
  active, a 20m pod is reaped and a 5m pod is kept.
- A failed `list_pods` yields an errored-but-empty result, never a raise.
- The name `RunpodTrainer.train()` builds starts with `TRAIN_POD_PREFIX` and is matched by the reaper.
- `_RealRunpodPodClient.list_pods()` parses the RunPod `myself.pods` shape (uptime to age, missing
  runtime to 0, id-less entries dropped).
- The scheduler tick terminates a leaked pod, writes a `training.pod_reaped` audit row, and notifies the
  org admins.
- Covered by `tests/test_runpod_reaper.py` (fully mocked); existing training tests still pass.
