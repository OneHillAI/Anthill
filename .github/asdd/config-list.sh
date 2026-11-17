#!/usr/bin/env bash
# ASDD - read one top-level list from a TRUSTED config. DETERMINISTIC, read-only, no model, no network.
#
# Prints the `- item` values under a top-level key of a YAML config, one per line, with the list marker,
# quotes, spaces and comments removed (internal characters such as hyphens and `[bot]` are kept). Prints
# nothing if the file or the key is missing. Same parser as owner-override.sh and machine-author.sh.
#
# Usage: config-list.sh <key> <trusted-config>
#   The CALLER guarantees the config is from the trusted base (the intake workflow passes the base commit's
#   .asdd.yml), never a PR-controlled working tree.
set -euo pipefail
KEY="${1:?usage: config-list.sh <key> <trusted-config>}"
CONFIG="${2:?usage: config-list.sh <key> <trusted-config>}"
[ -f "$CONFIG" ] || exit 0
awk -v k="$KEY" '
  $0 ~ "^" k ":" {f=1; next}
  f && /^[^[:space:]#-]/ {f=0}
  f {
    line=$0
    sub(/#.*$/, "", line)
    sub(/^[[:space:]]*-[[:space:]]*/, "", line)
    gsub(/["'"'"' ]/, "", line)
    if (line != "") print line
  }
' "$CONFIG"
