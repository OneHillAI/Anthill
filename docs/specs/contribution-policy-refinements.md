# Spec: contribution-policy refinements

Status: proposed
Lane: `pillar:platform`
Relates to: [`AGENTS.md`](../../AGENTS.md), [`CONTRIBUTING.md`](../../CONTRIBUTING.md),
[`.github/asdd/`](../../.github/asdd), [`.asdd.yml`](../../.asdd.yml),
[`.github/PULL_REQUEST_TEMPLATE.md`](../../.github/PULL_REQUEST_TEMPLATE.md)

## 1. Introduction

ASDD exists partly to prevent the failure mode of one author opening dozens of undisclosed,
low-quality PRs. The intake gate already enforces disclosure, DCO, and one lane label on every PR. This
spec adds three refinements drawn from mature open projects, adapted to Anthill's **real** channels (a
PR, a GitHub issue, the in-app `/contribute` flow, and private `SECURITY.md` - Anthill has no Discord):

1. **Route contributions by type** so the right ones become PRs and the rest go where they belong.
2. **An anti-flood cap** on how many PRs one author may have open at once.
3. **An understanding attestation** - the author affirms they have read and stand behind the change,
   which is exactly the accountability ASDD's disclosure is for.

These are policy, not runtime code; they touch the contributor-facing docs, the PR template, and the
deterministic intake gate (never the model path, which stays dry-run until a runtime is wired).

## 2. Requirements

### R1 - Route by contribution type
As a would-be contributor, I want to know where each kind of contribution goes, so I do not open a PR
that should have been an issue (or a security email).

- THE SYSTEM SHALL document, in `CONTRIBUTING.md`, where each contribution type goes: a bug or small fix
  -> a PR directly; a feature or architecture change -> a GitHub issue (or the in-app `/contribute`
  flow) first; a refactor/test/CI-only change -> only when a maintainer asked; a security issue ->
  privately per `SECURITY.md`, never a public issue or PR.
- THE SYSTEM SHALL reference only channels Anthill actually has (no Discord).

### R2 - Anti-flood cap on open PRs per author
As a maintainer, I want a ceiling on simultaneously-open PRs per author, so no single author can flood
the queue (the pattern ASDD was created to stop).

- WHERE `max_open_prs_per_author` is set in `.asdd.yml`, THE SYSTEM SHALL fail intake for a PR whose
  author already has more than that many open PRs in the repo.
- THE SYSTEM SHALL keep the deterministic intake check pure: the network count is computed by the
  intake workflow (read-only) and passed to `intake-check.sh`, which only compares two numbers.
- IF no count is supplied or the cap is unset/zero, THEN THE SYSTEM SHALL skip the check (backward
  compatible; older callers and local runs are unaffected).
- THE SYSTEM SHALL add no write scope to the intake job; exceeding the cap surfaces through the existing
  intake status, not a new posting path.

### R3 - Understanding attestation
As a reviewer, I want the author to affirm they understand and stand behind the change - especially when
an agent wrote it - so authorship is accountable, not just disclosed.

- THE SYSTEM SHALL add an attestation to the PR template's disclosure: the author has read and
  understands the change and takes responsibility for it.
- THE SYSTEM SHALL NOT make this a new hard intake gate, so PRs opened before the template change are
  not retroactively failed; it strengthens the existing disclosure rather than adding a fourth blocker.

### R4 - DCO counts authored commits, not merge commits
As a contributor, I want to bring my branch up to date with `main` without breaking intake, so that
keeping a PR current never turns a green PR red.

- THE SYSTEM SHALL build the commit list that intake checks for a DCO sign-off with `git log --no-merges`,
  so a merge commit (for example the one GitHub's "Update branch" button creates, or a plain
  `git merge origin/main`) is not required to carry a `Signed-off-by:` line.
- THE SYSTEM SHALL still require a sign-off on every authored (non-merge) commit in the PR range.
- WHY: a merge commit carries no change of its own and cannot be signed without rewriting history, which
  contributors and agents here never do (no force-push). Counting it made "update branch" a permanent red
  `intake` with no clean fix (PR #63, 2026-10-03). The ASDD kit made the same change upstream.

### R5 - Open bot PRs are kept current with main automatically
As a maintainer, I want an open PR to be brought up to date with `main` for me when another PR merges, so I
am never the one clicking "Update branch" and waiting for CI again.

- WHEN a commit is pushed to `main`, THE SYSTEM SHALL ask GitHub to merge `main` into every open, non-draft
  PR that the bot opened, from a branch in this repository, against `main`, and that is behind it
  (`.github/workflows/sync-bot-prs.yml`, `scripts/sync_bot_prs.py`).
- THE SYSTEM SHALL do this with the bot App's token, so that CI starts on the updated head, and SHALL stay a
  clean no-op until the App's repository secrets exist.
- THE SYSTEM SHALL make API calls only: it never checks out, reads or runs anything from a PR, and the
  workflow holds `contents: read` only. A merge conflict is reported as a warning and left alone.
- THE SYSTEM SHALL NOT touch PRs opened by anyone else, draft PRs, or PRs from forks.
- WHY: the merge ruleset will not merge a branch that is behind `main`, so every merge made every other open PR
  stale and cost a manual update and a CI re-run each time (2026-10-03 and 2026-10-04). A merge commit made by
  the update carries no sign-off; R4 means intake does not count it.

## 3. Design

### 3.1 Fit
All three land in contributor-facing surfaces and the deterministic intake gate. Nothing touches
`anthill/` runtime code or the model review path. The cap follows the pipeline's own split - network in
the workflow, pure logic in the checked script - mirroring how `pr_number`/`head_sha` already reach
`intake-check.sh` through `meta.env`.

### 3.2 Cap enforcement
- `.asdd.yml`: `max_open_prs_per_author: 20` (a generous default; the "dozens of PRs" pattern is far
  above it).
- `.github/workflows/asdd-intake.yml`: add `pull-requests: read`, read the cap from `.asdd.yml`, count
  the PR author's open PRs via the API, and write `open_pr_count` + `max_open_prs` into `meta.env`.
- `.github/asdd/intake-check.sh`: `flood_ok = (count empty OR cap<=0 OR count<=cap)`; a fail adds a
  problem and joins `passed`. Uses `${var:-}` defaults so an absent count under `set -u` is safe.

### 3.3 Attestation and routing
- `.github/PULL_REQUEST_TEMPLATE.md`: one attestation checkbox in the disclosure block.
- `CONTRIBUTING.md`: a short "Before you open a PR" routing table; a line on the cap and the attestation.

## 4. Tasks

- [ ] `CONTRIBUTING.md`: "Before you open a PR" routing + cap + attestation notes (R1, R2, R3).
- [ ] `.github/PULL_REQUEST_TEMPLATE.md`: attestation checkbox (R3).
- [ ] `.asdd.yml`: `max_open_prs_per_author` (R2).
- [ ] `.github/asdd/intake-check.sh`: numeric cap check, backward compatible (R2).
- [ ] `.github/workflows/asdd-intake.yml`: read-only open-PR count into `meta.env` (R2).
- [ ] `tests/test_asdd_intake_gate.py`: model-free cap-logic tests over the script.
- [ ] `.github/workflows/asdd-intake.yml`: `git log --no-merges` for the DCO commit list (R4).
- [ ] `tests/test_intake_dco_merge_commits.py`: a merge commit does not fail DCO; an unsigned authored commit still does (R4).
- [ ] `scripts/sync_bot_prs.py`, `.github/workflows/sync-bot-prs.yml` and `tests/test_sync_bot_prs.py`: bring behind bot PRs up to date on every push to main (R5).
- [ ] `docs/SYSTEM_IMPACT_LOG.md` entry.

## 5. Out of scope

- New engagement channels (a Discord/Slack contribution bridge) - Anthill has no Discord, and the
  primary surface is its own chat + `/contribute`; a bridge is a separate, larger piece.
- Making the spec lens a hard gate or wiring a model runtime (unchanged: dry-run until a token is set).
