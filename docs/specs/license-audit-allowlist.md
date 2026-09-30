# Spec: License-audit allowlist for known-but-unclassified dependencies

Status: implemented. Lane: `chore` (CI governance). Issue: #489.

## Problem

The `Security audit` workflow's License-audit job fails the build when any installed package has an
`UNKNOWN` license (no metadata `pip-licenses` can read). That is the right default - an undeterminable
license must be reviewed before distribution. But some packages ship **no license classifier** while
having a **known** license, so they false-fail the gate. `cuda-toolkit` (a transitive, Linux/CUDA-only
build dep of `torch`, not shipped in any Anthill artifact) reddened `main` this way.

## Policy

- Maintain a small **allowlist** in the gate: normalized package name -> its actual, reviewed license.
  An allowlisted package emits a `::notice::` recording the license instead of failing.
- A `UNKNOWN`-licensed package that is **not** on the allowlist still **hard-fails** the build.
- The allowlist lives in `.github/workflows/security-audit.yml`, a **protected path**, so every addition
  requires a founding-contributor review - a license can't be waved through without human sign-off. This
  **resolves** (documents the real license) rather than **suppresses**, matching the AGPL + commercial +
  foundation licensing posture where an undeterminable dependency license is worth pinning down.

## Acceptance criteria

- A `cuda-toolkit` (or `cuda_toolkit`) `UNKNOWN` entry passes the gate with a notice; the job exits 0.
- A different, non-allowlisted `UNKNOWN` package still fails the gate (exit 1).
- Adding to the allowlist requires editing a protected path (review-gated).
- Covered by `tests/test_license_gate.py`, which runs the actual gate script against synthetic reports.
