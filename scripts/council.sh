#!/usr/bin/env bash
# Run the developer council on a change, from a Claude Code (or any) session.
#
# The council (2 to 5 diverse models: Opus and Gemini propose, GPT-5.6 leads and synthesises) implements
# an OpenSpec change against its acceptance criteria, the Berget test agents verify it on a model distinct
# from the council, and it returns ONE synthesised draft plus a full transcript. It is opt-in: reach for
# it when a change benefits from multi-model diversity, not on trivial edits (each run is real runware
# cost, several frontier calls).
#
# Usage:
#   1. Write the change bridge:  openspec/changes/<id>/proposal.md  and  tasks.md
#      tasks.md holds the acceptance criteria; reference the docs/specs/<x>.md it implements.
#   2. Run:  scripts/council.sh <id>
#   3. Review the draft it prints. Apply and refine it, then open a governed PR so it flows through
#      intake, the reviewer, and the tests like any change. Route the transcript to the private store.
#
# Keys are local and private, never committed:
#   ~/anthill-keys/runware.env  the council (runware, per-member ASDD_..._COUNCIL_i)
#   ~/anthill-keys/berget.env   the Berget verify models (shared ASDD_MODEL_URL / ASDD_RUNTIME_TOKEN)
# If a model is not reachable, run  python3 cli/connect-check.py  first; nothing is trustworthy until it
# reports every agent LIVE.
set -euo pipefail

CHANGE="${1:-}"
[ -n "$CHANGE" ] || { sed -n '11,18p' "$0"; exit 2; }
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

[ -d "$ROOT/openspec/changes/$CHANGE" ] || {
  echo "error: no bridge at openspec/changes/$CHANGE" >&2
  echo "create proposal.md and tasks.md there first (tasks.md = the acceptance criteria)." >&2
  exit 2
}

set -a
[ -f "$HOME/anthill-keys/runware.env" ] && . "$HOME/anthill-keys/runware.env"
[ -f "$HOME/anthill-keys/berget.env" ]  && . "$HOME/anthill-keys/berget.env"
set +a

OUT="${COUNCIL_OUT:-/tmp/council-${CHANGE}-draft.md}"
TRANSCRIPT="${COUNCIL_TRANSCRIPT:-/tmp/council-${CHANGE}-transcript.json}"

echo "running the developer council on '${CHANGE}' (a few minutes; frontier calls across 3 models)..."
python3 "$ROOT/cli/dev-council.py" --change "$CHANGE" --root "$ROOT" --out "$OUT" --transcript "$TRANSCRIPT"

echo
echo "draft:      $OUT"
echo "transcript: $TRANSCRIPT"
echo "  ^ full-fidelity training fuel. Route it to the private operate repo (anthill-run/training/),"
echo "    never a public or governed sink. Then review the draft and open a governed PR from it."
