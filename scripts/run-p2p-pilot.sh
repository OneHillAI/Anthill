#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# Anthill Node Pillar P2P Pilot - one-command launcher
#
# Share this file with a pilot partner (macOS or Linux) and run:
#   bash run-p2p-pilot.sh
#
# What it does:
#   1. Clones or updates branch `feat/node-p2p-pilot` into ~/anthill-p2p-pilot
#      (or uses the current repo if already inside an Anthill checkout on that branch).
#   2. Creates the Python .venv (`make install`) on first run.
#   3. Ensures Ollama is running locally and a chat model is available.
#   4. Starts an isolated Anthill instance on http://127.0.0.1:8001 with its own
#      persistent pilot data dir (~/.anthill-p2p-pilot) so your regular Anthill
#      data is never touched and your Ed25519 keys survive restarts.
#   5. Opens a Cloudflare Quick Tunnel (https://*.trycloudflare.com) so two Macs
#      in different locations can pair and sync live with zero port forwarding.
#
# Options:
#   --port <port>       Local port (default: 8001)
#   --model <tag>       Ollama chat model to ensure (default: reuses installed model, or qwen2.5:3b)
#   --seed-demo <who>   Seed Rome travel dummy pages (raul, christoph, or peter)
#   --no-tunnel         Skip cloudflared (use for same-LAN, Tailscale, or offline bundles)
#   --reset             Wipe ~/.anthill-p2p-pilot and start with a fresh identity
#   --no-open           Do not automatically open the browser
#   -h, --help          Show this help
# -----------------------------------------------------------------------------
set -euo pipefail

REPO_URL="${ANTHILL_PILOT_REPO:-https://github.com/OneHillAI/Anthill.git}"
BRANCH="${ANTHILL_PILOT_BRANCH:-feat/node-p2p-pilot}"
DEFAULT_REPO_DIR="${ANTHILL_PILOT_DIR:-$HOME/anthill-p2p-pilot}"
PILOT_DATA="${ANTHILL_PILOT_DATA:-$HOME/.anthill-p2p-pilot}"
PORT=8001
MODEL=""
DEFAULT_MODEL="qwen2.5:3b"
SEED_DEMO=""
USE_TUNNEL=1
RESET_DATA=0
OPEN_BROWSER=1

while [ $# -gt 0 ]; do
  case "$1" in
    --port)      PORT="$2"; shift 2 ;;
    --model)     MODEL="$2"; shift 2 ;;
    --seed-demo) SEED_DEMO="$2"; shift 2 ;;
    --no-tunnel) USE_TUNNEL=0; shift ;;
    --reset)     RESET_DATA=1; shift ;;
    --no-open)   OPEN_BROWSER=0; shift ;;
    -h|--help)
      sed -n '2,26p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown option: $1 (try --help)" >&2
      exit 1
      ;;
  esac
done

OS="$(uname -s)"
ANTHILL_PID=""
TUNNEL_PID=""
OLLAMA_STARTED_BY_US=0
OLLAMA_PID=""
CLEANED=0

cleanup() {
  [ "$CLEANED" = "1" ] && return 0
  CLEANED=1
  trap - INT TERM EXIT
  echo ""
  echo "  Stopping pilot services..."
  [ -n "$TUNNEL_PID" ] && kill "$TUNNEL_PID" 2>/dev/null || true
  [ -n "$ANTHILL_PID" ] && kill "$ANTHILL_PID" 2>/dev/null || true
  if [ "$OLLAMA_STARTED_BY_US" = "1" ] && [ -n "$OLLAMA_PID" ]; then
    kill "$OLLAMA_PID" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
  echo "  Stopped cleanly."
}
trap cleanup INT TERM EXIT

echo ""
echo "  Anthill Node Pillar - P2P Pilot"
echo "  -------------------------------"

# -- 1. Locate or clone the repo on feat/node-p2p-pilot -----------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR=""

if [ -f "$SCRIPT_DIR/../pyproject.toml" ] && [ -d "$SCRIPT_DIR/../anthill/node" ]; then
  REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
  echo "  Using local Anthill checkout: $REPO_DIR"
elif [ -f "./pyproject.toml" ] && [ -d "./anthill/node" ]; then
  REPO_DIR="$(pwd)"
  echo "  Using current Anthill checkout: $REPO_DIR"
else
  REPO_DIR="$DEFAULT_REPO_DIR"
  if [ ! -d "$REPO_DIR/.git" ]; then
    echo "  Cloning Anthill ($BRANCH) into $REPO_DIR..."
    git clone --branch "$BRANCH" --single-branch "$REPO_URL" "$REPO_DIR"
  else
    echo "  Updating $REPO_DIR ($BRANCH)..."
    git -C "$REPO_DIR" fetch origin "$BRANCH"
    git -C "$REPO_DIR" checkout "$BRANCH"
    git -C "$REPO_DIR" pull --ff-only origin "$BRANCH" || true
  fi
fi

cd "$REPO_DIR"

# -- 2. Python virtualenv -----------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "  ERROR: python3 is required (Python 3.10+)." >&2
  exit 1
fi

if [ ! -x ".venv/bin/anthill" ]; then
  echo "  Installing Python dependencies (first run takes ~1-2 min)..."
  python3 -m venv .venv
  .venv/bin/pip install -q -U pip
  .venv/bin/pip install -q -e ".[dev,docs]"
fi
echo "  [ok] Python environment ready"

# -- 3. Ollama local model engine ---------------------------------------------
ollama_up() { curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; }

OLLAMA="$(command -v ollama || true)"
for _cand in "$HOME/bin/ollama" /opt/homebrew/bin/ollama /usr/local/bin/ollama; do
  if [ -z "$OLLAMA" ] && [ -x "$_cand" ]; then
    OLLAMA="$_cand"
  fi
done

if ! ollama_up; then
  if [ -z "$OLLAMA" ]; then
    echo "  WARN: Ollama not found. Install from https://ollama.com/download"
    echo "        Continuing anyway (pairing and bundle sync work without Ollama,"
    echo "        but live AI counter-check consultations need Ollama running)."
  else
    echo "  Starting Ollama in background..."
    "$OLLAMA" serve > /tmp/anthill-pilot-ollama.log 2>&1 &
    OLLAMA_PID=$!
    OLLAMA_STARTED_BY_US=1
    for _i in $(seq 1 20); do
      ollama_up && break
      sleep 0.5
    done
  fi
fi

if ollama_up; then
  INSTALLED_MODELS=""
  if [ -n "$OLLAMA" ]; then
    INSTALLED_MODELS="$("$OLLAMA" list 2>/dev/null | awk 'NR>1 && $1 != "" {print $1}' || true)"
  fi
  if [ -n "$MODEL" ]; then
    if [ -n "$OLLAMA" ] && ! printf '%s\n' "$INSTALLED_MODELS" | grep -Fxq "$MODEL"; then
      echo "  Pulling requested local model $MODEL..."
      "$OLLAMA" pull "$MODEL"
    fi
    echo "  [ok] Ollama running ($MODEL ready)"
  elif [ -n "$INSTALLED_MODELS" ]; then
    FIRST_MODEL="$(printf '%s\n' "$INSTALLED_MODELS" | head -n 1)"
    echo "  [ok] Ollama running (using existing local models, e.g. $FIRST_MODEL)"
  elif [ -n "$OLLAMA" ]; then
    echo "  No local models installed yet - pulling $DEFAULT_MODEL (one-time download)..."
    "$OLLAMA" pull "$DEFAULT_MODEL"
    echo "  [ok] Ollama running ($DEFAULT_MODEL ready)"
  else
    echo "  [ok] Ollama running"
  fi
fi

# -- 4. Isolated persistent pilot data dir & encryption key -------------------
if [ "$RESET_DATA" = "1" ]; then
  echo "  Resetting pilot data directory: $PILOT_DATA"
  rm -rf "$PILOT_DATA"
fi

mkdir -p "$PILOT_DATA/org-wiki" "$PILOT_DATA/wikis"
KEY_FILE="$PILOT_DATA/.pilot-keys"

if [ -n "$SEED_DEMO" ]; then
  echo "  Seeding Rome travel demo pages (persona: $SEED_DEMO)..."
  .venv/bin/python scripts/seed-travel-demo.py --persona "$SEED_DEMO" --wiki-root "$PILOT_DATA/wikis"
fi

if [ ! -f "$KEY_FILE" ]; then
  ENC_KEY="$(.venv/bin/python -c 'import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())')"
  JWT_KEY="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
  umask 077
  cat > "$KEY_FILE" <<EOF
ANTHILL_ENCRYPTION_KEY=$ENC_KEY
ANTHILL_JWT_SECRET=$JWT_KEY
EOF
  chmod 600 "$KEY_FILE"
  echo "  [ok] Created isolated pilot encryption keys ($KEY_FILE)"
fi

# shellcheck disable=SC1090
set -a
source "$KEY_FILE"
set +a

# -- 5. Start Anthill on loopback ---------------------------------------------
ANTHILL_LOG="$PILOT_DATA/anthill.log"
echo "  Starting Anthill on http://127.0.0.1:$PORT ..."

ANTHILL_HOME="$PILOT_DATA" \
ANTHILL_DB="$PILOT_DATA/anthill.db" \
ANTHILL_WIKI_ROOT="$PILOT_DATA/wikis" \
ANTHILL_ORG_WIKI="$PILOT_DATA/org-wiki" \
ANTHILL_LOCAL_ONLY=1 \
.venv/bin/anthill web --host 127.0.0.1 --port "$PORT" > "$ANTHILL_LOG" 2>&1 &
ANTHILL_PID=$!

READY=0
for _i in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:$PORT/" >/dev/null 2>&1 || curl -sS -o /dev/null "http://127.0.0.1:$PORT/setup" 2>/dev/null; then
    READY=1
    break
  fi
  if ! kill -0 "$ANTHILL_PID" 2>/dev/null; then
    echo "  ERROR: Anthill exited unexpectedly. Last log lines:" >&2
    tail -n 30 "$ANTHILL_LOG" >&2
    exit 1
  fi
  sleep 0.5
done

if [ "$READY" != "1" ]; then
  echo "  ERROR: Anthill did not become ready on port $PORT. Check $ANTHILL_LOG" >&2
  exit 1
fi
echo "  [ok] Anthill ready at http://127.0.0.1:$PORT"

# -- 6. Optional Cloudflare Quick Tunnel for cross-Internet P2P ---------------
TUNNEL_URL=""
if [ "$USE_TUNNEL" = "1" ]; then
  CF="$(command -v cloudflared || true)"
  if [ -z "$CF" ] && [ "$OS" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
    echo "  Installing cloudflared via Homebrew (once)..."
    brew install cloudflared >/dev/null
    CF="$(command -v cloudflared || true)"
  fi

  if [ -n "$CF" ]; then
    TUNNEL_LOG="$PILOT_DATA/cloudflared.log"
    : > "$TUNNEL_LOG"
    echo "  Opening HTTPS Quick Tunnel..."
    "$CF" tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" > "$TUNNEL_LOG" 2>&1 &
    TUNNEL_PID=$!

    for _i in $(seq 1 50); do
      TUNNEL_URL="$(grep -Eo 'https://[a-zA-Z0-9-]+\.trycloudflare\.com' "$TUNNEL_LOG" | head -n 1 || true)"
      if [ -n "$TUNNEL_URL" ]; then
        break
      fi
      sleep 0.5
    done
  else
    echo "  WARN: cloudflared not found (install with 'brew install cloudflared')."
    echo "        You can still connect via Tailscale/LAN or offline .anthill-bundle files."
  fi
fi

# -- 7. Print clear pairing checklist & open browser --------------------------
FIRST_URL="http://127.0.0.1:$PORT/peers"
if [ ! -f "$PILOT_DATA/anthill.db" ]; then
  FIRST_URL="http://127.0.0.1:$PORT/setup"
fi

echo ""
echo "  ====================================================================="
echo "  Anthill P2P Pilot is LIVE"
echo "  ====================================================================="
echo "  Local app URL :  http://127.0.0.1:$PORT/setup  (then /peers)"
if [ -n "$TUNNEL_URL" ]; then
  echo "  Your Peer URL :  $TUNNEL_URL"
else
  echo "  Your Peer URL :  (no tunnel - use LAN/Tailscale IP or .anthill-bundle)"
fi
echo "  Pilot data    :  $PILOT_DATA"
echo "  Logs          :  $ANTHILL_LOG"
echo ""
echo "  Quick pairing steps:"
echo "  1. Open http://127.0.0.1:$PORT/setup (first time) -> then click 'Peers & Hills'."
echo "  2. Toggle 'Node peer-to-peer' ON."
if [ -n "$TUNNEL_URL" ]; then
  echo "  3. Under 'This desktop's peer URL', paste:  $TUNNEL_URL  and Save."
else
  echo "  3. Under 'This desktop's peer URL', paste your reachable URL (optional)."
fi
echo "  4. Either click 'Create pairing invite' and send the anthill-pair://v1/..."
echo "     token to Raul, OR paste Raul's token under 'Join with an invite'."
echo "  5. Confirm the 6-digit SAS code matches on both screens -> Trust & Connect."
echo ""
echo "  Press Ctrl+C in this window to stop Anthill and the tunnel."
echo "  ====================================================================="
echo ""

if [ "$OPEN_BROWSER" = "1" ] && [ "$OS" = "Darwin" ]; then
  open "http://127.0.0.1:$PORT/setup" >/dev/null 2>&1 || true
fi

# Wait until Anthill exits (or user hits Ctrl+C)
wait "$ANTHILL_PID"
