#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# anthill - ADMIN one-click installer.
#
# Double-click this file in Finder. On a fresh Mac it sets up everything with no
# Terminal typing: Python environment, the Ollama model engine, the local model,
# your security keys, then opens the dashboard so you can create your org.
#
# First double-click only: macOS may say "unidentified developer" - right-click
# the file → Open → Open, once. After that it just works.
#
# Everyday after install: double-click  anthill.command  to start it.
# ─────────────────────────────────────────────────────────────────────────────
cd "$(dirname "$0")"

clear
echo "════════════════════════════════════════════"
echo "   🤖  Installing anthill (admin)"
echo "   Everything runs on this Mac. Nothing leaves it."
echo "════════════════════════════════════════════"
echo ""
echo "   This takes a few minutes the first time (downloads the model engine"
echo "   and a ~2 GB model). You don't need to type anything - sit tight."
echo ""

# Do the full first-time setup without launching yet (venv, Ollama, model, keys).
if ! bash start.sh --setup-only; then
  echo ""
  echo "   ⚠️  Setup didn't finish. Check the messages above (usually no internet"
  echo "       on the first run). Fix that, then double-click this file again."
  echo ""
  read -r -p "   Press Return to close." _ || true
  exit 1
fi

echo ""
echo "   ✓ anthill is installed."
echo ""

# Build a no-Terminal launcher so you can start Anthill like any app, without the Rust/Tauri
# toolchain this repo's real release build needs. This is a lightweight fallback, not that
# release: it has no native window (it opens in your browser) and doesn't auto-update. If you
# just want the real app, skip this and download it from anthill.run/download instead.
echo "   Creating a local launcher (dev/fallback build - not the signed anthill.run release)…"
if bash scripts/build-app.sh >/dev/null 2>&1; then
  echo "   ✓ Created dist/Anthill-DevBuild.app - drag it into your Applications folder."
  echo "     (Or run 'make dmg' for a drag-to-Applications installer.)"
else
  echo "   (App build skipped - you can still start with anthill.command.)"
fi
echo ""

read -r -p "   Start anthill now and open the dashboard? [Y/n] " ans || true
case "${ans:-Y}" in
  [Nn]*) echo "   OK - open Anthill from Applications, or double-click anthill.command."; exit 0 ;;
esac

# Start the server in the background (no Terminal needed) and open the browser.
# Because it's detached, you can close this window and Anthill keeps running.
bash start.sh --no-wait
open "http://localhost:8000" 2>/dev/null || true
echo ""
echo "   ✓ Anthill is running. You can close this window - it keeps running."
echo "     Next time: open Anthill from Applications (no Terminal)."
echo ""
read -r -p "   Press Return to close this window." _ || true
