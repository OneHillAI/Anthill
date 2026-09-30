# Spec: owner review override

Status: proposed
Lane: `pillar:platform`
Relates to: [`.github/asdd/`](../../.github/asdd), [`.asdd.yml`](../../.asdd.yml), `AGENTS.md`

## 1. Introduction

The org owner sometimes needs to merge past the ASDD gates - a hotfix, a change the advisory review
flags but the owner has judged, a trivial edit that would otherwise need a spec. ASDD's rule is "humans
approve, no silent bypass", so the escape hatch must be **explicit, identity-bound, and on the record**,
not a quiet skip.

The mechanism the human direction asked for: a **tag checked against the owner's account**. A PR is
exempt from the ASDD gates blocking its merge only when BOTH hold:

1. Its **author** is one of `review_override_owners` in `.asdd.yml` (read from the trusted base copy).
2. It carries the **`owner-override`** label.

Author-binding is the security: a label alone cannot bypass (anyone with triage can label), and the
override only frees the owner's **own** PRs. The gates still **run and comment** - the override stops
them *blocking*, never *running*, so the audit trail is intact and every use is visible.

## 2. Requirements

### R1 - Identity-bound, explicit override
- WHEN a PR's author is in `review_override_owners` AND the PR carries the `owner-override` label, THE
  SYSTEM SHALL treat the PR as owner-overridden.
- THE SYSTEM SHALL read `review_override_owners` from the **base** `.asdd.yml` (a PR cannot add itself to
  the owners list to self-authorize), and SHALL take the author from trusted event metadata.
- IF either the author is not an owner OR the label is absent, THEN THE SYSTEM SHALL NOT override
  (a non-owner applying the label, or an owner not applying it, changes nothing).

### R2 - Override stops blocking, not running
- WHERE a PR is owner-overridden, THE intake gate SHALL pass even if a requirement is unmet, AND the
  review's merge-gating status SHALL be success even on a `request-changes`/security-`block` review.
- THE SYSTEM SHALL still run intake and the review and post their output, and SHALL record that the
  override was used and by whom - the override is auditable, not silent.

### R3 - Empty by default
- IF `review_override_owners` is empty/unset, THEN no PR is ever overridden (the feature is opt-in per
  install, and adds no exemption until an owner is named).

## 3. Design

### 3.1 Shared computation - `.github/asdd/owner-override.sh`
One small, dependency-free script decides the override so intake and the review status agree:
`owner-override.sh <author> <labels-file>` prints `true` iff `<author>` is in `review_override_owners`
(parsed from `.asdd.yml`) AND `owner-override` is in the labels file. Case-insensitive on the login.

### 3.2 Intake (`asdd-intake.yml` + `intake-check.sh`)
The intake workflow calls `owner-override.sh` with the event author + collected labels and writes
`override=true|false` into `meta.env`. `intake-check.sh`: when `override` is true it sets `passed=true`
and adds an advisory note naming the override (the unmet problems are retained in the output, not
hidden), and emits an `override` field.

### 3.3 Review status (`set-status.sh`)
`set-status.sh` fetches the PR author + labels (`gh pr view`) and calls `owner-override.sh`. When
overridden it sets `asdd/review` to `success` with a description that names the override, instead of the
`failure` a `request-changes`/security-block would set. `post-review.sh` still posts the full review.

## 4. Tasks

- [ ] `.github/asdd/owner-override.sh` + `tests/test_asdd_owner_override.py` (R1, R3).
- [ ] `.asdd.yml`: `review_override_owners` (with the owner) (R1).
- [ ] `.github/workflows/asdd-intake.yml`: compute + pass `override` (R2).
- [ ] `.github/asdd/intake-check.sh`: consume `override` (R2).
- [ ] `.github/asdd/set-status.sh`: consume the override for the merge status (R2).
- [ ] `docs/SYSTEM_IMPACT_LOG.md` entry.

## 5. Out of scope

- A blanket admin bypass (that is GitHub branch-protection's job); this is the explicit, auditable,
  per-PR ASDD-level hatch the direction asked for.
- Overriding another person's PR - the override is for the owner's own work; approving someone else's PR
  is ordinary human review.
