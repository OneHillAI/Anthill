#!/usr/bin/env bash
# ASDD operate - test agent runner (post-merge regression run).
#
# Runs the test-runner agent (Goose, on the roster's test_runner model) against a MERGED change and writes
# its pass/fail report to $OUT. It runs on TRUSTED input (a human already merged the change), so unlike the
# reviewer it is safe to give the agent a shell. It still opens no PR and writes no code: the workflow posts
# its output for a human. It is NEVER run on an open PR: executing a stranger's code with the model key in
# reach needs a sandbox this repo does not have, and cli/operate-guard.py enforces that rule below.
#
# Usage: test.sh <change_ref> <out_file>
# Env: ASDD_RUNTIME_TOKEN + ASDD_MODEL_URL (the shared pair), optionally the per-role overrides
# ASDD_RUNTIME_TOKEN__TEST_RUNNER / ASDD_MODEL_URL__TEST_RUNNER. The MODEL comes from the roster
# (models.test_runner), resolved via cli/resolve-model.sh. Keys stay in the environment.
#
# Adapted from the ASDD kit's template (cli/templates/operate/test.sh). Two things differ, both bugs in the
# template as shipped: it passed the recipe `change_ref` where the recipe's parameter is `pr`, and it
# looked for a "## Test result" heading the recipe never prints (the recipe writes a structured result
# file). The report here is built from that result file, and a run that did not complete says so.
set -euo pipefail

CHANGE_REF="${1:?test: change_ref required}"
OUT="${2:?test: out file required}"
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"
RECIPE="recipes/test-runner.yaml"
RESULT=".asdd-work/operate-result.json"
LEDGER="${ASDD_ACTIVITY_LOG:-.asdd-work/audit.jsonl}"

# Every exit path leaves exactly one trail record. A real run is recorded by cli/operate-run.py; the paths
# that never reach it (guard refusal, not wired, no Goose) are recorded here, so the agent is never invisible.
RECORDED=0
_trail() {
  [ "$RECORDED" = 1 ] && return 0
  local v=completed
  [ -f "$OUT" ] && grep -qi 'no test agent ran' "$OUT" 2>/dev/null && v=dry-run
  python3 cli/audit.py append --ledger "$LEDGER" --role test-runner --action test.run \
    --authorizing-decision "post-merge test agent (trusted)" --verdict "$v" \
    --reasoning "test agent did not run on ${CHANGE_REF}: ${v}" >/dev/null 2>&1 || true
}
trap _trail EXIT

# The tester has a shell, so it must only ever run on trusted input (see cli/operate-guard.py).
python3 cli/operate-guard.py "$RECIPE" --input trusted \
  || { echo "test: operate-guard refused this run" >&2; exit 1; }

not_run() {
  {
    echo "## Test agent - NO TEST AGENT RAN"
    echo
    echo "$1"
    echo
    echo "This is not a test result. A human should confirm the suite on \`${CHANGE_REF}\` (CI runs it on every"
    echo "push to main). To connect the agent: the roster's \`test_runner\` model, plus the endpoint and key"
    echo "(\`ASDD_MODEL_URL\` variable and \`ASDD_RUNTIME_TOKEN\` secret, or their \`__TEST_RUNNER\` variants)."
  } > "$OUT"
}

RESOLVE="cli/resolve-model.sh"
MODEL="$("$RESOLVE" test_runner .asdd.yml 2>/dev/null || true)"
MODEL_URL="$("$RESOLVE" test_runner .asdd.yml --url 2>/dev/null || true)"
# The resolver returns the NAME of the variable holding the key, never the key, so the secret never reaches
# a log or a command line. Dereference it here.
TOKEN_VAR="$("$RESOLVE" test_runner .asdd.yml --token-var 2>/dev/null || true)"
TOKEN="${!TOKEN_VAR:-}"

if [ -z "$TOKEN" ] || [ -z "$MODEL_URL" ] || [ -z "$MODEL" ]; then
  not_run "The model runtime is not wired (need an endpoint and key from ASDD_MODEL_URL / ASDD_RUNTIME_TOKEN or their __TEST_RUNNER variants, and a model from models.test_runner)."
  exit 0
fi
if ! command -v goose >/dev/null 2>&1; then
  not_run "Goose is not installed on the runner."
  exit 0
fi

# Point Goose's built-in openai provider at the endpoint this role resolved to. Goose wants the host and the
# FULL request path apart, so accept either spelling of the URL (a bare https://provider/v1 base, a trailing
# slash, or the full .../chat/completions URL), as the review adapter does.
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
rc=0
log="$(python3 cli/operate-run.py --role test-runner --recipe "$RECIPE" --provider openai --model "$MODEL" \
  --instructed-by asdd-test --param pr="$CHANGE_REF" 2>&1)" || rc=$?
RECORDED=1   # operate-run wrote the record

# Build the report from the agent's structured result. The result is model output, so only known fields are
# read, each is length-capped, and nothing in it is executed.
if [ -f "$RESULT" ]; then
  python3 - "$RESULT" "$CHANGE_REF" "$MODEL" > "$OUT" <<'PY'
import json, sys

path, ref, model = sys.argv[1:4]


def clip(v, n=300):
    return " ".join(str(v).split())[:n]


try:
    r = json.load(open(path, encoding="utf-8"))
    r = r if isinstance(r, dict) else {}
except (OSError, ValueError):
    r = {}
p = r.get("payload") if isinstance(r.get("payload"), dict) else {}
verdict = str(r.get("verdict", "")).strip().lower()
head = {"pass": "PASS", "fail": "FAIL"}.get(verdict, "NO VERDICT (the agent wrote an unusable result)")
print(f"## Test agent - result for `{ref}`\n")
print(f"**{head}** (agent-reported; run on `{model}`, the test_runner role)\n")
if p.get("tested"):
    print(f"- Tested: {clip(p['tested'])}")
if "passed" in p or "failed" in p:
    print(f"- Passed: {clip(p.get('passed', '?'), 20)} / Failed: {clip(p.get('failed', '?'), 20)}")
cases = p.get("failing") if isinstance(p.get("failing"), list) else []
if cases:
    print("- Failing cases: " + ", ".join(f"`{clip(c, 120)}`" for c in cases[:20]))
if r.get("reasoning"):
    print(f"- Reasoning: {clip(r['reasoning'])}")
PY
else
  detail="$(printf '%s\n' "$log" | tail -n 8)"
  detail="${detail//"$TOKEN"/[redacted]}"
  {
    echo "## Test agent - did not run to completion"
    echo
    echo "The test agent was started (goose exit code ${rc}) but left no result, so there is no pass or fail"
    echo "to report. A human should confirm the suite on \`${CHANGE_REF}\`."
    echo
    if [ -n "$detail" ]; then
      echo "Last lines of the run (key redacted):"
      echo
      echo '```'
      printf '%s\n' "$detail"
      echo '```'
    fi
  } > "$OUT"
fi
exit 0
