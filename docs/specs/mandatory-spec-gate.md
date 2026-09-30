# Spec: mandatory spec gate (spec-driven by default)

Status: proposed
Lane: `pillar:platform`
Relates to: [`AGENTS.md`](../../AGENTS.md), [`.github/asdd/`](../../.github/asdd),
[`.asdd.yml`](../../.asdd.yml), [`.github/asdd/agents/review-spec.md`](../../.github/asdd/agents/review-spec.md)

## 1. Introduction

ASDD is spec-driven, but today that is not enforced: intake gates disclosure, DCO, one lane, and the
open-PR cap, but not "is this change based on a spec." The `spec` review lens flags a missing spec only
as a `warn`, so a spec-less change can still merge. Now that a model runtime is wired (the lenses run
live), we can make the intent real: **a non-trivial PR must be based on a spec, and the review agent
tells the author what is needed when it is not.**

Two ways to satisfy it, per the human direction:

1. **Link an existing spec** - the PR references a `docs/specs/*.md` (a path in the description or a
   `Spec:` trailer), and the change is checked against it.
2. **Include a spec** - if none exists, the PR adds a `docs/specs/<name>.md` that it implements and is
   then counted against.

Trivial `chore` changes (a typo, a version bump) are exempt - a spec for a one-line fix is noise.

## 2. Requirements

### R1 - Intake requires a spec on non-trivial PRs
As a maintainer, I want intake to fail a substantive PR that is not based on a spec, so spec-driven is
the default, not a hope.

- WHERE `require_spec` is true in `.asdd.yml` and the PR's single lane is not `chore`, THE SYSTEM SHALL
  fail intake unless the PR either references an existing `docs/specs/*.md` (a path in the body or a
  `Spec:` trailer) OR adds/edits a `docs/specs/*.md` file in its diff.
- THE SYSTEM SHALL keep the check deterministic: the changed-file list is produced by the intake
  workflow and `intake-check.sh` only pattern-matches it and the body (no network, no model).
- IF `require_spec` is false/unset, THEN THE SYSTEM SHALL skip the check (backward compatible).
- THE SYSTEM SHALL exempt the `chore` lane (trivial by definition, `.asdd.yml`).

### R2 - The review agent says what is needed
As a contributor whose PR lacks a spec, I want the review to tell me exactly what to do, so I can fix it
in one pass.

- WHEN the change is non-trivial and no spec is linked or included, THE SYSTEM SHALL have the `spec`
  lens report a `block` (not a `warn`) whose message states the concrete next step: add a
  `docs/specs/<name>.md` that states the problem, the requirements, and the acceptance criteria, or link
  the existing spec this implements.
- WHEN a spec is present, THE SYSTEM SHALL have the `spec` lens check the change against it and, on a
  mismatch, name the requirement or spec section that is unmet and what to change.

### R3 - Two enforcement layers, one message
- THE SYSTEM SHALL enforce the requirement deterministically at intake (fail fast, no model) AND advise
  it through the live `spec` lens comment (actionable guidance). The two agree: no spec is a hard stop
  and a clear comment.

## 3. Design

### 3.1 Fit
This extends the existing intake gate and the `spec` lens; it adds no runtime code and no new mechanism.
The changed-file list reaches `intake-check.sh` through `meta.env`/a workdir file, mirroring how
`pr_number`, `head_sha`, and the open-PR count already flow in. `chore` exemption reuses the lane label.

### 3.2 Intake
- `.asdd.yml`: `require_spec: true`.
- `.github/workflows/asdd-intake.yml`: write `changed.txt` (`git diff --name-only base..head`) into the
  workdir and pass `require_spec` from `.asdd.yml` via `meta.env`.
- `.github/asdd/intake-check.sh`: `spec_ok`; a non-chore PR with `require_spec` and neither a referenced
  nor an included `docs/specs/*.md` fails, with a message that states both fixes. Absent inputs skip it.

### 3.3 Review lens
- `.github/asdd/agents/review-spec.md`: a missing spec on a non-trivial change becomes a `block`, and
  the prompt instructs the lens to give the concrete step to make the PR spec-conformant.

## 4. Tasks

- [ ] `.asdd.yml`: `require_spec: true`.
- [ ] `.github/workflows/asdd-intake.yml`: `changed.txt` + `require_spec` into the workdir.
- [ ] `.github/asdd/intake-check.sh`: `spec_ok` check (R1, R3).
- [ ] `.github/asdd/agents/review-spec.md`: block + actionable guidance (R2).
- [ ] `.github/PULL_REQUEST_TEMPLATE.md` + `CONTRIBUTING.md`: the spec requirement.
- [ ] `tests/test_asdd_intake_gate.py`: spec-requirement cases.
- [ ] `docs/SYSTEM_IMPACT_LOG.md` entry.

## 5. Out of scope / next

- **Including the linked spec's text in the review data** so the `spec` lens can check conformance
  against an *existing* (not in-diff) spec. Today the lens sees an included spec (it is in the diff) but
  not a linked one; it flags the latter as unverifiable. Fetching referenced `docs/specs/*.md` into the
  review workdir is a focused follow-up on the live review path.
