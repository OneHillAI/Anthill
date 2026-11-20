# Spec: retire the manual System Impact Log

Status: implemented. Lane: `pillar:platform`. Issue: #308. Decided by the maintainer on 2026-11-19. Follows the daily docs
check of 2026-11-17. Touches `.github/**`, which is a protected path: a named human reviewer is required.

## Problem

`docs/SYSTEM_IMPACT_LOG.md` was a hand-kept entry per merged PR. Its newest entry is dated 2026-10-09; 112 PRs have merged
since (79 in November). Every PR already leaves a better record: a spec (`docs/specs/`), an OpenSpec change folder
(`openspec/changes/`), a changelog fragment and a PR description with its "Not verified" section. Yet `AGENTS.md`, the PR
template, the process documents and the recipes still said an entry was required, and the documentation agent produced
one for every merged PR (the `impact` object was its main output; the renderer returned nothing without it). Nobody
pasted them.

## Requirements

1. `docs/SYSTEM_IMPACT_LOG.md` SHALL be frozen as history: every entry stays and none is edited; its introduction says it
   is frozen as of 2026-10-09 and names what records a change now (spec and OpenSpec change, changelog fragment, PR
   description) and where user-facing behaviour is described (the guide pages under `docs/`).
2. `AGENTS.md`, `.github/PULL_REQUEST_TEMPLATE.md`, `docs/asdd-anthill-process.md`, `docs/asdd-goose-adoption.md`,
   `recipes/README.md`, `recipes/developer.yaml` and the `.asdd.yml` comments SHALL NOT ask for an impact-log entry; the
   changelog fragment, the spec and the guide pages are what a change updates.
3. The documentation agent SHALL propose the changelog line and the doc sentences that are now untrue, and nothing about an
   impact log:
   - `recipes/documentation.yaml` has no `impact` object or footprint rule, and requires `doc_edits` (an empty list when
     nothing is untrue);
   - `docsync-render.py` has no impact entry, field limits, footprint check or month heading; a result is a proposal only
     if it has `changelog` or `doc_edits`, otherwise it renders nothing (exit 3, "no usable proposal"); a proposal with
     no changelog wanted, no untrue sentence and nothing to check renders one line, "No documentation changes
     proposed"; otherwise it renders "1. Changelog" and "2. Docs that are now untrue";
   - the checks that stay: the changelog fragment is wanted only when product code changed, never for a release cut,
     is named `changelog.d/<PR>.<category>.md` with an allowed category and has no heading or dash; a doc edit is kept
     only if its sentence is in its file; en and em dashes become hyphens; numbers the PR does not mention are flagged,
     now in the changelog text and in a doc fix (against the sentence the fix replaces);
   - `docsync.sh` no longer passes `--impact-log` and says "no usable proposal".
4. `tests/test_asdd_docsync.py` SHALL test what remains and SHALL fail if the words "impact log" return to the recipe,
   the script or the renderer.
5. `docs/specs/asdd-docs-agent.md` and `docs/specs/operate-docsync.md` SHALL carry a dated note that the impact part is
   superseded; their history is not rewritten.
6. The agent's model, endpoint, runner, audit record and trust boundary (post-merge only, never on an open PR) SHALL NOT
   change.

## Known limits

- `.claude/agents/desktop-smoke-test.md` still reads the old entries for a list of items that "still need a real desktop
  build to confirm"; that list will not grow from the log any more. It is left alone.
- `cli/conventions-check.py` keeps its generic optional `impact_log` field (the shared ASDD framework); Anthill does not
  declare it.
- The review lenses named "impact" (`cli/audit.py`, `tests/test_asdd_honest_status.py`) are the reviewer's, not this log, and
  are untouched.
- The renderer was run by hand on a proposal with a changelog and a doc fix, on a result with nothing to do, and on an
  old-shape result; it has not yet run on a real merged PR. The first real comment is the check.

## Acceptance

- `tests/test_asdd_docsync.py` passes, with the new tests; the workflow test still shows it runs after merge only.
- A search of the process files, the PR template, the recipes and `.asdd.yml` finds no instruction to add an impact-log
  entry; the log's introduction says it is frozen.
- The full suite and CI pass; the next documentation comment on a merged PR has the two sections (or the one line).
