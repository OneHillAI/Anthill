#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# anthill - one-command alpha startup
# Run this from the project folder:  bash start.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")"

PORT=8000
DB="data/anthill.db"
OLLAMA="$HOME/bin/ollama"
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

# ── 2. Ollama (auto-acquire the local model engine if missing) ───────────────
if [ ! -f "$OLLAMA" ]; then
  echo "  Ollama not found - downloading the local model engine (~160 MB, once)..."
  mkdir -p "$HOME/bin"
  TMP=$(mktemp -d)
  if /usr/bin/curl -fsSL --location -o "$TMP/Ollama-darwin.zip" \
       "https://ollama.com/download/Ollama-darwin.zip" 2>/dev/null \
     && /usr/bin/unzip -oq "$TMP/Ollama-darwin.zip" -d "$TMP" 2>/dev/null \
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

if ! /usr/bin/curl -s http://localhost:11434/ > /dev/null 2>&1; then
  echo "  Starting Ollama..."
  "$OLLAMA" serve > /tmp/ollama.log 2>&1 &
  sleep 3
fi

# Pull the default model if not present
if ! "$OLLAMA" list 2>/dev/null | grep -q "qwen2.5:3b"; then
  echo "  Pulling qwen2.5:3b (~2 GB, one-time download)..."
  "$OLLAMA" pull qwen2.5:3b
fi
echo "  ✓ Ollama running  (qwen2.5:3b ready)"

# ── 3. Persist security secrets (generate once, reuse across restarts) ───────
# Without stable secrets, every restart invalidates all sessions (JWT) and makes
# AES-256 at-rest data undecryptable. Generated once into .env (gitignored).
ENV_FILE=".env"
if [ ! -f "$ENV_FILE" ] || ! grep -q "ANTHILL_JWT_SECRET" "$ENV_FILE"; then
  echo "  Generating persistent security secrets → $ENV_FILE"
  {
    echo "ANTHILL_JWT_SECRET=$(.venv/bin/python -c 'import secrets;print(secrets.token_hex(32))')"
    echo "ANTHILL_ENCRYPTION_KEY=$(.venv/bin/python -c 'import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())')"
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
  chmod 600 "$ENV_FILE"
fi
set -a; . "./$ENV_FILE"; set +a   # export the secrets into the environment
echo "  ✓ Security secrets loaded (stable across restarts)"

if [ "$SETUP_ONLY" = "1" ]; then
  echo ""
  echo "  ✓ Setup complete. Start anytime by double-clicking anthill.command."
  echo ""
  exit 0
fi

# ── 4. Stop any existing dashboard ───────────────────────────────────────────
OLD=$(lsof -ti :$PORT 2>/dev/null || true)
if [ -n "$OLD" ]; then
  kill "$OLD" 2>/dev/null || true
  sleep 1
fi

# ── 5. Start dashboard ────────────────────────────────────────────────────────
mkdir -p data
# nohup so the server outlives this script in --no-wait mode (Anthill.app launch),
# while still being a child we can `wait` on in normal foreground mode.
ANTHILL_DB="$DB" nohup .venv/bin/anthill web --port $PORT --host 127.0.0.1 \
  > /tmp/anthill-web.log 2>&1 &
DASHBOARD_PID=$!
echo "  ✓ Dashboard PID $DASHBOARD_PID"

# Wait until it responds
for i in $(seq 1 15); do
  if /usr/bin/curl -s -o /dev/null http://127.0.0.1:$PORT/; then
    break
  fi
  sleep 1
done

# ── 5. Open browser ───────────────────────────────────────────────────────────
URL="http://localhost:$PORT"
echo "  ✓ Open in Chrome:  $URL"
echo ""
echo "  First run?  → Go to $URL/setup  to create your account."
echo "  Returning?  → Go to $URL/login  or $URL/chat"
echo ""
echo "  Logs: /tmp/anthill-web.log   /tmp/ollama.log"
echo "  Stop: kill $DASHBOARD_PID"
echo ""
echo "  Tip: 'make autostart' makes Ollama + the dashboard launch on login"
echo "       and relaunch if they crash, so scheduled Tasks survive a reboot."
echo ""

# Detached launch (Anthill.app): the server is up and nohup'd - return now so no
# terminal is held open. The .app opens the browser itself.
if [ "$NO_WAIT" = "1" ]; then
  exit 0
fi

# Open browser on macOS
open "$URL" 2>/dev/null || true

# Keep script alive so Ctrl-C stops cleanly
wait $DASHBOARD_PID
