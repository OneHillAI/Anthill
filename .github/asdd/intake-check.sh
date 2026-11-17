#!/usr/bin/env bash
# ASDD - intake gate. DETERMINISTIC, read-only, no model.
#
# A PR that does not (1) disclose authorship, (2) sign off every commit (DCO), and (3) carry exactly one
# lane tag fails intake (a machine author such as Dependabot is waived (1) and (3), see step 0). A commit by a
# configured agent identity must carry the Agent: trailer (step 6). This runs in a read-only job: it only pattern-matches untrusted text as data
# (no prompt, no shell-exec), so it adds no write-scoped untrusted-input step. It filters non-conforming
# PRs without a maintainer having to read them.
#
# Usage: intake-check.sh <workdir> <out.json>
#   workdir holds: body.md (PR body, untrusted), commits.txt (NUL-delimited commit messages),
#                  labels.txt (one label per line), meta.env (pr_number, head_sha),
#                  optional authors.txt + author-emails.txt (commit author name and email, one line per commit, in
#                  commits.txt order), author.txt (the PR author's login), base-asdd.yml (the BASE commit's config)
set -euo pipefail
WORKDIR="${1:?usage: intake-check.sh <workdir> <out.json>}"
OUT="${2:?usage: intake-check.sh <workdir> <out.json>}"
# shellcheck disable=SC1090
set -a; . "$WORKDIR/meta.env"; set +a

problems=()

# 0) Machine author: automation such as Dependabot writes its own PR body and labels and cannot tick the
#    template or carry a lane tag. When the PR author is in `machine_authors` of the trusted base .asdd.yml
#    (the workflow sets machine_author=true; a PR cannot add itself) AND every commit in the PR is authored by
#    that same login, the authorship disclosure and the lane tag are waived: the identity is the disclosure,
#    and the PR counts as a `chore`. A human commit pushed onto the branch ends the waiver, so the PR then
#    needs a disclosure like any human PR. DCO, anti-flood and everything after intake still apply.
machine_ok=false
if [ "${machine_author:-false}" = "true" ] && [ -s "$WORKDIR/authors.txt" ] && [ -s "$WORKDIR/author.txt" ]; then
  pr_author="$(head -n1 "$WORKDIR/author.txt" | tr -d '\r' | tr '[:upper:]' '[:lower:]')"
  machine_ok=true
  while IFS= read -r name; do
    name="$(printf '%s' "${name%$'\r'}" | tr '[:upper:]' '[:lower:]')"
    [ -n "$name" ] || continue
    [ "$name" = "$pr_author" ] || { machine_ok=false; break; }
  done < "$WORKDIR/authors.txt"
fi

# 1) Disclosure: the PR body must tick the human or agent/AI box.
disclosed=false
if [ "$machine_ok" = "true" ]; then
  disclosed=true
elif grep -qiE '^[[:space:]]*-?[[:space:]]*\[[xX]\].*(human|agent|\bAI\b)' "$WORKDIR/body.md" 2>/dev/null; then
  disclosed=true
else
  problems+=("No authorship disclosure: tick the human or AI-agent box in the PR template (ASDD 1).")
fi

# 2) DCO: every commit in the range must be Signed-off-by.
total=0; signed=0
if [ -f "$WORKDIR/commits.txt" ]; then
  while IFS= read -r -d '' msg; do
    [ -n "${msg//[[:space:]]/}" ] || continue
    total=$((total+1))
    grep -qiE '^[[:space:]]*Signed-off-by:[[:space:]]+.+<.+@.+>' <<<"$msg" && signed=$((signed+1))
  done < "$WORKDIR/commits.txt"
fi
signed_off=false
if [ "$total" -gt 0 ] && [ "$signed" -eq "$total" ]; then
  signed_off=true
else
  problems+=("Missing DCO sign-off on $((total-signed))/$total commit(s): use 'git commit -s' (ASDD 6.1).")
fi

# 3) Lane tag: exactly one lane label must be present.
lane_count=0
if [ -f "$WORKDIR/labels.txt" ]; then
  lane_count=$(grep -cE '^(pillar:(privacy|knowledge|model|platform|feature)|chore)$' "$WORKDIR/labels.txt" || true)
fi
laned=false
if [ "$lane_count" -eq 1 ] || { [ "$machine_ok" = "true" ] && [ "$lane_count" -eq 0 ]; }; then
  laned=true
else
  problems+=("Lane tag: add exactly one of pillar:privacy|knowledge|model|platform|feature or chore (found $lane_count). Anthill charters, Section 2.")
fi

# 4) Anti-flood: an author may not have too many PRs open at once. The workflow supplies the count and
#    the cap (network, read-only) via meta.env; this stays a pure numeric compare. Absent count or a
#    non-positive cap => skipped, so older callers and local runs are unaffected (${var:-} under set -u).
open_prs="${open_pr_count:-}"
cap="${max_open_prs:-0}"
flood_ok=true
if [ -n "$open_prs" ] && [ "$cap" -gt 0 ] && [ "$open_prs" -gt "$cap" ]; then
  flood_ok=false
  problems+=("Too many open PRs from this author ($open_prs, cap $cap). Land or close some before opening more (anti-flood, ASDD 3).")
fi

# 5) Spec-driven by default: a non-trivial PR (single lane, not `chore`) must be based on a spec - it
#    references an existing docs/specs/*.md (a path in the body or a `Spec:` trailer) OR adds/edits one
#    in its diff. require_spec + the changed-file list arrive from the workflow; this stays a pure match.
#    Disabled or a chore lane => skipped, so trivial fixes and older callers are unaffected.
spec_ok=true
is_chore=false
grep -qxE 'chore' "$WORKDIR/labels.txt" 2>/dev/null && is_chore=true
[ "$machine_ok" = "true" ] && [ "$lane_count" -eq 0 ] && is_chore=true
if [ "${require_spec:-false}" = "true" ] && [ "$laned" = "true" ] && [ "$is_chore" = "false" ]; then
  has_spec=false
  # (a) a docs/specs/*.md ADDED, MODIFIED or RENAMED-to in THIS PR is a spec by definition. changed.txt
  #     is `git diff --name-status`, so a DELETED spec (status D) does not count (its path would otherwise
  #     match and let a spec-deleting PR pass). $NF is the (new) path; the status column is $1.
  if [ -f "$WORKDIR/changed.txt" ] && awk -F'\t' \
      '$1 ~ /^[AMR]/ && $NF ~ /(^|\/)docs\/specs\/.+\.md$/ {f=1} END{exit f?0:1}' \
      "$WORKDIR/changed.txt"; then
    has_spec=true
  fi
  # (b) a REFERENCED existing spec: the body must name a docs/specs/*.md that actually EXISTS in the
  #     tree (base checkout). A fabricated or misspelled path does not satisfy the gate. The reference is
  #     constrained to a DIRECT child of docs/specs (no `..`, no nested path), so a traversal reference
  #     like `docs/specs/../../README.md` cannot resolve an unrelated existing file into a "spec".
  if [ "$has_spec" = "false" ]; then
    while IFS= read -r ref; do
      [ -n "$ref" ] || continue
      case "$ref" in *..*) continue ;; esac
      [[ "$ref" =~ ^docs/specs/[A-Za-z0-9._-]+\.md$ ]] || continue
      [ -f "$ref" ] && { has_spec=true; break; }
    done < <(grep -oiE 'docs/specs/[A-Za-z0-9._/-]+\.md' "$WORKDIR/body.md" 2>/dev/null)
  fi
  if [ "$has_spec" = "false" ]; then
    spec_ok=false
    problems+=("Not based on a spec (ASDD is spec-driven). Either reference an existing spec (a docs/specs/... path in the description or a 'Spec:' line) OR add docs/specs/<name>.md in this PR - problem, requirements, acceptance criteria - that this change implements. The review agent checks the change against it.")
  fi
fi

# 6) Agent trailer (STANDARD 1.2, AGENTS.md): an agent-authored commit must say so. Two rules, both from the
#    trusted base config and the PR's own data: (a) every non-merge commit whose author name or email contains
#    an identity in the base .asdd.yml `agents` list carries `Agent: <name> (automated, instructed-by: <human>)`;
#    (b) a PR that ticks the AI-agent box has that trailer on at least one commit. A human commit in a mixed PR
#    is not asked for one. Inert without the author files or an agents list, so older callers are unaffected.
trailer_re='^[[:space:]]*Agent:[[:space:]]+.+\(automated,[[:space:]]*instructed-by:[[:space:]]*[^)]+\)'
agent_trailer_ok=true
ai_box=false
grep -qiE '^[[:space:]]*-?[[:space:]]*\[[xX]\].*(\bAI\b|\bagents?\b)' "$WORKDIR/body.md" 2>/dev/null && ai_box=true
agents_list=""
[ -f "$WORKDIR/base-asdd.yml" ] && agents_list="$(bash "$(dirname "$0")/config-list.sh" agents "$WORKDIR/base-asdd.yml" | tr '[:upper:]' '[:lower:]')"
n_commits=0; agent_commits=0; agent_unmarked=0; any_trailer=false
if [ -f "$WORKDIR/commits.txt" ]; then
  have_ids=false
  [ -f "$WORKDIR/authors.txt" ] && [ -f "$WORKDIR/author-emails.txt" ] && have_ids=true
  while IFS= read -r -d '' msg; do
    n_commits=$((n_commits+1))   # counted before any skip, so line N of the author files is commit N
    has_trailer=false
    grep -qE "$trailer_re" <<<"$msg" && { has_trailer=true; any_trailer=true; }
    if [ "$have_ids" = "true" ] && [ -n "$agents_list" ]; then
      who="$(sed -n "${n_commits}p" "$WORKDIR/authors.txt") $(sed -n "${n_commits}p" "$WORKDIR/author-emails.txt")"
      who="$(printf '%s' "$who" | tr '[:upper:]' '[:lower:]')"
      while IFS= read -r a; do
        [ -n "$a" ] || continue
        case "$who" in *"$a"*) agent_commits=$((agent_commits+1)); [ "$has_trailer" = "true" ] || agent_unmarked=$((agent_unmarked+1)); break ;; esac
      done <<<"$agents_list"
    fi
  done < "$WORKDIR/commits.txt"
fi
if [ "$agent_unmarked" -gt 0 ]; then
  agent_trailer_ok=false
  problems+=("Agent trailer: $agent_unmarked of $agent_commits commit(s) by a configured agent identity lack 'Agent: <name> (automated, instructed-by: <human>)' (STANDARD 1.2, AGENTS.md).")
fi
if [ "$ai_box" = "true" ] && [ "$any_trailer" = "false" ] && [ "$n_commits" -gt 0 ]; then
  agent_trailer_ok=false
  problems+=("The AI-agent box is ticked but no commit carries 'Agent: <name> (automated, instructed-by: <human>)'. Add the trailer to the agent's commits (AGENTS.md).")
fi

# Owner override: an owner's own PR carrying the `owner-override` label passes intake even with unmet
# requirements. The problems are RETAINED in the output (advisory, on the record) - the override stops
# intake blocking, it does not hide what was skipped. `override` comes from the workflow (meta.env).
overridden="${override:-false}"
if [ "$overridden" = "true" ] && [ "${#problems[@]}" -gt 0 ]; then
  problems+=("Owner override in effect: the above are advisory only for this PR (owner-override label).")
fi

# Emit intake JSON.
if [ "${#problems[@]}" -gt 0 ]; then
  probs_json="$(printf '%s\n' "${problems[@]}" | jq -R . | jq -s 'map(select(length>0))')"
else
  probs_json='[]'
fi
jq -n \
  --argjson pr "${pr_number}" --arg head "${head_sha}" \
  --argjson disc "$disclosed" --argjson sign "$signed_off" --argjson lane "$laned" \
  --argjson flood "$flood_ok" --argjson spec "$spec_ok" --argjson ovr "$overridden" --argjson mach "$machine_ok" \
  --argjson trailer "$agent_trailer_ok" --argjson probs "$probs_json" \
  '{schema:"asdd/intake/v0.1", pr_number:$pr, head_sha:$head,
    disclosed:$disc, signed_off:$sign, laned:$lane, flood_ok:$flood, spec_ok:$spec, override:$ovr, machine_author:$mach, agent_trailer_ok:$trailer,
    passed:($ovr or ($disc and $sign and $lane and $flood and $spec and $trailer)),
    problems:$probs}' > "$OUT"

echo "intake: disclosed=$disclosed signed_off=$signed_off ($signed/$total signed) laned=$laned flood_ok=$flood_ok spec_ok=$spec_ok override=$overridden machine_author=$machine_ok agent_trailer_ok=$agent_trailer_ok"
[ -s "$OUT" ] || { echo "intake-check: no output" >&2; exit 1; }
