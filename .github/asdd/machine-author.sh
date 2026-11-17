#!/usr/bin/env bash
# ASDD - machine authors. DETERMINISTIC, read-only, no model, no network.
#
# Prints "true" iff a PR's AUTHOR login is one of `machine_authors` in the (trusted, base) .asdd.yml: an
# automation (for example Dependabot) whose own login is its authorship disclosure, because the tool writes
# its own PR body and labels and cannot be taught the PR template. Intake uses this to waive the disclosure
# box and the lane label for such a PR, and only when every commit in it is also authored by that machine
# (intake-check.sh checks that). Empty or missing list => never. Only a GitHub bot account (a login ending in
# `[bot]`) can qualify, even if it is listed: no person has such a login, so listing one does nothing.
#
# Usage: machine-author.sh <author-login> <trusted-config>
#   trusted-config: a config path the CALLER guarantees is from the trusted base (never a PR-controlled
#   working tree). The intake workflow passes `git show <base-sha>:.asdd.yml`. The list is read ONLY from
#   this file, so a PR cannot add itself. Same shape and trust rule as owner-override.sh.
set -euo pipefail

AUTHOR="$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')"
CONFIG="${2:?usage: machine-author.sh <author-login> <trusted-config>}"

[ -n "$AUTHOR" ] || { echo "false"; exit 0; }
case "$AUTHOR" in *"[bot]") ;; *) echo "false"; exit 0 ;; esac
[ -f "$CONFIG" ] || { echo "false"; exit 0; }

# The `- item` lines under `machine_authors:` up to the next top-level key; strip the list marker, quotes and
# spaces (never internal characters: logins contain hyphens and the `[bot]` suffix) and match the author.
if awk -v a="$AUTHOR" '
    /^machine_authors:/ {f=1; next}
    f && /^[^[:space:]#-]/ {f=0}
    f {
      line=$0
      sub(/#.*$/, "", line)
      sub(/^[[:space:]]*-[[:space:]]*/, "", line)
      gsub(/["'"'"' ]/, "", line)
      if (line != "" && tolower(line) == a) { print "hit"; exit }
    }
  ' "$CONFIG" 2>/dev/null | grep -q hit; then
  echo "true"
else
  echo "false"
fi
