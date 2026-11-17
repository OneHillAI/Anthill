#!/usr/bin/env bash
# ASDD - collect the specs a PR links, for the review's spec lens. DETERMINISTIC, read-only, no model.
#
# The spec lens checks a change against the spec it claims to implement. A spec the PR ADDS is in the diff and
# the lens sees it; a spec it only LINKS (an existing docs/specs/*.md named in the description) was invisible,
# so the lens called it unverifiable. This writes the linked specs, as they are on the base branch, to a file
# that generic.sh puts inside the fenced untrusted data block.
#
# Usage: linked-specs.sh <body.md> <out.md>      run from the root of the TRUSTED base checkout
# The body is untrusted: a reference is used only as a path, and only if it is a plain direct child of
# docs/specs/ (no `..`, no nesting) that EXISTS in the base checkout, so it can name nothing else. The file
# contents come from the base checkout, not the PR. Each spec is capped at 30000 bytes and the total at 60000.
set -euo pipefail
BODY="${1:?usage: linked-specs.sh <body.md> <out.md>}"
OUT="${2:?usage: linked-specs.sh <body.md> <out.md>}"
: > "$OUT"
[ -f "$BODY" ] || exit 0
per=30000; total=60000; used=0
while IFS= read -r ref; do
  [ -n "$ref" ] || continue
  case "$ref" in *..*) continue ;; esac
  [[ "$ref" =~ ^docs/specs/[A-Za-z0-9._-]+\.md$ ]] || continue
  [ -f "$ref" ] && [ ! -L "$ref" ] || continue
  size="$(head -c "$per" "$ref" | wc -c | tr -d ' ')"
  [ $((used + size)) -le "$total" ] || break
  { echo "### $ref"; echo '```markdown'; head -c "$per" "$ref"; echo; echo '```'; echo; } >> "$OUT"
  used=$((used + size))
done < <(grep -oiE 'docs/specs/[A-Za-z0-9._/-]+\.md' "$BODY" 2>/dev/null | sort -u)
exit 0
