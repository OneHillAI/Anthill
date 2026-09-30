# Phase 6a: disk-space capacity checking (foundation for unified onboarding)

## Why

The founder corrected a standing assumption this session: Solo and Org accounts must have IDENTICAL
model/compute/council capability - the only thing "creating an organization" should gate is inviting
teammates (shared wiki/model/chats/agents/tasks), never model or council access itself. Building the
resulting unified onboarding flow (a later change) requires checking, at onboarding time, whether a
machine can actually carry the models it is about to be offered - not just in RAM/VRAM (already checked)
but on DISK, since every model must be downloaded and stored before it can run. Today NOTHING checks disk
space anywhere in this codebase (verified: `grep -rn "disk_space|free_disk|disk_gb|shutil.disk_usage"
anthill/` matches only fixed `disk_gb` PARAMETERS passed to cloud training-pod launches - never a
capability CHECK of available-vs-required space).

This is deliberately split out as its own change: it is a small, fully self-contained, independently
testable capability (mirroring the existing `free_mem_gb()` hardware probe and the existing
`onprem_council_fits()` memory fit-gate), and the onboarding-flow rebuild that consumes it is large enough
to deserve its own separate review.

## What already exists (verified, `origin/main`)

- `anthill/hosting/sizing.py`'s `family_download_gb(params_b) -> float` (line 577) already estimates a
  model's 4-bit on-disk/download size in GB from its parameter count - today this is DISPLAY-ONLY, fed
  into `FamilyPick.download_gb` for `recommend_by_family()`'s local picker. It is never compared against
  actual free disk space.
- `anthill/hosting/sizing.py`'s `onprem_council_fits(member_params_b: list[float], ...) -> bool` (line 368,
  shipped in an earlier phase) already sums per-member RAM/VRAM cost against one shared machine's memory
  budget for a local council - this is the exact pattern to mirror for disk, not re-invent.
- `anthill/hosting/sizing.py` already has hardware-probe functions for memory: `_macos_mem_gb()`,
  `_nvidia_vram_gb()`, `_posix_ram_gb()`, `free_mem_gb()`, `local_hardware()` (returns `(mem_gb, kind)`).
  There is no disk-space equivalent of any of these today.

## What this change adds

1. A disk-space hardware probe, `free_disk_gb(path: str = ...) -> float | None`, mirroring the existing
   `free_mem_gb()` convention (returns `None` when it cannot be determined, never raises - callers must
   treat `None` as "could not probe, do not block on a bad probe," exactly like `onprem_council_fits`
   already does for a failed memory probe). Use `shutil.disk_usage()` (stdlib, cross-platform) rather than
   shelling out - simpler and more portable than the existing `sysctl`/`vm_stat` memory probes.
2. A disk fit-gate that sums the on-disk size of a set of models being proposed together (single model, or
   a council of N on-prem members) via the existing `family_download_gb()`, and compares the sum against
   `free_disk_gb()`, leaving comfortable headroom (state and justify your chosen headroom fraction/GB
   explicitly - downloading up to the last free byte risks the OS itself failing, exactly the kind of
   real-world constraint `_COMFORT_FREE_GB`/`_APPLE_USABLE` already encode for memory). Mirror
   `onprem_council_fits`'s signature/shape (a list of `params_b` in, a bool out) rather than inventing a
   different calling convention for the same kind of question.
3. Wherever a family/model is deemed "fits" for RAM/speed purposes (`recommend_by_family`,
   `onprem_council_fits`), disk space becomes a THIRD, equally-real precondition - a model that fits in
   memory and runs at a usable speed but will not fit on disk is not actually recommendable. State plainly
   in the PR whether this is folded into the EXISTING functions' return shape (e.g. a new field on
   `FamilyPick`/`ModelFit`) or exposed as a separate check the onboarding flow calls alongside them - either
   is acceptable, but do not silently change existing callers' behavior without updating their tests.

## Explicitly out of scope (belongs to the follow-up "unified onboarding" change)

- The onboarding flow itself (sign-up -> Solo/Org choice -> compute picker -> auto council-size suggestion
  -> personalize -> wiki -> privacy -> done).
- Cloud/VPC-side storage checking ("in the cloud" storage the founder also asked about) - a rented GPU
  instance's disk allocation is a PROVISIONING-time concern (`disk_gb` params already exist in
  `runpod_train.py`/`wiki_host.py`), not a local-hardware-probe concern; deciding how/whether to surface a
  free-tier-storage check for a rented instance is part of the onboarding-flow change, not this one.
- Any UI - this change is pure `anthill/hosting/sizing.py` capability, consumed later.
- Any changes to the existing legacy Settings/dashboard copy that conflates "create an organization" with
  "get model/council access" (`dashboard.html`'s "Create an organization" card, verified to literally say
  "connect a shared backend (its own model + wiki)" as something gated behind creating an org) - that
  cleanup belongs to the onboarding-flow change, since it is a UI/copy fix, not a capability this one adds.

## Acceptance criteria

1. `free_disk_gb()` exists, follows the `free_mem_gb()` None-on-failure convention, uses `shutil.disk_usage`
   (stdlib), and is tested with a mocked/monkeypatched filesystem path (do not depend on the CI runner's
   actual free space, which is not controllable or repeatable).
2. A disk fit-gate exists mirroring `onprem_council_fits`'s calling convention (list of `params_b` in, bool
   out, or an equivalent shape you justify), with an explicit, justified comfort-headroom constant (do not
   compare against the raw free-byte count).
3. Existing `recommend_by_family`/`onprem_council_fits` callers and their tests are updated consistently if
   disk becomes a field on their existing return types; otherwise the new disk check is exposed as its own
   function with its own tests, and neither existing function's current tests need behavior changes.
4. `ruff check`, `ruff format --check`, `mypy`, and the full test suite pass. No em/en-dashes, no
   TODO/FIXME/XXX markers.
