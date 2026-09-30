#!/usr/bin/env bash
# ASDD - OpenAI-compatible model command. Reads a prompt on stdin, calls a chat-completions
# endpoint, and prints the model's review JSON on stdout. Used by runtime/generic.sh.
#
# Config (env): ASDD_RUNTIME_TOKEN (the API key, the repo secret), ASDD_MODEL_URL (the full
# chat-completions URL, e.g. https://<provider>/v1/chat/completions), ASDD_MODEL (the model name).
# This holds NO GitHub write scope. It prints data; it never executes anything.
set -euo pipefail

: "${ASDD_RUNTIME_TOKEN:?openai-compat: ASDD_RUNTIME_TOKEN (API key) not set}"
: "${ASDD_MODEL_URL:?openai-compat: ASDD_MODEL_URL not set}"
: "${ASDD_MODEL:?openai-compat: ASDD_MODEL not set}"

prompt="$(cat)"

# Normalize the endpoint. The classic misconfiguration is setting ASDD_MODEL_URL to the base
# (e.g. https://provider/v1) instead of the full chat-completions URL. We POST to it verbatim, so a
# base URL returns a non-review body and the gate fails closed with no surfaced cause. Append the path
# and say we did it, rather than fail silently.
endpoint="${ASDD_MODEL_URL%/}"
case "$endpoint" in
  */chat/completions) ;;
  *) echo "openai-compat: ASDD_MODEL_URL ('$ASDD_MODEL_URL') is not a chat-completions endpoint; using '$endpoint/chat/completions'. Set the full URL to silence this notice." >&2
     endpoint="$endpoint/chat/completions" ;;
esac

payload="$(jq -n --arg m "$ASDD_MODEL" --arg p "$prompt" \
  '{model:$m, temperature:0, messages:[{role:"user", content:$p}], response_format:{type:"json_object"}}')"

resp="$(curl -sS --max-time 180 -X POST "$endpoint" \
  -H "Authorization: Bearer $ASDD_RUNTIME_TOKEN" \
  -H "Content-Type: application/json" \
  -d "$payload" || true)"

content="$(printf '%s' "$resp" | jq -r '.choices[0].message.content // empty' 2>/dev/null || true)"

# Strip markdown fences if the model wrapped the JSON.
printf '%s' "$content" | sed -e 's/^```json[[:space:]]*//' -e 's/^```[[:space:]]*//' -e 's/[[:space:]]*```$//'
