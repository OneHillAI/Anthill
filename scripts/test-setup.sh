#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# End-to-end SETUP test: from a clean state through to a fully set-up system.
# Verifies the path a new admin takes - install prerequisites, server comes up,
# create the organization, log in, reach the dashboard. Prints PASS/FAIL per
# stage and exits non-zero on any failure.
#
#   bash scripts/test-setup.sh
#
# Uses a throwaway DB/workspace on a test port; never touches your real data.
# Requires Ollama already present (the installer acquires it; the test assumes it).
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
cd "$(dirname "$0")/.."

PORT=8123
DB="/tmp/anthill-setup-test.db"
WS="/tmp/anthill-setup-test-ws"
CK="/tmp/anthill-setup-test-cookies.txt"
PASS=0; FAIL=0
say()  { printf "  %-52s" "$1"; }
ok()   { echo "✓ PASS"; PASS=$((PASS+1)); }
bad()  { echo "✗ FAIL - $1"; FAIL=$((FAIL+1)); }

cleanup() {
  [ -n "${PID:-}" ] && kill "$PID" 2>/dev/null || true
  lsof -ti :$PORT 2>/dev/null | xargs kill 2>/dev/null || true
  rm -f "$DB" "$CK"; rm -rf "$WS"
}
trap cleanup EXIT

echo ""; echo "  anthill setup → ready test"; echo "  ─────────────────────────"

# Stage 1 - prerequisites installed (venv + the CLI entry point)
say "1. dependencies installed"
if [ -x ".venv/bin/anthill" ]; then ok; else bad "run 'make install' (or install.command)"; fi

# Stage 2 - security secrets present/persistent
say "2. persistent security secrets"
if [ -f ".env" ] && grep -q ANTHILL_JWT_SECRET .env && grep -q ANTHILL_ENCRYPTION_KEY .env; then
  ok; else bad ".env missing secrets (start.sh generates them)"; fi

# Stage 3 - dashboard starts on a clean DB
say "3. dashboard starts"
lsof -ti :$PORT 2>/dev/null | xargs kill 2>/dev/null || true
rm -f "$DB"; rm -rf "$WS"
ANTHILL_LOCAL_ONLY="${ANTHILL_LOCAL_ONLY-1}" ANTHILL_WORKSPACE="$WS" .venv/bin/anthill web --port $PORT --host 127.0.0.1 --db "$DB" \
  > /tmp/anthill-setup-test.log 2>&1 &
PID=$!
up=0
for _ in $(seq 1 20); do
  /usr/bin/curl -s -o /dev/null "http://127.0.0.1:$PORT/setup" && { up=1; break; }
  sleep 1
done
[ "$up" = 1 ] && ok || bad "server did not come up (see /tmp/anthill-setup-test.log)"

# Stage 4 - fresh install routes to the setup screen (not a login dead-end)
say "4. fresh install shows setup"
code=$(/usr/bin/curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/setup")
[ "$code" = "200" ] && ok || bad "/setup returned $code"

# Stage 5 - create the organization + admin
say "5. create organization + admin"
loc=$(/usr/bin/curl -s -c "$CK" -o /dev/null -w '%{redirect_url}' -X POST "http://127.0.0.1:$PORT/setup" \
  --data-urlencode "org_name=Setup Test Co" \
  --data-urlencode "admin_email=admin@test.local" \
  --data-urlencode "admin_password=supersecret1234" \
  --data-urlencode "admin_name=Admin" \
  --data-urlencode "topology=local")
echo "$loc" | grep -q "/$" && ok || bad "setup POST didn't redirect to dashboard (got '$loc')"

# Stage 6 - dashboard reachable with the session from setup
say "6. dashboard reachable (logged in)"
code=$(/usr/bin/curl -s -b "$CK" -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/")
[ "$code" = "200" ] && ok || bad "dashboard returned $code"

# Stage 7 - log out / log back in with the new credentials
say "7. admin can log in"
/usr/bin/curl -s -c "$CK" -o /dev/null -X POST "http://127.0.0.1:$PORT/login" \
  --data-urlencode "email=admin@test.local" --data-urlencode "password=supersecret1234"
code=$(/usr/bin/curl -s -b "$CK" -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/settings")
[ "$code" = "200" ] && ok || bad "login → settings returned $code"

echo ""; echo "  ─────────────────────────"
echo "  Result: $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ] && { echo "  ✅ Setup works end-to-end."; echo ""; exit 0; } \
                || { echo "  ❌ Setup has a problem (see above)."; echo ""; exit 1; }
