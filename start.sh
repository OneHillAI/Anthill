#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# anthill - one-command alpha startup
# Run this from the project folder:  bash start.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"

PORT=8000
DB="data/anthill.db"
LOG_DIR="${ANTHILL_LOG_DIR:-/tmp}"
OS=$(uname -s)
SETUP_ONLY=0
NO_WAIT=0
for _arg in "$@"; do
  case "$_arg" in
    --setup-only) SETUP_ONLY=1 ;;   # install everything, don't launch
    --no-wait)    NO_WAIT=1 ;;       # launch detached (used by Anthill.app) - don't hold a terminal
  esac
done

echo ""
echo "  🤖  anthill alpha"
echo "  ─────────────────"

# ── 1. Python venv ────────────────────────────────────────────────────────────
if [ ! -f ".venv/bin/anthill" ]; then
  echo "  Installing dependencies (first run - takes ~2 min)..."
  make install
fi

# ── 2. Ollama (use the one on this machine; fetch it on macOS if missing) ────
server_up() { curl -s http://localhost:11434/ > /dev/null 2>&1; }

OLLAMA=$(command -v ollama || true)
for _candidate in "$HOME/bin/ollama" /usr/local/bin/ollama; do
  if [ -z "$OLLAMA" ] && [ -x "$_candidate" ]; then
    OLLAMA="$_candidate"
  fi
done

# A server that is already running is enough; no need for the binary.
if [ -z "$OLLAMA" ] && ! server_up; then
  if [ "$OS" != "Darwin" ]; then
    echo ""
    echo "  ⚠️  Ollama is not installed (no 'ollama' on PATH, in ~/bin or in /usr/local/bin)."
    echo "  start.sh only downloads it automatically on macOS. Install it once from:"
    echo ""
    echo "      https://ollama.com/download"
    echo ""
    echo "  then run this again."
    echo ""
    exit 1
  fi
  OLLAMA="$HOME/bin/ollama"
  echo "  Ollama not found - downloading the local model engine (~160 MB, once)..."
  mkdir -p "$HOME/bin"
  TMP=$(mktemp -d)
  if curl -fsSL --location -o "$TMP/Ollama-darwin.zip" \
       "https://ollama.com/download/Ollama-darwin.zip" 2>/dev/null \
     && unzip -oq "$TMP/Ollama-darwin.zip" -d "$TMP" 2>/dev/null \
     && cp "$TMP/Ollama.app/Contents/Resources/ollama" "$OLLAMA" 2>/dev/null; then
    chmod +x "$OLLAMA"
    rm -rf "$TMP"
    echo "  ✓ Ollama installed → $OLLAMA"
  else
    rm -rf "$TMP"
    echo ""
    echo "  ⚠️  Couldn't download Ollama automatically (no internet?)."
    echo "  Install it once from https://ollama.com, then run this again."
    echo ""
    exit 1
  fi
fi

if ! server_up; then
  echo "  Starting Ollama..."
  "$OLLAMA" serve > "$LOG_DIR/ollama.log" 2>&1 &
  sleep 3
fi

# Pull the default model if not present
if [ -n "$OLLAMA" ]; then
  if ! "$OLLAMA" list 2>/dev/null | grep -q "qwen2.5:3b"; then
    echo "  Pulling qwen2.5:3b (~2 GB, one-time download)..."
    "$OLLAMA" pull qwen2.5:3b
  fi
  echo "  ✓ Ollama running  (qwen2.5:3b ready)"
elif curl -s http://localhost:11434/api/tags 2>/dev/null | grep -q "qwen2.5:3b"; then
  echo "  ✓ Ollama running  (qwen2.5:3b ready)"
else
  echo "  ✓ Ollama is running, but no 'ollama' command was found to check for qwen2.5:3b."
  echo "    If chat fails, pull it where Ollama runs:  ollama pull qwen2.5:3b"
fi

# ── 3. Persist security secrets (generate once, reuse across restarts) ───────
# Without stable secrets, every restart invalidates all sessions (JWT) and makes
# AES-256 at-rest data undecryptable. Generated once into .env (gitignored). On a Mac the
# app moves the key into the Keychain at its first start and leaves a marker in the data
# folder (key-location.json); from then on no key is generated here. A key is never made
# over a database that already holds encrypted values: a new key would make them unreadable.
ENV_FILE=".env"
KEY_HOME="${ANTHILL_HOME:-$(dirname "$DB")}"
if [ ! -f "$KEY_HOME/key-location.json" ]; then
  _new_env=0
  [ -f "$ENV_FILE" ] || _new_env=1
  if [ "$_new_env" = "1" ] || ! grep -Eq "^[[:space:]]*(export[[:space:]]+)?ANTHILL_ENCRYPTION_KEY=" "$ENV_FILE"; then
    _rc=0
    .venv/bin/python -m anthill.web.key_startup has-values "$DB" || _rc=$?
    if [ "$_rc" -ne 0 ]; then
      echo ""
      echo "  ⚠️  Anthill did not start: its encryption key is missing, and $DB may already"
      echo "  hold saved passwords that need it. A new key would make them unreadable, so none was made."
      echo "  Put the old ANTHILL_ENCRYPTION_KEY line back in $ENV_FILE (from a backup or an older copy of"
      echo "  this folder), then run this again. Nothing was changed."
      echo ""
      exit 1
    fi
    echo "  Generating a persistent encryption key → $ENV_FILE"
    echo "ANTHILL_ENCRYPTION_KEY=$(.venv/bin/python -c 'import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())')" >> "$ENV_FILE"
  fi
  if ! grep -q "ANTHILL_JWT_SECRET" "$ENV_FILE"; then
    echo "  Generating a persistent session secret → $ENV_FILE"
    echo "ANTHILL_JWT_SECRET=$(.venv/bin/python -c 'import secrets;print(secrets.token_hex(32))')" >> "$ENV_FILE"
  fi
  if [ "$_new_env" = "1" ]; then
    {
      echo ""
      echo "# Google web search (optional, recommended - the reliable search backend)."
      echo "# Get a key: console.cloud.google.com (enable 'Custom Search API') and"
      echo "# create an engine: programmablesearchengine.google.com (set it to search the whole web)."
      echo "# Free up to 100 searches/day. Leave blank to fall back to DuckDuckGo."
      echo "# GOOGLE_SEARCH_API_KEY="
      echo "# GOOGLE_SEARCH_CX="
      echo ""
      echo "# Page-fetch quality (optional): clean markdown extraction for web pages."
      echo "# Firecrawl (paid, best) - firecrawl.dev; or free Jina Reader. Else a built-in fetch is used."
      echo "# FIRECRAWL_API_KEY="
      echo "# JINA_API_KEY="
      echo ""
      echo "# Self-hosted wiki search (optional): point at a Meilisearch instance"
      echo "# (meilisearch.com) for fast in-perimeter retrieval. Else embeddings are used."
      echo "# MEILI_URL=http://localhost:7700"
      echo "# MEILI_API_KEY="
    } >> "$ENV_FILE"
  fi
  chmod 600 "$ENV_FILE"
fi
if [ -f "$ENV_FILE" ]; then set -a; . "./$ENV_FILE"; set +a; fi   # export the secrets into the environment
echo "  ✓ Security secrets loaded (stable across restarts)"

if [ "$SETUP_ONLY" = "1" ]; then
  echo ""
  if [ "$OS" = "Darwin" ]; then
    echo "  ✓ Setup complete. Start anytime by double-clicking anthill.command."
  else
    echo "  ✓ Setup complete. Start anytime with:  bash start.sh"
  fi
  echo ""
  exit 0
fi

# ── 4. Stop any existing dashboard ───────────────────────────────────────────
if command -v lsof > /dev/null 2>&1; then
  OLD=$(lsof -ti :$PORT 2>/dev/null || true)
elif command -v fuser > /dev/null 2>&1; then
  OLD=$(fuser -n tcp $PORT 2>/dev/null || true)
elif command -v ss > /dev/null 2>&1; then
  OLD=$(ss -ltnp "sport = :$PORT" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 || true)
else
  OLD=""
  echo "  ⚠️  Can't check for an old dashboard on :$PORT (no lsof, fuser or ss found)."
fi
if [ -n "$OLD" ]; then
  kill $OLD 2>/dev/null || true
  sleep 1
fi

# ── 5. Start dashboard ────────────────────────────────────────────────────────
mkdir -p data
# nohup so the server outlives this script in --no-wait mode (Anthill.app launch),
# while still being a child we can `wait` on in normal foreground mode.
ANTHILL_LOCAL_ONLY="${ANTHILL_LOCAL_ONLY-1}" ANTHILL_DB="$DB" nohup .venv/bin/anthill web --port $PORT --host 127.0.0.1 \
  > "$LOG_DIR/anthill-web.log" 2>&1 &
DASHBOARD_PID=$!
echo "  ✓ Dashboard PID $DASHBOARD_PID"

# Wait until it responds
for i in $(seq 1 15); do
  if /usr/bin/curl -s -o /dev/null http://127.0.0.1:$PORT/; then
    break
  fi
  sleep 1
done

# ── 6. Open browser ───────────────────────────────────────────────────────────
URL="http://localhost:$PORT"
echo "  ✓ Dashboard is up:  $URL"
echo ""
echo "  First run?  → Go to $URL/setup  to create your account."
echo "  Returning?  → Go to $URL/login  or $URL/chat"
echo ""
echo "  Logs: $LOG_DIR/anthill-web.log   $LOG_DIR/ollama.log"
echo "  Stop: kill $DASHBOARD_PID"
if [ "$NO_WAIT" != "1" ]; then
  echo "  Keep this terminal open - Ctrl-C stops Anthill. Run with --no-wait to detach."
fi
echo ""
echo "  Tip: 'make autostart' makes Ollama + the dashboard launch on login"
echo "       and relaunch if they crash, so scheduled Tasks survive a reboot."
echo ""

# Detached launch (Anthill.app): the server is up and nohup'd - return now so no
# terminal is held open. The .app opens the browser itself.
if [ "$NO_WAIT" = "1" ]; then
  exit 0
fi

# A background job started by a script ignores Ctrl-C, so stop the dashboard ourselves.
trap 'kill $DASHBOARD_PID 2>/dev/null || true' INT TERM

if [ "$OS" = "Darwin" ]; then
  open "$URL" 2>/dev/null || true
elif command -v xdg-open > /dev/null 2>&1; then
  xdg-open "$URL" > /dev/null 2>&1 || true
fi

# Keep script alive so Ctrl-C stops cleanly
wait $DASHBOARD_PID
