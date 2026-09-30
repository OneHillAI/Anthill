#!/usr/bin/env bash
# Test: openai-compat.sh normalizes a base ASDD_MODEL_URL to the full chat-completions endpoint
# instead of POSTing to it verbatim (which failed closed with no cause). Stubs curl to report the URL
# it was asked to POST to. No network.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT="$HERE/openai-compat.sh"

STUB="$(mktemp -d)"
cat > "$STUB/curl" <<'EOF'
#!/usr/bin/env bash
# echo the first http(s) argument back as the review "content" so the caller sees the endpoint used
for a in "$@"; do case "$a" in http*) url="$a";; esac; done
printf '{"choices":[{"message":{"content":"%s"}}]}' "$url"
EOF
chmod +x "$STUB/curl"

run() { PATH="$STUB:$PATH" ASDD_RUNTIME_TOKEN=x ASDD_MODEL=m ASDD_MODEL_URL="$1" bash "$SCRIPT" </dev/null 2>"$STUB/err"; }
fail() { echo "FAIL: $1" >&2; exit 1; }

# base URL -> appended + a notice on stderr
got="$(run 'https://x.test/v1')"
[ "$got" = "https://x.test/v1/chat/completions" ] || fail "base URL not normalized (got '$got')"
grep -q 'not a chat-completions endpoint' "$STUB/err" || fail "no notice emitted for a base URL"

# trailing slash on the base -> same
got="$(run 'https://x.test/v1/')"
[ "$got" = "https://x.test/v1/chat/completions" ] || fail "trailing-slash base not normalized (got '$got')"

# already-full URL -> unchanged, no notice
got="$(run 'https://x.test/v1/chat/completions')"
[ "$got" = "https://x.test/v1/chat/completions" ] || fail "full URL changed (got '$got')"
grep -q 'not a chat-completions endpoint' "$STUB/err" && fail "notice wrongly emitted for a full URL" || true

rm -rf "$STUB"
echo "openai-compat.test.sh: PASS"
