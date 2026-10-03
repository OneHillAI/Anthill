# Spec: ASDD checks must say what actually happened

Status: implemented
Lane: `pillar:platform`
Relates to: `.github/asdd/` (`set-status.sh`, `post-review.sh`, `runtime/generic.sh`, `operate/docsync.sh`),
`.github/workflows/asdd-runtime-check.yml`, [`asdd-model-roster.md`](asdd-model-roster.md)

## 1. Problem

On 2026-10-02 two PRs showed red ASDD checks and nobody could tell why. Tracing it back found the
gate had not been doing its job for days, and it looked fine the whole time:

1. **Dry-run reviews looked like real reviews.** The review gate reads three repo settings
   (`ASDD_MODEL`, `ASDD_MODEL_URL`, the `ASDD_RUNTIME_TOKEN` secret). While they were empty, every
   review ran as a placeholder ("DRY-RUN, no runtime token"), 23 of the 25 reviews since 30 September.
   `set-status.sh` gave a placeholder the same green "Advisory review complete; a human approves and
   merges." as a real review, so a PR that no AI had looked at read as reviewed.
2. **A model answering with garbage was recorded as a live review.** When the runtime returned
   output that was not review JSON, `generic.sh` failed closed to a comment but stamped it
   `mode: "live"`, so it also read as a real review that found nothing.
3. **The documentation agent failed silently, and blamed the wrong thing.** `docsync.sh` derives
   Goose's `OPENAI_HOST` and `OPENAI_BASE_PATH` from `ASDD_MODEL_URL`. Goose needs the host and the
   *full* request path, but a base URL (`https://api.infercom.ai/v1`, how the variable is naturally
   written, and how the review gate's adapter already accepts it) gave `OPENAI_BASE_PATH=v1`, so Goose
   POSTed to `/v1` and got a 404. The error was swallowed (`|| true`) and the posted report said the
   agent "did not return a proposal" and told a human to "wire the model", when the model was wired.
4. **Nothing noticed a disconnected runtime.** `ASDD runtime check` (the honest "is every agent
   connected" probe) ran only when `.asdd.yml` or its scripts changed, so it ran once, and empty or
   expired settings could sit unseen for as long as nobody touched those files.

## 2. Requirements

R1. A review whose mode is not `live` (`dry-run`, `adapter-template`, `degraded`, or anything
unrecognised) is never described as a completed review. The `asdd/review` status description reads
`NO AI REVIEW RAN (<reason>). A human must review this PR.`

R2. The status **state** for such a review stays `success`. An unwired runtime must never block a
merge: fork and Dependabot PRs receive no secrets and dry-run by design, and the gate is advisory. A
real failure (a security block, request-changes) keeps its `failure` state and description unchanged,
and the owner-override path is unchanged.

R3. The advisory comment says so in its body for every non-live mode, not only `dry-run`.

R4. A model that returns unusable output is recorded as `mode: "degraded"` (still failing closed to a
non-blocking comment), not `live`.

R5. `docsync.sh` accepts `ASDD_MODEL_URL` as a base URL (with or without a trailing slash) or as the
full chat-completions URL, and gives Goose the host plus `v1/chat/completions` in every case,
matching what the review adapter (`openai-compat.sh`) already does.

R6. When Goose fails or returns no proposal, `docsync.sh` posts an honest report: that it did not run
to completion, the Goose exit code, the target endpoint, and the last lines of output with
`ASDD_RUNTIME_TOKEN` redacted. It does not tell a human to "wire the model" when the model is wired.
The workflow stays advisory (the script still exits 0).

R7. `ASDD runtime check` also runs on a daily schedule, so a missing or expired key or variable shows
up within a day.

## 3. Acceptance criteria

- `tests/test_asdd_honest_status.py` passes, and 12 of its 16 tests fail against the previous scripts
  (the other 4 pin behaviour that must not change: a live review's text, a real failure, a proposal
  passing through, a live comment carrying no warning).
- For each of `dry-run`, `adapter-template`, `degraded`: status state `success`, description starts
  `NO AI REVIEW RAN`, and the posted comment names the mode and says no AI review ran.
- For a live review: the description is still `Advisory review complete; a human approves and merges.`
  and a `request-changes` review is still `failure` / `Review recommends changes.`
- `generic.sh` with a model command that prints non-JSON writes `mode == "degraded"`.
- For `https://api.infercom.ai/v1`, `.../v1/` and `.../v1/chat/completions`, Goose receives
  `OPENAI_HOST=https://api.infercom.ai` and `OPENAI_BASE_PATH=v1/chat/completions`.
- A failing Goose yields a report containing `did not run to completion` and the exit code, and not
  the API key.
- The existing ASDD tests and `openai-compat.test.sh` still pass.

## 4. Out of scope

- Making the review gate or docsync read the per-role roster in `.asdd.yml` (they still use the single
  shared `ASDD_MODEL`; the per-role resolution lives in `cli/resolve-model.sh`, used by the runtime
  check). That is a larger feature and a separate spec.
- Making `asdd/review` a required check, or failing a PR whose review did not run. Both would block
  fork and Dependabot PRs, which cannot hold the key; the honest description is the fix, not a block.
- Enforcing the no-Chinese-model rule in a check (it is policy in the roster spec only today).
