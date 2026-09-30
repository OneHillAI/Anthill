#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Build the Anthill Appliance installer (.pkg): a double-click that configures THIS
# Mac as the always-on org backend (never sleep, auto-restart, the LaunchAgent serving
# on 0.0.0.0, and the model pulled). It is a *scripts-only* package - no app payload, so
# it builds in a second and stays tiny. Install the Anthill app first; this just makes it
# always-on.
#
# Signing is OPTIONAL. For a Mac you control (in your office) an unsigned package is fine -
# the installer just asks the admin to allow it. To distribute it outside your machines,
# set INSTALLER_ID to a "Developer ID Installer" identity and it will be signed.
#
#   bash scripts/build-appliance-pkg.sh                       # unsigned -> dist/Anthill-Appliance.pkg
#   INSTALLER_ID="Developer ID Installer: Acme (TEAMID)" \
#     bash scripts/build-appliance-pkg.sh                     # signed
#   VERSION=2026.06.12 bash scripts/build-appliance-pkg.sh    # pin the version
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PKG_SRC="$PROJECT_DIR/scripts/appliance-pkg"
DIST="$PROJECT_DIR/dist"
IDENT="org.onehill.anthill.appliance"
VERSION="${VERSION:-$(date +%Y.%m.%d 2>/dev/null || echo 1.0)}"

if [ "$(uname)" != "Darwin" ]; then
  echo "macOS only (needs pkgbuild/productbuild)." >&2
  exit 1
fi

mkdir -p "$DIST"
chmod +x "$PKG_SRC/scripts/postinstall"

# 1. Component package: no payload, just the postinstall script.
pkgbuild \
  --nopayload \
  --identifier "$IDENT" \
  --version "$VERSION" \
  --scripts "$PKG_SRC/scripts" \
  "$DIST/appliance-component.pkg"

# 2. Product archive: the user-facing installer (welcome screen + the component).
product_args=(--distribution "$PKG_SRC/distribution.xml" --package-path "$DIST")
if [ -n "${INSTALLER_ID:-}" ]; then
  product_args+=(--sign "$INSTALLER_ID")
fi
product_args+=("$DIST/Anthill-Appliance.pkg")
productbuild "${product_args[@]}"

rm -f "$DIST/appliance-component.pkg"
echo "built $DIST/Anthill-Appliance.pkg (version $VERSION)${INSTALLER_ID:+, signed}"

# 3. Notarize + staple (only if signed AND App Store Connect API creds are present), so a
#    downloaded .pkg installs without a Gatekeeper warning. Mirrors scripts/build-dmg.sh.
if [ -n "${INSTALLER_ID:-}" ] && [ -n "${AC_API_KEY_ID:-}" ] \
   && [ -n "${AC_API_ISSUER_ID:-}" ] && [ -n "${AC_API_KEY_PATH:-}" ]; then
  # See scripts/build-dmg.sh: --timeout caps the 10x macOS-runner wait so an Apple Notary outage
  # can't idle-bill for hours. Fails fast + cheap; re-run the Release once Apple recovers.
  echo "Notarizing $DIST/Anthill-Appliance.pkg (a few minutes normally; capped at ${NOTARIZE_TIMEOUT:-20m})..."
  if ! xcrun notarytool submit "$DIST/Anthill-Appliance.pkg" \
       --key "$AC_API_KEY_PATH" --key-id "$AC_API_KEY_ID" --issuer "$AC_API_ISSUER_ID" \
       --wait --timeout "${NOTARIZE_TIMEOUT:-20m}"; then
    echo "::error::Appliance pkg notarization did not finish within ${NOTARIZE_TIMEOUT:-20m} - Apple's Notary queue is likely backed up. Re-run the Release once Apple recovers." >&2
    exit 1
  fi
  xcrun stapler staple "$DIST/Anthill-Appliance.pkg"
  echo "notarized + stapled (installs without a warning)."
else
  echo "(not notarized - set INSTALLER_ID + AC_API_KEY_ID/AC_API_ISSUER_ID/AC_API_KEY_PATH to notarize)"
fi
