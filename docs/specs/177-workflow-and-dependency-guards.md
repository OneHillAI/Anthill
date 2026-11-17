# Spec: Deterministic guards for workflow hygiene and dependency bounds

Status: in progress (first part). Lane: `pillar:platform`. Issue: #177.

## Problem

The security review lens is asked to check two mechanical properties by reading a diff: whether a change under
`.github/` expands untrusted input into a shell script or leaves an action on a movable tag, and whether a
dependency change keeps the bounds `pyproject.toml` promises. A model reading diffs misses drift that a parser
catches every time. Today:

- Only the four "Detect docs-only change" steps are tested against `${{ ... }}` expansion in `run:`. The other
  workflows are not.
- Some `uses:` references are pinned to a full commit SHA while their neighbours sit on `@v4`, `@v5` or
  `@stable`, including `actions/create-github-app-token@v1`, which handles the bot's App credentials.
- `pyproject.toml` states that every base runtime dependency has a floor and an upper bound (except `cryptography`
  and `certifi`), and `requirements.lock` is hash-pinned from it. Nothing enforces either statement.

## Requirements

1. A test fails when a base dependency in `pyproject.toml` lacks a `>=` floor or an upper bound, other than the
   documented `cryptography` and `certifi`, which still need a floor. The exception list fails if an entry leaves
   `pyproject.toml`.
2. A test fails when a pin in `requirements.lock` falls outside the range `pyproject.toml` declares, when a base
   dependency is missing from the lock, or when any lock pin has no `--hash=sha256:` entry. Dependencies gated by
   an environment marker that does not apply to the running platform are skipped.
3. A test fails when any workflow in `.github/workflows/` expands an attacker-controlled GitHub context (PR or
   issue text, head branch or ref, commit and comment bodies) inside a `run:` script.
4. A test fails on any other new `${{ ... }}` inside a `run:` script unless it is on a reviewed list with a reason.
   The list holds what exists today: tag pushes by maintainers, the PR's base branch, a boolean dispatch input and
   step outputs.
5. A test fails when a workflow gains an action reference pinned to a tag or branch. The references that exist
   today are counted per file and can only go down: the test also fails when a file has fewer than its baseline,
   so the baseline is lowered with the change that pins them.
6. All of this is stdlib plus `pyyaml` and `packaging`, runs offline, and adds no dependency.

## Known limits

- The baseline records what is unpinned today; it does not pin it. Resolving those tags to commit SHAs is a
  separate change that edits protected CI files and needs the SHAs verified against upstream.
- The untrusted-context list is a denylist of the well-known cases. It is not a replacement for a workflow
  linter, which also checks syntax, permissions and `pull_request_target` hazards.
- `actionlint` and `zizmor` are not part of this change: adding them to CI needs the tools pinned and installed
  in a workflow, which is decided separately.

## Acceptance

- On `main` today the tests pass.
- Adding `${{ github.event.pull_request.title }}` to any `run:` fails requirement 3.
- Adding an unreviewed `${{ ... }}` to a `run:` fails requirement 4.
- Changing a `uses:` in a clean file such as `ci.yml` to `@v4` fails requirement 5; pinning one baseline entry
  without lowering the baseline fails it too.
- Dropping `<1.0` from `httpx`, raising `rich` above its lock pin, adding a dependency the lock lacks, or removing
  all hashes from one lock pin each fail requirements 1 and 2.
- Tests: `tests/test_supply_chain_bounds.py` and `tests/test_workflow_hygiene.py` (model-free).
