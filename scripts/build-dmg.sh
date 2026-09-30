#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Build Anthill-DevBuild.dmg - a drag-to-Applications installer disk image for the dev/
# fallback build (see build-app.sh's header for what that is and why it's not the official
# release - deliberately NOT named Anthill.dmg, which is the real Tauri release's filename).
#
# Opens to a window with Anthill-DevBuild.app next to an Applications shortcut, so the user
# drags the icon across - the standard Mac install gesture.
#
# Code-signing + notarization are CONDITIONAL on credentials being present:
#   - MACOS_SIGN_IDENTITY  ("Developer ID Application: Onehill Foundation (TEAMID)")  -> codesign
#   - AC_API_KEY_ID / AC_API_ISSUER_ID / AC_API_KEY_PATH (App Store Connect)   -> notarize+staple
# Without them the build is UNSIGNED but still distributable: first launch is
# right-click → Open → Open (once). It upgrades to true one-click when the paid
# Apple Developer ID lands - no code change needed.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")/.."

bash scripts/build-app.sh
APP="dist/Anthill-DevBuild.app"

# Code-sign (only if a Developer ID identity is configured). PyInstaller bundles nested
# Mach-O (.so/.dylib + the bootloader), so sign INSIDE-OUT - every nested binary first, the
# .app bundle last - under hardened runtime with the entitlements an embedded Python needs.
# (`codesign --deep` is unreliable here and is discouraged by Apple for distribution.)
if [ -n "${MACOS_SIGN_IDENTITY:-}" ]; then
  # entitlements.plist grants the hardened-runtime exceptions an embedded CPython needs:
  # allow-unsigned-executable-memory + allow-jit (bytecode/ctypes/JIT pages), disable-library-
  # validation (load PyInstaller's collected unsigned libs and the bundled ollama), and
  # allow-dyld-environment-variables (the PyInstaller bootloader sets DYLD_*).
  # KEEP THAT FILE FREE OF XML COMMENTS: codesign's AMFI parser rejects them outright
  # ("Failed to parse entitlements: AMFIUnserializeXML: syntax error"), even though plutil
  # and ordinary XML parsers accept them.
  ENTITLEMENTS="scripts/entitlements.plist"
  echo "Signing $APP as: $MACOS_SIGN_IDENTITY"
  sign() {
    codesign --force --options runtime --timestamp \
      --entitlements "$ENTITLEMENTS" --sign "$MACOS_SIGN_IDENTITY" "$@"
  }
  # 1) Every nested Mach-O first (inside-out). A name/extension filter misses extension-less
  #    executables - the Python.framework "Python" binary and Ollama's runner binaries
  #    (llama-server, llama-quantize, ...) ship with their vendors' signatures, which pass a
  #    local `codesign --verify` but FAIL notarization ("not signed with a valid Developer ID",
  #    "no secure timestamp", "hardened runtime not enabled"). Notarization rejects ANY nested
  #    Mach-O that is not Developer-ID + hardened-runtime + timestamp signed, so detect by
  #    content (file type) rather than by name and re-sign each one.
  find "$APP/Contents" -type f -print0 \
    | while IFS= read -r -d '' f; do
        if file -b "$f" | grep -q "Mach-O"; then sign "$f"; fi
      done
  # 2) the bundle last (this signs the main executable too).
  sign "$APP"
  codesign --verify --deep --strict "$APP" && echo "✓ Signed (hardened runtime)"
else
  echo "(unsigned - set MACOS_SIGN_IDENTITY to code-sign; first launch needs right-click → Open)"
fi

STAGE="$(mktemp -d)"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
mkdir -p dist
rm -f dist/Anthill-DevBuild.dmg
hdiutil create -volname "Anthill (Dev Build)" -srcfolder "$STAGE" -ov -format UDZO dist/Anthill-DevBuild.dmg >/dev/null
rm -rf "$STAGE"

# Notarize + staple (only if signed AND App Store Connect API creds are present).
if [ -n "${MACOS_SIGN_IDENTITY:-}" ] && [ -n "${AC_API_KEY_ID:-}" ] \
   && [ -n "${AC_API_ISSUER_ID:-}" ] && [ -n "${AC_API_KEY_PATH:-}" ]; then
  # --timeout caps how long we hold the (10x-billed) macOS runner waiting on Apple. WITHOUT it,
  # an Apple Notary outage idles the runner for HOURS - a single uncapped wait once ran 4h40m and
  # cost ~$22. With it, a slow day fails fast and cheap; just re-run the Release once Apple's queue
  # recovers (the build is deterministic, so a re-run notarizes an equivalent artifact).
  echo "Notarizing dist/Anthill-DevBuild.dmg (a few minutes normally; capped at ${NOTARIZE_TIMEOUT:-20m})..."
  if ! xcrun notarytool submit dist/Anthill-DevBuild.dmg \
       --key "$AC_API_KEY_PATH" --key-id "$AC_API_KEY_ID" --issuer "$AC_API_ISSUER_ID" \
       --wait --timeout "${NOTARIZE_TIMEOUT:-20m}"; then
    echo "::error::Notarization did not finish within ${NOTARIZE_TIMEOUT:-20m} - Apple's Notary queue is likely backed up. This is NOT a build error; re-run the Release once https://developer.apple.com/system-status/ shows Notary Service healthy." >&2
    exit 1
  fi
  xcrun stapler staple dist/Anthill-DevBuild.dmg
  echo "✓ Notarized + stapled (true one-click install)."
else
  echo "(not notarized - set AC_API_KEY_ID/AC_API_ISSUER_ID/AC_API_KEY_PATH to notarize)"
fi

echo "✓ Built dist/Anthill-DevBuild.dmg (not the official release - see build-app.sh's header)"
echo "  Open it, drag Anthill (Dev Build) into Applications, then launch from Applications."
