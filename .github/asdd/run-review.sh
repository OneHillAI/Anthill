#!/usr/bin/env bash
# ASDD - runtime adapter entrypoint (the pluggable seam).
#
# Reads a work directory of UNTRUSTED PR data (title.txt, body.md, author.txt, changes.diff, meta.env)
# and writes a structured review JSON. Untrusted content is handed to the runtime as FILES; it is never
# interpolated into a prompt string here, and this script never executes runtime output as a command
# (the runtime returns JSON; we validate and pass it through). See the ASDD standards/security.md.
#
# Runtime selection: .asdd.yml -> `runtime:`. If .github/asdd/runtime/<name>.sh exists and
# a runtime credential is set, it is invoked to produce the review. Otherwise this falls back to a
# clearly labelled DRY-RUN so the pipeline can be watched before a model is wired.
#
# Usage: run-review.sh <workdir> <out.json>
set -euo pipefail

WORKDIR="${1:?usage: run-review.sh <workdir> <out.json>}"
OUT="${2:?usage: run-review.sh <workdir> <out.json>}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"   # .github/asdd -> repo root

# Read a single top-level scalar from .asdd.yml without a YAML dependency.
yaml_scalar() {
  local key="$1" file="$ROOT/.asdd.yml"
  [ -f "$file" ] || { echo ""; return; }
  sed -n "s/^${key}:[[:space:]]*//p" "$file" | head -n1 | sed 's/[[:space:]]*#.*$//; s/^["'\'']//; s/["'\'']$//'
}

# shellcheck disable=SC1090
set -a; . "$WORKDIR/meta.env"; set +a   # pr_number, base_sha, head_sha

RUNTIME="$(yaml_scalar runtime)"; RUNTIME="${RUNTIME:-generic}"
ADAPTER="$ROOT/.github/asdd/runtime/${RUNTIME}.sh"

if [ -n "${ASDD_RUNTIME_TOKEN:-}" ] && [ -x "$ADAPTER" ]; then
  echo "asdd: running '$RUNTIME' runtime adapter"
  ASDD_WORKDIR="$WORKDIR" ASDD_OUT="$OUT" ASDD_ROOT="$ROOT" bash "$ADAPTER"
else
  reason="dry-run"
  [ -z "${ASDD_RUNTIME_TOKEN:-}" ] && reason="no ASDD_RUNTIME_TOKEN set"
  [ -x "$ADAPTER" ] || reason="$reason; no adapter at .github/asdd/runtime/${RUNTIME}.sh"
  echo "asdd: DRY-RUN ($reason)"
  # grep -c prints the count (0 on no match) and exits 1 when 0; `|| true` keeps that "0" without
  # `|| echo 0` appending a SECOND 0 (which produced malformed review JSON when a side had 0 lines).
  added="$(grep -c '^+' "$WORKDIR/changes.diff" 2>/dev/null || true)"; added="${added:-0}"
  removed="$(grep -c '^-' "$WORKDIR/changes.diff" 2>/dev/null || true)"; removed="${removed:-0}"
  cat > "$OUT" <<JSON
{
  "schema": "asdd/review/v0.1",
  "pr_number": ${pr_number},
  "head_sha": "${head_sha}",
  "mode": "dry-run",
  "recommendation": "comment",
  "summary": "Dry-run: the ASDD review pipeline ran but no agent runtime is wired ($reason). Diff stats only.",
  "lenses": [
    {"lens": "code",     "verdict": "skipped", "findings": []},
    {"lens": "security", "verdict": "skipped", "findings": []},
    {"lens": "spec",     "verdict": "skipped", "findings": []},
    {"lens": "quality",  "verdict": "skipped", "findings": []}
  ],
  "stats": {"diff_added_lines": ${added}, "diff_removed_lines": ${removed}}
}
JSON
fi

[ -s "$OUT" ] || { echo "asdd: ERROR runtime produced no review at $OUT" >&2; exit 1; }

# Security lens, layers 1+2: deterministic rules + SAST over the diff, merged into the `security` lens
# of $OUT. Runs regardless of whether a model runtime is wired (so the gate works even in dry-run), and
# is fail-safe (it never corrupts $OUT or breaks the pipeline). A `block` finding it adds is what
# set-status.sh turns into a failing asdd/review status. Reviewed code is data, never executed.
if command -v python3 >/dev/null 2>&1; then
  python3 "$ROOT/.github/asdd/security_scan.py" \
    --review "$OUT" --workdir "$WORKDIR" --head-ref refs/anthill-pr-head || \
    echo "asdd: security scan returned non-zero (ignored; gate degrades to model lens only)" >&2
else
  echo "asdd: python3 not available; skipping the deterministic/SAST security layers" >&2
fi

echo "asdd: review written to $OUT"
