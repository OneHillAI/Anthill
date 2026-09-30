# ASDD - the contribution pipeline (this repo)

The gates that run on every PR. See `FOUNDING_CONTRIBUTORS_GUIDE.md` and
`PILLAR_CHARTERS_AND_GOVERNANCE.md` in the `internal` repo for the why, and
[OneHillAI/ASDD](https://github.com/OneHillAI/ASDD) for the standard.

## What runs

- `asdd-intake.yml` - deterministic gate: authorship disclosure + DCO sign-off + exactly one lane
  tag. No model, read-only.
- `pr-review.yml` (read-only) + `pr-review-publish.yml` (write-scoped) - the advisory review. The
  analysis job produces a review artifact; a separate write-scoped job runs each action through the
  policy decision point, sets the `asdd/review` status, and posts one advisory comment. It never
  merges.

## Turning the review lenses live

Out of the box the lenses dry-run (they post a "no model wired" placeholder). To review with a real model:

1. Set the repository **secret** `ASDD_RUNTIME_TOKEN` to your model API key.
2. Set the repository **variables** `ASDD_MODEL_URL` (a chat-completions URL, for example
   `https://<provider>/v1/chat/completions`) and `ASDD_MODEL` (the model name).
3. Open a test PR. The advisory comment now reflects a real review; the analysis job still holds no write
   scope.

Recommended: a benchmark-leading open model via a managed OpenAI-compatible endpoint. The runtime is
pluggable (`runtime/<name>.sh` + `runtime:` in `.asdd.yml`); `generic.sh` + `openai-compat.sh` is
the default.

## The security lens (deterministic + SAST + model)

`security_scan.py` runs inside the read-only analysis job and feeds the `security` lens in three layers:

1. **Deterministic rules** over the added diff lines: committed private keys / cloud keys, disabled TLS
   verification, dangerous sinks (eval/exec, insecure deserialization, shell injection), pipe-to-shell,
   Trojan-Source bidi/zero-width Unicode, and injected-instruction markers. No dependencies.
2. **OSS SAST** (`bandit`) over the changed Python files at head, filtered to the added lines, installed
   best-effort by `pr-review.yml`. If unavailable, layer 1 still stands.
3. The **model** `security` lens (when a runtime is wired) merges in.

Reviewed code is data: the scanner reads bytes and runs static checks, and never imports, evals, or
shell-executes any of it. A `block` finding makes `set-status.sh` fail the `asdd/review` status,
so the gate is mechanical and works even in dry-run. Suppress a specific line with a visible `# nosec`
or `# asdd: ignore` comment.

## Conformance

The analysis job (`pr-review.yml`) holds `contents: read` only; write scope lives solely in
`pr-review-publish.yml`, which never reads untrusted PR content. The policy decision point
(`policy-check.sh`) allow-lists `comment`/`label` and denies `merge`. The adversarial quality lens runs as
a separate inference so it cannot rubber-stamp the others.
