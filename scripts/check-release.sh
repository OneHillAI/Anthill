#!/usr/bin/env bash
# Release gate: a version can never be tagged/published without being changelogged.
#
# Given a release tag (vX.Y.Z), this asserts that:
#   1. CHANGELOG.md has a matching "## [X.Y.Z]" section, and
#   2. pyproject.toml's version equals X.Y.Z.
# With --release (only the release workflows pass it), it additionally asserts:
#   3. no changelog.d/ fragment was left unassembled.
# Check 3 is release-cut-only on purpose: between releases, changelog.d/ legitimately holds pending
# fragments for the NEXT version, so enforcing it during PR-time validation (the test suite runs this
# gate against the current version) would fail on the normal steady state (issue #522). At an actual
# release cut the human assembles fragments with scripts/build_changelog.py first, so none remain.
# The Release workflow runs this before building the dmg, so a missing changelog entry, a version
# mismatch, or a forgotten fragment fails the release in seconds instead of shipping a bad build.
#
# Usage: scripts/check-release.sh [--release] v0.1.2   (the leading "v" is optional)
set -euo pipefail
cd "$(dirname "$0")/.."

release_mode=0
if [ "${1:-}" = "--release" ]; then
  release_mode=1
  shift
fi

TAG="${1:?usage: check-release.sh [--release] <tag, e.g. v0.1.2>}"
VER="${TAG#v}"
VER_RE="$(printf '%s' "$VER" | sed 's/\./\\./g')" # escape dots for the regex

fail=0

if ! grep -qE "^## \[${VER_RE}\]" CHANGELOG.md; then
  echo "::error::CHANGELOG.md has no '## [${VER}]' section. Add a changelog entry for ${VER} before releasing." >&2
  fail=1
fi

PYVER="$(grep -E '^version[[:space:]]*=' pyproject.toml | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
if [ "${PYVER}" != "${VER}" ]; then
  echo "::error::pyproject.toml version (${PYVER}) does not match release ${VER}. Bump it before releasing." >&2
  fail=1
fi

# Release-cut only: fragments must have been assembled into the release section, not left behind. Any
# remaining changelog.d/<id>.<category>.md means the cut forgot to run scripts/build_changelog.py.
if [ "${release_mode}" -eq 1 ] &&
  git ls-files 'changelog.d/*.md' | grep -qE '\.(added|changed|deprecated|removed|fixed|security)\.md$'; then
  echo "::error::changelog.d/ has unassembled fragments. Run scripts/build_changelog.py ${VER} <date> before releasing." >&2
  fail=1
fi

if [ "${fail}" -ne 0 ]; then
  exit 1
fi
echo "Release check OK: CHANGELOG has [${VER}] and pyproject version matches."
