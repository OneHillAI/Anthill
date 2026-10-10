# Spec: CI runs with a read-only token

Status: implemented. Lane: `chore`.

## Problem

`.github/workflows/ci.yml` had no `permissions` block, so its jobs ran with the repository's default
`GITHUB_TOKEN` scope. The lint, test and browser jobs only read the code, run linters and run tests.

## Requirements

- The workflow sets `permissions: contents: read` once, at the top.
- No job in the workflow widens it.
- Nothing else about the workflow changes: same triggers, jobs, steps and pinned actions.

## Acceptance criteria

- `tests/test_ci_workflow_permissions.py` parses the workflow: the default is exactly `contents: read`, no
  job asks for write access, and the `lint`, `test` and `browser` jobs are still present.
- The CI jobs still pass with the narrower token.

`.github/**` is a protected path, so a named code-owner review is required.
