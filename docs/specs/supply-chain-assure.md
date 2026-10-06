# Spec: Supply-chain Assure layer (SBOM, provenance, signing)

Status: partial (this PR does the CI-verifiable half). Lane: `pillar:platform`. Issue: #355.

## Problem

The dogfood conformance scorecard flagged the **Assure layer** as a gap: `release.yml` only Apple
code-signs the app, with no build provenance, no SBOM, no attestation, and base Python dependencies
declared with open-ended version ranges. This maps to OWASP ASI04 / LLM03 and the governance Assure
layer (`standards/assure.md`).

## Scope of this PR (verifiable in CI now)

- **SBOM.** `.github/workflows/sbom.yml` generates a CycloneDX (JSON) software bill of materials of the
  app's installed runtime dependency set with `cyclonedx-py environment`, and uploads it as a build
  artifact. It runs on dependency changes, on `main`, and on demand (path-filtered + concurrency-capped
  to respect CI cost), so the generation is exercised by CI on this PR itself. The release workflow can
  later download and attach this artifact to the GitHub Release. (Update 2026-10-06: the job now installs
  the pinned, hash-checked `requirements.lock`, the set the packaged app ships, instead of resolving
  `pyproject.toml`, and it also runs when the lock changes. `supply-chain.yml` separately covers the built
  sdist and wheel.)
- **Bounded dependencies.** Every base runtime dependency in `pyproject.toml` now carries an upper bound
  at the next major in addition to its floor, so an untested major release cannot silently enter a
  build. Bounds sit above the versions we currently resolve (verified: all installed versions satisfy
  the new specifiers), so nothing is forced to change. Two are intentionally left unbounded and marked
  so in-line: `cryptography` and `certifi` are security-critical and must be free to take forward
  updates (a new major, or a fresh CA bundle) without a release edit.

## Deferred to the owner-verified release pass (tracked, not in this PR)

These touch protected release paths and cannot be honestly verified without a real tag/release run
(which re-bills Apple), so they are deliberately left for a follow-up the owner drives:

- **SLSA build provenance** for the `.dmg` / `.pkg` via `actions/attest-build-provenance` in
  `release.yml` (needs `id-token: write` and the actual built artifacts).
- **Sigstore/cosign** signing of the release artifacts, in addition to Apple notarisation.
- **Require the attestation** on agent-authored PRs once the integrity-attestation format is final.

## Acceptance criteria

- The `sbom` workflow runs on this PR and produces a valid CycloneDX SBOM artifact with a non-empty
  component list (the job asserts `bomFormat == CycloneDX` and `components > 0`).
- `pip install -e .` still resolves under the new bounds; the full test suite is unaffected.
- The deferred items above are recorded here and remain open on #355.
