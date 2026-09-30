#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Install macOS LaunchAgents so Ollama AND the anthill dashboard start on login
# and relaunch if they crash - the same auto-start the official Ollama app does,
# adapted to our bare-binary install. This is what makes scheduled Tasks survive
# a reboot.
#
#   bash scripts/install-autostart.sh           # install + load
#   bash scripts/install-autostart.sh --remove  # uninstall
#
# Linux servers should use a systemd unit instead (see docs/setup.md).
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
AGENTS_DIR="$HOME/Library/LaunchAgents"
OLLAMA_BIN="$HOME/bin/ollama"
PORT=8000

OLLAMA_PLIST="$AGENTS_DIR/com.anthill.ollama.plist"
DASH_PLIST="$AGENTS_DIR/com.anthill.dashboard.plist"

if [ "${1:-}" = "--remove" ]; then
  for label in com.anthill.ollama com.anthill.dashboard; do
    launchctl unload "$AGENTS_DIR/$label.plist" 2>/dev/null || true
    rm -f "$AGENTS_DIR/$label.plist"
    echo "  removed $label"
  done
  echo "Auto-start removed."
  exit 0
fi

if [ "$(uname)" != "Darwin" ]; then
  echo "This installer is for macOS. On Linux use a systemd service (docs/setup.md)."
  exit 1
fi

mkdir -p "$AGENTS_DIR"

# ── Ollama agent ──────────────────────────────────────────────────────────────
cat > "$OLLAMA_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.anthill.ollama</string>
  <key>ProgramArguments</key>
  <array>
    <string>$OLLAMA_BIN</string>
    <string>serve</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/ollama.log</string>
  <key>StandardErrorPath</key><string>/tmp/ollama.log</string>
</dict>
</plist>
PLIST

# ── Dashboard agent ─────────────────────────────────────────────────────────────
cat > "$DASH_PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.anthill.dashboard</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PROJECT_DIR/.venv/bin/anthill</string>
    <string>web</string>
    <string>--port</string><string>$PORT</string>
    <string>--host</string><string>127.0.0.1</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJECT_DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>ANTHILL_DB</key><string>$PROJECT_DIR/data/anthill.db</string>
    <key>ANTHILL_WORKSPACE</key><string>$PROJECT_DIR/workspace</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/anthill-web.log</string>
  <key>StandardErrorPath</key><string>/tmp/anthill-web.log</string>
</dict>
</plist>
PLIST

# Reload (unload first so re-running picks up changes)
launchctl unload "$OLLAMA_PLIST" 2>/dev/null || true
launchctl unload "$DASH_PLIST"   2>/dev/null || true
launchctl load "$OLLAMA_PLIST"
launchctl load "$DASH_PLIST"

echo "Auto-start installed:"
echo "  • Ollama      → starts on login, relaunches if it crashes"
echo "  • Dashboard   → http://localhost:$PORT (starts on login)"
echo ""
echo "Scheduled Tasks will now run across reboots (while the Mac is on/awake)."
echo "Remove with: bash scripts/install-autostart.sh --remove"
