# Spec: review-runtime reliability (endpoint normalization + out-of-diff lens calibration)

Status: proposed
Lane: `pillar:platform`
Relates to: [`.github/asdd/runtime/openai-compat.sh`](../../.github/asdd/runtime/openai-compat.sh),
[`.github/asdd/agents/review-quality.md`](../../.github/asdd/agents/review-quality.md),
OneHillAI/ASDD PR #38 (the framework-side handoff of these two items).

## 1. Introduction

Two reliability defects in the live review gate surfaced while dogfooding the ASDD roster on this repo.
Both make the gate behave wrongly on correct input, so both erode trust in it.

1. **A base `ASDD_MODEL_URL` fails closed silently.** `runtime/openai-compat.sh` POSTs to
   `ASDD_MODEL_URL` verbatim. If the value is the provider base (for example `https://provider/v1`)
   rather than the full chat-completions URL, the call returns a non-review body, the adapter finds no
   review JSON, and the gate fails closed to "a human should review" with no cause surfaced. On this repo
   that cost a debugging cycle before the missing `/chat/completions` was spotted.
2. **The adversarial lens blocks on claims it cannot verify.** The quality lens sees only the diff. When
   a change describes the behaviour of a file that is not in the diff (a doc that says "`foo.sh` does
   X"), the lens reasoned about a file it could not read and recommended request-changes against a true
   statement. Observed twice on one PR.

## 2. Requirements

### R1 - Normalize the endpoint, and say so
- WHEN `ASDD_MODEL_URL` does not already end in `/chat/completions`, THE adapter SHALL append it (after
  trimming a trailing slash) and SHALL emit a notice on stderr naming the URL it used, so the
  transformation is visible rather than silent.
- WHEN `ASDD_MODEL_URL` already ends in `/chat/completions`, THE adapter SHALL use it unchanged and emit
  no notice.

### R2 - The lens does not block on unverifiable out-of-diff claims
- WHEN the quality lens's objection depends on the behaviour of a file not present in the diff, THE lens
  SHALL record it as a `note` that names the file to check, and SHALL NOT raise a `warn` or `block` on
  that basis.

## 3. Design

- **R1**: `openai-compat.sh` gains a small normalization block before the request: trim a trailing slash,
  and unless the path already ends in `/chat/completions`, append it and print the notice. The `curl`
  call uses the normalized endpoint. Failing closed on a genuinely bad endpoint is still correct; only
  the silence was the bug.
- **R2**: a paragraph in `review-quality.md`'s fixed instruction prompt tells the lens to judge only what
  it was shown and to downgrade an unverifiable out-of-diff claim to a `note`. This is the light,
  prompt-level calibration; giving the lens read access to referenced files is a heavier follow-up on the
  analysis job and is out of scope here.

## 4. Tasks

- [ ] `.github/asdd/runtime/openai-compat.sh`: endpoint normalization + notice (R1).
- [ ] `.github/asdd/runtime/openai-compat.test.sh` + `tests/test_asdd_runtime.py`: normalization test (R1).
- [ ] `.github/asdd/agents/review-quality.md`: out-of-diff calibration (R2).
- [ ] `docs/SYSTEM_IMPACT_LOG.md` entry.

## 5. Out of scope

- **Giving the review lens read access to files a diff references** so it can verify out-of-diff claims
  rather than only downgrade them. A heavier change to the analysis job; R2 is the light calibration.
- **The reviewer model driven from `.asdd.yml` `models.reviewer`** (ASDD #28): the adapter still reads a
  single `ASDD_MODEL`. Same wiring surface, separate change.
