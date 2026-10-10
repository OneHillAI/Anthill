# Spec: a design-only objection from the adversarial review does not fail the status

Status: implemented
Lane: `pillar:platform`
Relates to: `.github/asdd/set-status.sh`, `.github/asdd/runtime/generic.sh`, `docs/asdd-anthill-process.md`,
[`asdd-honest-review-status.md`](asdd-honest-review-status.md), [`owner-review-override.md`](owner-review-override.md)

## 1. Problem

The ASDD review runs two model calls: the code, security and spec lenses, and an independent adversarial
quality lens whose job is to refute "looks good". `generic.sh` merges them with "skeptic wins": the review
recommends changes if EITHER call does. `set-status.sh` then turns any `request-changes` into a failing
`asdd/review` status.

In practice the adversarial lens finds a design objection on almost every PR (net complexity, a display-side
patch instead of a source fix, a new abstraction, an edge case) and it changes its objection each time the PR
is touched. On 2026-10-06 to 2026-10-09 that put a red status on PRs #98, #102, #104 and #106, whose code and
security lenses were ok. Each round of "fix the objection, push, wait for the checks" re-ran every check, and
#102 merged with the status red. A red status on a PR that has no defect trains reviewers to ignore red.

The review is advisory (`docs/asdd-anthill-process.md`: "a human approves and merges"), and `asdd/review` is
not one of the required checks.

## 2. Requirements

R1. When the recommendation is `request-changes` and the adversarial `quality` lens is present, the status
state is `success` and the description reads `Advisory: only the adversarial design pass recommends changes;
code, security and spec are ok. A human decides.` if, and only if, ALL of these hold:
- the review ran live (`mode` is `live` or absent);
- every lens other than `quality` (code, security, spec, impact or any other) has verdict `ok` (or no verdict)
  and no `warn` or `block` finding;
- no lens, including `quality`, has a `block` finding;
- there is no security `block` finding.

R2. In every other case a `request-changes` keeps its `failure` state and the description
`Review recommends changes.`, and a security block keeps `Security review raised a blocking finding; needs a
human resolution.` A review with no lens detail at all (an unknown source of the objection) stays a failure,
and so does one whose lens findings cannot be read: the rule only ever softens when it can positively show the
other lenses are clean.

R3. A non-live review (`dry-run`, `adapter-template`, `degraded`) and the owner-override path are unchanged.
A review that did not run live is never treated as design-only, whatever its lenses say, so it keeps the
failure it had before this change.

This change supersedes one sentence of [`asdd-honest-review-status.md`](asdd-honest-review-status.md): that a
live `request-changes` review is always `failure` / `Review recommends changes.` It now holds except for the
design-only case above.

R4. The review comment is unchanged: the adversarial objection is still posted in full, with its
recommendation, so nothing is hidden. Only the merge-gating status stops failing on it.

R5. `docs/asdd-anthill-process.md` says what fails the status and what does not.

## 3. Acceptance criteria

- `tests/test_asdd_honest_status.py` pins: a quality-only `request-changes` is `success` with the design-only
  description; the same with a code, security, spec or impact lens that is not ok, or with a `warn`, is
  `failure`; a `block` finding anywhere is `failure`; a security block keeps its own failure line;
  `request-changes` with no lenses, or with no `quality` lens, is `failure`; an unreadable lens keeps the
  failure; a non-live review is never softened; a `comment` recommendation, the live no-objection line and the
  non-live descriptions are unchanged. Run against the previous `set-status.sh`, the two quality-only cases
  fail (the rule is what turns them green) and the cases that must stay failures pass.
- The unchanged set-status tests still pass.
- `post-review.sh` and the owner-override path are untouched by this change (R3, R4).

## 4. Not in this change

- Making `asdd/review` a required check, or changing what the lenses are asked.
- Changing the adversarial prompt so that it finds fewer objections. The objection stays visible in the comment.
- Anything about PRs from outside contributors beyond the status they see: whether to merge past an advisory
  stays the maintainer's decision.

## 5. Risk

A genuine design problem that only the adversarial lens sees no longer shows as a red status. It is still
posted as the review comment, and the status description names it. A human who merges without reading the
comment is the risk the advisory model already accepts.
