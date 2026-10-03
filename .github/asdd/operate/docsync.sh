#!/usr/bin/env bash
# ASDD operate - documentation agent runner (post-merge doc sync).
#
# Runs the documentation agent (Goose, on the roster's documentation model) against a MERGED change and
# writes a paste-ready proposal to $OUT. It runs on TRUSTED input (a human already merged the change), so
# unlike the reviewer it is safe to give the agent a shell; the recipe tells it to use that read-only. It
# opens no PR and edits no file: the workflow posts the report for a human to apply. (A bot that opens PRs
# on its own is not allowed here: no PR opens without the founder's yes.)
#
# The agent returns STRUCTURED data; docsync-render.py lays it out and checks it against the project's
# rules (real PR number and merge date, template fields, changelog rule, sentences really in the file,
# numbers really in the PR). So a misplaced heading, an invented count or a bad label is caught here, not
# left for a human to spot.
#
# Usage: docsync.sh <change_ref> <out_file>
# Env: ASDD_RUNTIME_TOKEN + ASDD_MODEL_URL (the shared pair), optionally the per-role overrides
# ASDD_RUNTIME_TOKEN__DOCUMENTATION / ASDD_MODEL_URL__DOCUMENTATION. The MODEL comes from the roster
# (models.documentation), resolved via cli/resolve-model.sh. The workflow supplies the merged PR's facts as
# .asdd-work/pr.json (number, title, merged_at) and .asdd-work/pr-body.md, from GitHub.
set -euo pipefail

CHANGE_REF="${1:?docsync: change_ref required}"
OUT="${2:?docsync: out file required}"
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"
RECIPE="recipes/documentation.yaml"
RESULT=".asdd-work/operate-result.json"
LEDGER="${ASDD_ACTIVITY_LOG:-.asdd-work/audit.jsonl}"

# Every exit path leaves exactly one trail record. A real run is recorded by cli/operate-run.py; the paths
# that never reach it (guard refusal, not wired, no Goose) are recorded here.
RECORDED=0
_trail() {
  [ "$RECORDED" = 1 ] && return 0
  local v=completed
  [ -f "$OUT" ] && grep -qi 'no documentation agent ran' "$OUT" 2>/dev/null && v=dry-run
  python3 cli/audit.py append --ledger "$LEDGER" --role documentation --action docs.run \
    --authorizing-decision "post-merge documentation agent (trusted)" --verdict "$v" \
    --reasoning "documentation agent did not run on ${CHANGE_REF}: ${v}" >/dev/null 2>&1 || true
}
trap _trail EXIT

# The agent has a shell, so it only ever runs on trusted input (see cli/operate-guard.py).
python3 cli/operate-guard.py "$RECIPE" --input trusted \
  || { echo "docsync: operate-guard refused this run" >&2; exit 1; }

not_run() {
  {
    echo "## Documentation agent - NO DOCUMENTATION AGENT RAN"
    echo
    echo "$1"
    echo
    echo "This is not a proposal. A human should sync the docs for \`${CHANGE_REF}\`: the impact log entry,"
    echo "a changelog fragment if product code changed, and any doc sentence that is now untrue."
  } > "$OUT"
}

failed() {
  local detail="${2:-}"
  detail="${detail//"$TOKEN"/[redacted]}"
  {
    echo "## Documentation agent - did not run to completion"
    echo
    echo "$1"
    echo
    if [ -n "$detail" ]; then
      echo "Last lines of the run (key redacted):"
      echo
      echo '```'
      printf '%s\n' "$detail"
      echo '```'
      echo
    fi
    echo "A human should sync the docs for \`${CHANGE_REF}\`. The \`ASDD runtime check\` workflow shows whether the"
    echo "documentation agent's model, endpoint and key are connected."
  } > "$OUT"
}

RESOLVE="cli/resolve-model.sh"
MODEL="$("$RESOLVE" documentation .asdd.yml 2>/dev/null || true)"
MODEL_URL="$("$RESOLVE" documentation .asdd.yml --url 2>/dev/null || true)"
# The resolver returns the NAME of the variable holding the key, never the key, so the secret never reaches
# a log or a command line. Dereference it here.
TOKEN_VAR="$("$RESOLVE" documentation .asdd.yml --token-var 2>/dev/null || true)"
TOKEN="${!TOKEN_VAR:-}"

if [ -z "$TOKEN" ] || [ -z "$MODEL_URL" ] || [ -z "$MODEL" ]; then
  not_run "The model runtime is not wired (need an endpoint and key from ASDD_MODEL_URL / ASDD_RUNTIME_TOKEN or their __DOCUMENTATION variants, and a model from models.documentation)."
  exit 0
fi
if ! command -v goose >/dev/null 2>&1; then
  not_run "Goose is not installed on the runner."
  exit 0
fi

# Goose wants the host and the FULL request path apart, so accept either spelling of the URL (a bare
# https://provider/v1 base, a trailing slash, or the full .../chat/completions URL).
endpoint="${MODEL_URL%/}"
case "$endpoint" in
  */chat/completions) ;;
  *) endpoint="$endpoint/chat/completions" ;;
esac
rest="${endpoint#*://}"
export OPENAI_API_KEY="$TOKEN"
export OPENAI_HOST="${endpoint%%://*}://${rest%%/*}"
export OPENAI_BASE_PATH="${rest#*/}"

mkdir -p .asdd-work
# The merged PR's facts come from GitHub (the workflow wrote them), never from the model.
PR_NUMBER="$(python3 -c "import json;print(json.load(open('.asdd-work/pr.json')).get('number',''))" 2>/dev/null || true)"
PR_TITLE="$(python3 -c "import json;print(json.load(open('.asdd-work/pr.json')).get('title',''))" 2>/dev/null || true)"
MERGED_AT="$(python3 -c "import json;print(str(json.load(open('.asdd-work/pr.json')).get('merged_at',''))[:10])" 2>/dev/null || true)"
[ -n "$PR_TITLE" ] || PR_TITLE="$(git log -1 --format=%s "$CHANGE_REF" 2>/dev/null || true)"
[ -n "$MERGED_AT" ] || MERGED_AT="$(git log -1 --format=%cs "$CHANGE_REF" 2>/dev/null || date -u +%F)"
[ -n "$PR_NUMBER" ] || PR_NUMBER="$(printf '%s' "$PR_TITLE" | sed -n 's/^Merge pull request #\([0-9]*\).*/\1/p')"
[ -n "$PR_NUMBER" ] || PR_NUMBER="unknown"
printf '%s' "$PR_TITLE" > .asdd-work/pr-title.txt
[ -f .asdd-work/pr-body.md ] || : > .asdd-work/pr-body.md

# What the agent is allowed to have read, for checking the numbers it writes, and the changed paths.
{ git diff --stat "$CHANGE_REF^1" "$CHANGE_REF" 2>/dev/null || git show --stat --format=%B "$CHANGE_REF" 2>/dev/null || true
  cat .asdd-work/pr-title.txt .asdd-work/pr-body.md; } > .asdd-work/corpus.txt
{ git diff --name-only "$CHANGE_REF^1" "$CHANGE_REF" 2>/dev/null || git show --name-only --format= "$CHANGE_REF" 2>/dev/null || true; } > .asdd-work/changed.txt

rc=0
log="$(python3 cli/operate-run.py --role documentation --recipe "$RECIPE" --provider openai --model "$MODEL" \
  --instructed-by asdd-docsync --param change_ref="$CHANGE_REF" --param instructed_by=asdd-docsync 2>&1)" || rc=$?
RECORDED=1   # operate-run wrote the record

if [ ! -f "$RESULT" ]; then
  failed "The documentation agent was started (goose exit code ${rc}) but left no result, so there is nothing to propose." "$(printf '%s\n' "$log" | tail -n 8)"
  exit 0
fi

rrc=0
python3 .github/asdd/operate/docsync-render.py --result "$RESULT" --ref "$CHANGE_REF" --pr "$PR_NUMBER" \
  --title "$PR_TITLE" --date "$MERGED_AT" --corpus .asdd-work/corpus.txt --changed .asdd-work/changed.txt \
  --impact-log docs/SYSTEM_IMPACT_LOG.md --model "$MODEL" --root . > "$OUT.tmp" 2> .asdd-work/render.err || rrc=$?
if [ "$rrc" -eq 0 ]; then
  mv "$OUT.tmp" "$OUT"
else
  rm -f "$OUT.tmp"
  failed "The documentation agent ran but its result had no usable impact-log entry (renderer exit ${rrc})." "$(tail -n 4 .asdd-work/render.err 2>/dev/null)"
fi
exit 0
