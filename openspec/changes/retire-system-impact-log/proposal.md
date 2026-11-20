# Retire the manual System Impact Log: keep it as history and stop the documentation agent proposing entries

Lane: `pillar:platform`. Priority: medium. Category: enhancement.
Full spec, added by the PR that builds this: `docs/specs/retire-system-impact-log.md`.
Issue #308. Decided by the maintainer on 2026-11-19.

## Why

The log has had no entry since 2026-10-09 while 112 PRs merged. Specs, OpenSpec folders, changelog fragments and PR
descriptions record each change better. The process still demands an entry and the documentation agent still produces
one for every merged PR, which nobody pastes.

## What changes

Freeze the log as history; remove the requirement from `AGENTS.md`, the PR template, the process documents, the recipes and
the `.asdd.yml` comments; make the documentation agent propose the changelog line and the untrue doc sentences only (its
recipe, renderer, script and tests); add dated notes to the two doc agent specs.

## Guardrails (do NOT touch)

- No log entry is edited or deleted; no file moves.
- The agent's model, endpoint, runner, audit record and post-merge trust boundary.
- The generic `impact_log` field of `cli/conventions-check.py`; the reviewer's "impact" lens;
  `.claude/agents/desktop-smoke-test.md`.

## Impact

About twenty files, no product code. `.github/**` is a protected path: a named human reviewer is required.
