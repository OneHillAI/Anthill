#!/usr/bin/env bash
# ASDD operate - post-merge test run.
#
# Runs the project's test suite on a MERGED change and writes a pass/fail report to $OUT. Running pytest and
# counting what passed is deterministic, so no model is involved: the suite is the same command CI runs, the
# verdict is pytest's own exit code, and the counts and failing cases come from its JUnit XML (see
# test-junit.py). A model in this path could only add ways to fail (endpoint down, malformed JSON) to a
# suite that is green. Nothing here reads a model key or secret, so running the merged code with a shell is
# safe by construction; the workflow still only ever runs it on a commit that is on main.
#
# Usage: test.sh <change_ref> <out_file>
# Leaves .asdd-work/operate-result.json (the structured result the `asdd/test` commit status reads) and one
# audit record. Always exits 0 once it has run, whatever the verdict: the workflow posts the report and
# records the status either way.
set -euo pipefail

CHANGE_REF="${1:?test: change_ref required}"
OUT="${2:?test: out file required}"
REPO_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$REPO_ROOT"
JUNIT=".asdd-work/junit.xml"
RESULT=".asdd-work/operate-result.json"
LEDGER="${ASDD_ACTIVITY_LOG:-.asdd-work/audit.jsonl}"
OPERATE=".github/asdd/operate"

mkdir -p .asdd-work
rm -f "$JUNIT" "$RESULT"

# Same flags as ci.yml (two workers: more OOM-killed a hosted runner there).
rc=0
python3 -m pytest -n 2 --dist loadscope -q -p no:cacheprovider --junitxml="$JUNIT" || rc=$?

python3 "$OPERATE/test-junit.py" "$JUNIT" "$rc" "$CHANGE_REF" > "$RESULT" || echo '{}' > "$RESULT"
python3 "$OPERATE/test-report.py" "$RESULT" "$CHANGE_REF" pytest > "$OUT"
verdict="$(python3 "$OPERATE/test-report.py" --verdict "$RESULT")"

python3 cli/audit.py append --ledger "$LEDGER" --role test-runner --action test.run \
  --authorizing-decision "post-merge deterministic pytest run (trusted)" --verdict "$verdict" \
  --reasoning "pytest exit code ${rc} on ${CHANGE_REF}: ${verdict}" >/dev/null 2>&1 || true
exit 0
