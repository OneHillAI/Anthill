#!/usr/bin/env bash
# ASDD - set the merge-gating commit status for the review. Turns the security/quality verdict
# into a MECHANICAL gate: make `asdd/review` a required status check in branch protection and a
# security block or a request-changes recommendation from the code, security or spec lens blocks merge for
# everyone (a design-only objection from the adversarial lens is advice and stays green, see below). Runs only in the
# write-scoped publish job. Never shell-execs model output. (Intake is its own separate check.)
#
# Usage: set-status.sh <review.json>
# Env: GH_TOKEN, REPO.
set -euo pipefail
REVIEW="${1:?usage: set-status.sh <review.json>}"
: "${GH_TOKEN:?}"; : "${REPO:?}"

SHA="$(jq -r '.head_sha' "$REVIEW")"
[[ "$SHA" =~ ^[0-9a-f]{7,40}$ ]] || { echo "set-status: invalid head_sha" >&2; exit 1; }

sec_block="$(jq -r '[(.lenses // [])[] | select(.lens=="security") | (.findings // [])[] | select(.severity=="block")] | length' "$REVIEW")"
rec="$(jq -r '.recommendation // "comment"' "$REVIEW")"

# "Design-only": the adversarial QUALITY lens recommends changes while the code, security and spec lenses are all ok
# (verdict ok or absent, no warn and no block finding) and no lens has a block finding. The independent skeptic
# pass raises a design objection on almost every PR (more code, more abstraction, more edge cases), and the merge
# of the two model calls lets it fail the whole review. That objection is advice for a human, not a defect: it
# is shown in the comment and named in the status description, and the state stays success. Anything else a
# request-changes can come from (a security block, a concern in the code or spec lens, a block finding, or a review
# with no lens detail at all) keeps its failure unchanged. Only a LIVE review can be design-only: a review that
# did not run live is never softened, and a lens that cannot be read keeps the failure (the `|| echo false`).
design_only="$(jq -r '
  (.lenses // []) as $l
  | ([$l[] | select(.lens=="quality")] | length > 0) as $has_quality
  | ([$l[] | select(.lens!="quality")
       | select((.verdict // "ok") != "ok"
                or ([(.findings // [])[] | select(.severity=="warn" or .severity=="block")] | length > 0))] | length == 0) as $others_ok
  | ([$l[] | (.findings // [])[] | select(.severity=="block")] | length == 0) as $no_block
  | ($has_quality and $others_ok and $no_block and ((.mode // "live") == "live"))' "$REVIEW" 2>/dev/null || echo false)"

state="success"; desc="Advisory review complete; a human approves and merges."
if [ "${sec_block:-0}" -gt 0 ]; then
  state="failure"; desc="Security review raised a blocking finding; needs a human resolution."
elif [ "$rec" = "request-changes" ]; then
  if [ "$design_only" = "true" ]; then
    desc="Advisory: only the adversarial design pass recommends changes; code, security and spec are ok. A human decides."
  else
    state="failure"; desc="Review recommends changes."
  fi
fi

# A review that did not happen must not read like one. The runtime records its mode in the artifact:
# "dry-run" (no model runtime wired), "adapter-template" (adapter chosen, no endpoint) or "degraded" (the
# model answered with unusable output); only a "live" review earns "Advisory review complete". The state
# stays success on purpose: an unwired runtime must never BLOCK a merge (fork and Dependabot PRs get no
# secrets, so they dry-run by design), but the description now says plainly that no AI review ran. Until
# this, every dry-run PR showed the same green line as a real review and nobody could tell the
# difference. A failure (a security block, request-changes) keeps its failure either way.
mode="$(jq -r '.mode // "live"' "$REVIEW")"
if [ "$state" = "success" ] && [ "$mode" != "live" ]; then
  case "$mode" in
    dry-run)          why="the model runtime is not connected" ;;
    adapter-template) why="no model endpoint is wired" ;;
    degraded)         why="the model returned unusable output" ;;
    *)                why="mode: ${mode}" ;;
  esac
  desc="NO AI REVIEW RAN (${why}). A human must review this PR."
fi

# Owner override: an owner's own PR carrying the `owner-override` label does not have its merge blocked
# by the review. The review still posts (post-review.sh); this only turns the gating status green, with a
# description naming the override so the bypass is on the record. Author + labels are read live; the
# owners list is the trusted base .asdd.yml (via the shared script). `|| echo false` keeps it inert
# before the script exists in base (self-heals after merge).
if [ "$state" = "failure" ]; then
  PR="$(jq -r '.pr_number' "$REVIEW")"
  if [[ "$PR" =~ ^[0-9]+$ ]]; then
    author="$(gh pr view "$PR" --repo "$REPO" --json author -q '.author.login' 2>/dev/null || echo '')"
    lf="$(mktemp)"; gh pr view "$PR" --repo "$REPO" --json labels -q '.labels[].name' > "$lf" 2>/dev/null || true
    # The publish job checks out the base repo (never PR head), so ./.asdd.yml here is the trusted base.
    ovr="$(bash "$(dirname "$0")/owner-override.sh" "$author" "$lf" .asdd.yml 2>/dev/null || echo false)"
    rm -f "$lf"
    if [ "$ovr" = "true" ]; then
      state="success"; desc="Owner override (${author}): merged past the advisory review on the owner's judgement."
    fi
  fi
fi
desc="${desc:0:140}"   # GitHub truncates status descriptions at 140 chars.

echo "set-status: $REPO@${SHA:0:12} context=asdd/review state=$state"
gh api "repos/${REPO}/statuses/${SHA}" \
  -f state="$state" -f context="asdd/review" -f description="$desc" >/dev/null
echo "set-status: done"
