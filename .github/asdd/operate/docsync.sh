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

# The run was attempted and did not produce a proposal. Not a dry run: the model WAS wired, so say what
# actually happened (exit code plus the tail of Goose's output, key redacted) instead of telling a human to
# "wire the model" when it is already wired. That misleading text hid a bad endpoint path for days.
failed() {
  local detail="${2:-}"
  detail="${detail//"$ASDD_RUNTIME_TOKEN"/[redacted]}"
  {
    echo "## Documentation agent - did not run to completion"
    echo
    echo "$1"
    echo
    if [ -n "$detail" ]; then
      echo "Last lines of the agent's output (key redacted):"
      echo
      echo '```'
      printf '%s\n' "$detail"
      echo '```'
      echo
    fi
    echo "A human should sync the docs for \`${CHANGE_REF}\`. Run the \`ASDD runtime check\` workflow to"
    echo "confirm the documentation agent's model, endpoint and key are connected."
  } > "$OUT"
}

# Live: point Goose's built-in openai provider at the OpenAI-compatible endpoint via env, derived from the
# same ASDD_MODEL_URL the review gate uses. Goose takes the host and the FULL request path separately, so a
# bare base URL (https://provider/v1, the common way to write it) would make Goose POST to /v1 and get a
# 404. Normalize exactly as the review gate's openai-compat.sh does: strip a trailing slash and append
# /chat/completions when it is missing, so both spellings of the variable work.
endpoint="${ASDD_MODEL_URL%/}"
case "$endpoint" in
  */chat/completions) ;;
  *) endpoint="$endpoint/chat/completions" ;;
esac
rest="${endpoint#*://}"
export OPENAI_API_KEY="$ASDD_RUNTIME_TOKEN"
export OPENAI_HOST="${endpoint%%://*}://${rest%%/*}"
export OPENAI_BASE_PATH="${rest#*/}"

rc=0
report="$(goose run --recipe "$RECIPE" --provider openai --model "$ASDD_MODEL" \
  --params instructed_by=asdd-docsync --params change_ref="$CHANGE_REF" 2>&1)" || rc=$?

# Keep only the agent's proposal section if it emitted one; otherwise say what went wrong.
if printf '%s' "$report" | grep -q '## Proposed doc updates'; then
  printf '## Documentation agent - proposed doc updates for `%s`\n\n' "$CHANGE_REF" > "$OUT"
  printf '%s\n' "$report" | sed -n '/## Proposed doc updates/,$p' >> "$OUT"
elif [ "$rc" -ne 0 ]; then
  failed "The documentation agent failed (goose exit code ${rc}) against ${OPENAI_HOST}/${OPENAI_BASE_PATH}." "$(printf '%s\n' "$report" | tail -n 8)"
else
  failed "The documentation agent ran but returned no proposal section." "$(printf '%s\n' "$report" | tail -n 8)"
fi
