#!/usr/bin/env bash
# ASDD operate - documentation agent runner (post-merge doc sync).
#
# Runs the documentation agent (Goose, on the roster's open model) against a MERGED change and writes its
# proposed doc updates to $OUT. It runs on TRUSTED input (a human already merged the change), so unlike
# the reviewer it is safe to give the agent a shell. It still opens no PR and writes no code: the workflow
# posts its output for a human. If the model is not wired or Goose is absent, it writes a dry-run preview.
#
# Usage: docsync.sh <change_ref> <out_file>
# Env (same as the reviewer gate): ASDD_RUNTIME_TOKEN, ASDD_MODEL_URL (full chat-completions URL), ASDD_MODEL.
set -euo pipefail

CHANGE_REF="${1:?docsync: change_ref required}"
OUT="${2:?docsync: out file required}"
RECIPE="$(cd "$(dirname "$0")/../../.." && pwd)/recipes/documentation.yaml"

dryrun() {
  {
    echo "## Documentation agent - dry run"
    echo
    echo "$1"
    echo
    echo "It would run the documentation agent against \`${CHANGE_REF}\` and propose the doc, impact-log,"
    echo "changelog, and knowledge-base updates the change needs. Wire the model to activate it:"
    echo "set the repo \`ASDD_MODEL_URL\` + \`ASDD_MODEL\` variables and the \`ASDD_RUNTIME_TOKEN\` secret"
    echo "(the same config the review gate uses)."
  } > "$OUT"
}

# Not wired -> dry run (same fail-soft posture as the review gate's template).
if [ -z "${ASDD_RUNTIME_TOKEN:-}" ] || [ -z "${ASDD_MODEL_URL:-}" ] || [ -z "${ASDD_MODEL:-}" ]; then
  dryrun "The model runtime is not wired (no ASDD_MODEL_URL / ASDD_MODEL / ASDD_RUNTIME_TOKEN)."
  exit 0
fi
if ! command -v goose >/dev/null 2>&1; then
  dryrun "Goose is not installed on the runner."
  exit 0
fi

# Live: point Goose's built-in openai provider at the OpenAI-compatible endpoint via env, derived from the
# same ASDD_MODEL_URL the review gate uses. (First live run should be verified once the secret is set.)
rest="${ASDD_MODEL_URL#*://}"
export OPENAI_API_KEY="$ASDD_RUNTIME_TOKEN"
export OPENAI_HOST="${ASDD_MODEL_URL%%://*}://${rest%%/*}"
export OPENAI_BASE_PATH="${rest#*/}"

report="$(goose run --recipe "$RECIPE" --provider openai --model "$ASDD_MODEL" \
  --params instructed_by=asdd-docsync --params change_ref="$CHANGE_REF" 2>&1 || true)"

# Keep only the agent's proposal section if it emitted one; otherwise pass the run through as-is.
if printf '%s' "$report" | grep -q '## Proposed doc updates'; then
  printf '## Documentation agent - proposed doc updates for `%s`\n\n' "$CHANGE_REF" > "$OUT"
  printf '%s\n' "$report" | sed -n '/## Proposed doc updates/,$p' >> "$OUT"
else
  dryrun "The documentation agent did not return a proposal (runtime error or empty output); a human should sync the docs."
fi
