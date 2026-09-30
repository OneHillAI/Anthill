#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Build a SELF-CONTAINED dev/fallback build (macOS) with PyInstaller: dist/Anthill-DevBuild.app.
#
# This is NOT the official Anthill release - that's the native, auto-updating Tauri build
# (src-tauri/), published from anthill.run/download. This one bundles a Python runtime + the
# anthill package + its dependencies so it runs on a CLEAN Mac with no repo clone, no system
# Python, and no install.command - useful for testing on a machine without the Rust/Tauri
# toolchain - but it has no native window (anthill/desktop.py opens the system browser
# instead) and does not auto-update. User data lives in the same place either way:
#   ~/Library/Application Support/Anthill/   (see anthill/desktop.py)
#
# Requires: macOS, Python 3.11 (uv is used when present, else python3). PyInstaller
# is installed into a throwaway build venv here - it is a build-time tool, not a
# runtime dependency. Heavy ML libs (torch/sentence-transformers) are NOT bundled
# (not installed here, and excluded in Anthill.spec) to keep the dmg small; semantic
# search degrades to keyword search until the first-run lazy-download lands.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")/.."

# 1) App icon (.icns) from the brand mark via built-in sips/iconutil (the spec uses it).
# Regenerate when it's missing OR the mark is newer, so a brand refresh actually ships
# (the .icns is gitignored; rebuilt from anthill-mark.png, which scripts/gen_icons.py emits).
ICON_SRC="assets/anthill-mark.png"
if [ -f "$ICON_SRC" ] && command -v iconutil >/dev/null 2>&1 \
   && { [ ! -f assets/anthill.icns ] || [ "$ICON_SRC" -nt assets/anthill.icns ]; }; then
  ICONSET="$(mktemp -d)/anthill.iconset"; mkdir -p "$ICONSET"
  for s in 16 32 128 256 512; do
    sips -z "$s" "$s"         "$ICON_SRC" --out "$ICONSET/icon_${s}x${s}.png"    >/dev/null 2>&1 || true
    sips -z $((s * 2)) $((s * 2)) "$ICON_SRC" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null 2>&1 || true
  done
  iconutil -c icns "$ICONSET" -o assets/anthill.icns 2>/dev/null || true
  rm -rf "$(dirname "$ICONSET")"
fi

# 2) Throwaway build venv: runtime deps + the anthill package + PyInstaller.
BUILD_DIR="$(mktemp -d)"; BUILD_VENV="$BUILD_DIR/venv"
if command -v uv >/dev/null 2>&1; then
  uv venv --python 3.11 "$BUILD_VENV"
  PY="$BUILD_VENV/bin/python"
  uv pip install --python "$PY" ".[docs,mcp]" pyinstaller
else
  (python3.11 -m venv "$BUILD_VENV" 2>/dev/null || python3 -m venv "$BUILD_VENV")
  PY="$BUILD_VENV/bin/python"
  "$PY" -m pip install -U pip >/dev/null
  "$PY" -m pip install ".[docs,mcp]" pyinstaller
fi

# 2.5) Bundle the Ollama runtime (the local-model engine) so the app works with NOTHING
# else installed - no separate Ollama download. Pinned + checksum-verified, extracted into
# build-ollama/, which Anthill.spec ships as ollama-runtime/ (find_ollama_bin prefers it).
# ~137 MB download / ~450 MB on disk (the engine + its GPU/runner libs). Set BUNDLE_OLLAMA=0
# for a lean dev build (then the local model needs a separately installed Ollama).
OLLAMA_VERSION="0.30.10"
OLLAMA_SHA256="ad8a4d2918ed09480b8160419570602b4f49e48c9e3792efb601c0f54619e48e"
rm -rf build-ollama
if [ "${BUNDLE_OLLAMA:-1}" = "1" ]; then
  echo "→ fetching Ollama ${OLLAMA_VERSION} to bundle (~137 MB)…"
  OLLAMA_TGZ="$BUILD_DIR/ollama-darwin.tgz"
  curl -fsSL -o "$OLLAMA_TGZ" \
    "https://github.com/ollama/ollama/releases/download/v${OLLAMA_VERSION}/ollama-darwin.tgz"
  echo "${OLLAMA_SHA256}  ${OLLAMA_TGZ}" | shasum -a 256 -c - \
    || { echo "✗ Ollama checksum mismatch - aborting the build" >&2; exit 1; }
  mkdir -p build-ollama
  tar -xzf "$OLLAMA_TGZ" -C build-ollama
  chmod +x build-ollama/ollama
  echo "✓ Ollama ${OLLAMA_VERSION} staged in build-ollama/ (bundled as ollama-runtime/)"
else
  echo "(BUNDLE_OLLAMA=0 - no bundled engine; the local model will need a separate Ollama)"
fi

# 3) Build the self-contained bundle.
rm -rf build dist/Anthill-DevBuild.app
"$PY" -m PyInstaller --noconfirm Anthill.spec
rm -rf "$BUILD_DIR"

# 4) Smoke-test the PACKAGED app: create one of each file-export format with the bundled libraries.
# This runs the frozen binary (not the dev env), so a data template PyInstaller dropped - e.g.
# python-pptx/python-docx open a default template at runtime - fails the build here, not a user download.
echo "-> Office-export smoke test (packaged app)"
if ! "dist/Anthill-DevBuild.app/Contents/MacOS/Anthill" --selfcheck-office; then
  echo "✗ export/PDF-ingest smoke test FAILED - the bundle is missing runtime data (check Anthill.spec collect_all)" >&2
  exit 1
fi

echo "✓ Built dist/Anthill-DevBuild.app (self-contained dev/fallback build - not the official release)"
echo "  Install: drag into /Applications, or run: make dmg"
echo "  User data: ~/Library/Application Support/Anthill/"
echo "  The official, native, auto-updating build ships from anthill.run/download instead."
