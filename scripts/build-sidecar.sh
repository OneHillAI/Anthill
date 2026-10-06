#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Build the `anthill-server` Tauri sidecar: a ONE-FILE binary of the backend.
#
# The Tauri desktop shell (src-tauri/) bundles this as an `externalBin` and spawns
# it headless (ANTHILL_NO_BROWSER=1). Tauri requires the sidecar file to be named
# `<name>-<rust-target-triple>` (e.g. anthill-server-aarch64-apple-darwin), so this
# builds with PyInstaller (Anthill-sidecar.spec) and copies the result into
# src-tauri/binaries/ under that name. Run on each target OS/arch in release CI.
#
# Also runs on Windows, from Git Bash (the windows-build workflow does this): the binary is then
# anthill-server.exe and Tauri wants anthill-server-x86_64-pc-windows-msvc.exe.
#
# Requires: Python 3.11 (uv when present, else python3) and a Rust toolchain (for
# `rustc` to report the host target triple). PyInstaller is a build-time tool only.
# Heavy ML libs are excluded (same as the .app build); Ollama is NOT bundled here -
# the Tauri app supplies the local engine separately.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")/.."

# Rust target triple Tauri expects in the sidecar filename. Allow an override (cross-builds /
# CI matrices), else read the host triple from rustc.
TRIPLE="${TAURI_TARGET_TRIPLE:-}"
if [ -z "$TRIPLE" ]; then
  if command -v rustc >/dev/null 2>&1; then
    TRIPLE="$(rustc -Vv | sed -n 's/^host: //p')"
  fi
fi
if [ -z "$TRIPLE" ]; then
  echo "✗ could not determine the Rust target triple (install Rust, or set TAURI_TARGET_TRIPLE)" >&2
  exit 1
fi

# Windows binaries (and Tauri's sidecar names for them) end in .exe.
EXE=""
case "$TRIPLE" in *windows*) EXE=".exe" ;; esac

# Throwaway build venv. Runtime deps come from the pinned, hash-verified lockfile
# (requirements.lock, which covers base + the docs and mcp extras) for a reproducible,
# tamper-evident bundle; the anthill package installs --no-deps on top; PyInstaller is a
# build-time tool only. Regenerate the lock with `uv pip compile --universal --python-version 3.11
# --generate-hashes --extra docs --extra mcp pyproject.toml -o requirements.lock` when deps change
# (keep --python-version 3.11, the packaged app's Python; see CONTRIBUTING.md).
BUILD_DIR="$(mktemp -d)"; BUILD_VENV="$BUILD_DIR/venv"
if command -v uv >/dev/null 2>&1; then
  uv venv --python 3.11 "$BUILD_VENV"
  PY="$BUILD_VENV/bin/python"; [ -x "$PY" ] || PY="$BUILD_VENV/Scripts/python.exe"
  uv pip install --python "$PY" --require-hashes -r requirements.lock
  uv pip install --python "$PY" --no-deps .
  uv pip install --python "$PY" pyinstaller
else
  (python3.11 -m venv "$BUILD_VENV" 2>/dev/null || python3 -m venv "$BUILD_VENV")
  PY="$BUILD_VENV/bin/python"; [ -x "$PY" ] || PY="$BUILD_VENV/Scripts/python.exe"
  "$PY" -m pip install -U pip >/dev/null
  "$PY" -m pip install --require-hashes -r requirements.lock
  "$PY" -m pip install --no-deps .
  "$PY" -m pip install pyinstaller
fi

# Build the one-file binary.
rm -rf build "dist/anthill-server$EXE"
"$PY" -m PyInstaller --noconfirm Anthill-sidecar.spec
if ! "dist/anthill-server$EXE" --selfcheck-office; then
  echo "✗ sidecar export/PDF-ingest smoke test FAILED - bundled runtime data is incomplete" >&2
  exit 1
fi
# The semantic cache's native stack (lancedb + pyarrow) must survive a worker thread inside the FROZEN
# binary: Arrow's mimalloc allocator segfaulted there and killed every chat turn. A native crash exits
# nonzero here, so a sidecar like that fails the build instead of shipping.
if ! "dist/anthill-server$EXE" --selfcheck-cache; then
  echo "✗ sidecar semantic-cache check FAILED - the frozen build is not on the safe Arrow allocator, or its lancedb/pyarrow stack is broken" >&2
  exit 1
fi
# End to end: boot the frozen binary, sign up, and run one real chat against a small fake Ollama (no
# model or network needed). Catches a dead frozen chat from broken routes, templates, or bundled data.
if ! "$PY" scripts/smoke_frozen_chat.py "$PWD/dist/anthill-server$EXE"; then
  echo "✗ frozen sidecar chat smoke test FAILED - chat does not work in the packaged binary" >&2
  exit 1
fi
rm -rf "$BUILD_DIR"

# Place it where Tauri's externalBin expects it.
DEST="src-tauri/binaries/anthill-server-${TRIPLE}${EXE}"
mkdir -p src-tauri/binaries
cp "dist/anthill-server$EXE" "$DEST"
chmod +x "$DEST"

echo "✓ Built sidecar $DEST"
echo "  Tauri (tauri.conf.json externalBin: binaries/anthill-server) bundles this and spawns it headless."
