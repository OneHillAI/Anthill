#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Turn this Mac (a Mac Mini / Studio) into a headless, always-on org backend that
# your whole team reaches over the LAN. This is the team-grade path: it binds
# 0.0.0.0 (not just localhost), sizes + pulls the model for this box, and prints
# the always-on power + auto-login recipe.
#
#   bash scripts/install-mac-appliance.sh                  # install (sized model)
#   bash scripts/install-mac-appliance.sh --model qwen2.5:32b
#   bash scripts/install-mac-appliance.sh --apply-power    # also run pmset (sudo)
#   bash scripts/install-mac-appliance.sh --uninstall
#
# This supersedes install-autostart.sh's localhost dashboard agent; if you ran
# that before, it is unloaded below so the two do not fight over the port.
# Linux servers should use a systemd unit instead (see docs/setup.md).
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [ "$(uname)" != "Darwin" ]; then
  echo "This installer is for macOS. On Linux use a systemd service (docs/setup.md)."
  exit 1
fi

# Resolve the anthill CLI: prefer the repo venv, then PATH.
ANTHILL_BIN="$PROJECT_DIR/.venv/bin/anthill"
if [ ! -x "$ANTHILL_BIN" ]; then
  ANTHILL_BIN="$(command -v anthill || true)"
fi
if [ -z "$ANTHILL_BIN" ]; then
  echo "Could not find the 'anthill' CLI. Install it first (pip install -e . in $PROJECT_DIR)."
  exit 1
fi

if [ "${1:-}" = "--uninstall" ]; then
  "$ANTHILL_BIN" appliance uninstall
  exit 0
fi

if ! command -v ollama >/dev/null 2>&1 && [ ! -x "$HOME/bin/ollama" ]; then
  echo "Note: ollama was not found. Install it (https://ollama.com) so the model can serve."
fi

# Retire the old localhost-only dashboard agent so it doesn't share the port.
OLD_DASH="$HOME/Library/LaunchAgents/com.anthill.dashboard.plist"
if [ -f "$OLD_DASH" ]; then
  echo "Retiring the old localhost dashboard agent (the appliance binds 0.0.0.0 instead)…"
  launchctl unload "$OLD_DASH" 2>/dev/null || true
  rm -f "$OLD_DASH"
fi

# Hand off to the CLI installer (writes + loads the LaunchAgent, pulls the model,
# reports GPU/LAN, and prints the power + auto-login steps).
exec "$ANTHILL_BIN" appliance install "$@"
