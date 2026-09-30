# Solo tuning: a Solo account fine-tunes its own model on its own gold

## Status

Wires the Solo-tuning entry that the Solo-settings consolidation staged. Reuses the existing training
pipeline (local MLX train + serve, eval-gate, promote); the only change is the gold-selection scope + a
Solo-settings surface.

## Problem

Fine-tuning existed only for an org: `/training` (admin) trains on `scope == "org"` gold and promotes the
org model. A **Solo account** (a one-user tenant, `deployment_topology == "solo"`) accumulates
`scope == "personal"` gold that was never trained on, and had no way to tune its own model - even though
the local MLX train-and-serve path already exists and `is_local_training(cfg)` is already `topology ==
"solo"`.

## Principle

One model per account: a Solo account fine-tunes **its own model** on **its own** approved answers (the
user's personal gold), on **its own compute** (this machine / a self-hosted server such as a Mac mini),
and serves the result on-device (the Solo plane already picks up `local_serve_url`). Nothing leaves the
perimeter. An org still trains the shared org model on org-scope gold only.

## Requirements (EARS)

- WHEN a training run executes for a **Solo account** (local training), the system SHALL train on the
  user's **personal** gold (`scope == "personal"`, `quality == "gold"`); for an **org** account it SHALL
  stay org-scope gold only (the shared model never trains on personal/unreviewed signals).
- The `/training/run` gold-count guard SHALL use the same scope, so the count matches what will train.
- Solo settings SHALL show a **Tuning** section (for a Solo account only) with the approved-example count,
  the current status, whether a fine-tune is served, and a **Train now** action that starts a run.
- The tuned model SHALL be served on-device via the existing local MLX serve path; the eval-gate SHALL
  still guard promotion (a candidate only ships if it beats the incumbent).

## Implemented

- `training/executor._gold(db, org_id, cfg)`: personal-scope gold for a Solo account, else org-scope;
  `execute_run` passes `cfg`.
- `POST /training/run`: the gold-count guard chooses scope via `is_local_training(cfg)`.
- `/personalize` (Solo settings): `can_tune` / `tune_gold` / `tune_status` / `served_finetune` context +
  a Tuning card with a **Train now** button (posts `/training/run`), shown only for a Solo account.

## Follow-up

- A non-MLX Solo cloud (a remote GPU VPC rather than a self-hosted Mac) currently falls to the org-style
  remote path; a dedicated Solo remote-tune path is a later pass.

## Acceptance criteria

- `executor._gold(db, org_id, <solo cfg>)` returns the personal-scope gold; with an org cfg, org-scope.
- `POST /training/run` on a Solo account with personal gold starts a run (not "nothing to train on").
- `GET /personalize` for a Solo account shows the Tuning section + the personal gold count; an org
  account does not show it.
