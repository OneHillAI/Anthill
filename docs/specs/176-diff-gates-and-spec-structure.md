# Spec: Mechanical checks move out of the AI lenses and into deterministic gates

Status: implemented. Lane: `pillar:platform`. Issue: #176.

## Problem

The four AI review lenses were asked to check properties a parser checks faster, cheaper and the same way every
time: blind `except`, commented-out code and over-long functions; whether a change to Python product code comes
with a test; whether a new spec has the sections a reviewer needs; and whether the spec lens can see the spec a
PR claims to implement. A linked existing spec was invisible to the lens (it only saw `changes.diff`), so it
called the spec unverifiable; `docs/specs/mandatory-spec-gate.md` lists that as a known follow-up.

Measured against the last 100 merged PRs (#56 to #211) and the tree, before choosing thresholds:

- Whole-tree findings for the strict rules today: 508 (BLE001 373, C901 64, PLR0912 34, PLR0915 33, ERA001 4).
  Turning them on globally would fail the build, so they apply to added lines only.
- Of 57 merged PRs that touched `anthill/`, 15 (26%) had a finding on a line they added: 22 blind excepts and 3
  new functions over the complexity limit (11, 11 and 13 against 10). Commented-out code, too many branches and
  too many statements found nothing.
- Of 49 non-chore PRs that changed `anthill/**/*.py`, 48 already changed a test; #142 (a typing-only fix) did not.
- Of 75 newly added specs, 1 would fail the structure check below; 39 of the 214 specs in the tree would, which is
  why only new specs are checked.

## Requirements

1. On a pull request, `ci.yml` SHALL run ruff's BLE001, ERA001, C901, PLR0912 and PLR0915 over `anthill/` and keep
   only the findings on lines the PR added (`diff-lint.py`). Findings on legacy lines, and in files the PR did not
   touch, never count. C901 and the PLR rules report at the `def` line, so a change inside an old complex function
   is not flagged and a new complex function is.
2. ERA001, C901, PLR0912 and PLR0915 on an added line SHALL fail the step. BLE001 SHALL be reported as a warning
   on the line and not fail it: broad `except` is a deliberate graceful-degradation pattern in this codebase.
   Dropping `--warn BLE001` from the step makes it blocking.
3. A deliberate broad `except` SHALL be annotatable with `# noqa: BLE001 - <why>`. The five codes are listed under
   `lint.external` in `pyproject.toml` so the main ruff run does not report that comment as an unused directive
   (RUF100); they are not enabled globally.
4. `pr-validation.yml` SHALL fail a PR that changes `anthill/**/*.py` without adding or changing anything under
   `tests/`, unless it is in the chore lane, from a machine author, or a release cut (the same release-cut rule as
   the changelog gate, loaded from `operate/docsync-render.py`). The message says what to add and that a
   no-behaviour change belongs in the chore lane. It does not judge the test; the review does.
5. Intake SHALL check each spec a PR ADDS (the workflow copies each added direct child of `docs/specs/` with a plain
   name into a data directory) for a problem statement (Problem, Introduction, Background, Context, Why, Summary,
   Motivation or Overview), a description of the change (Requirements, Fix, Design, Plan, Decisions, Behaviour,
   Approach, Changes, Goals, What, Rules, Specification, Solution, Proposal or How) and a way to verify it
   (Acceptance, Tasks, Done when, Verification, Tests, Proof, Checks, Criteria or Known limits), as `##` or `###`
   headings, numbered or not, in any case. A missing section is named in the failure. A spec only edited, not
   added, is not checked. The result JSON records `spec_structure_ok`.
6. For the review, `linked-specs.sh` SHALL copy each existing `docs/specs/*.md` the PR description names, from the
   trusted base checkout, into the review workdir, and `generic.sh` SHALL put it inside the randomized fenced
   untrusted block under "Linked specs". A reference counts only if it is a plain direct child of `docs/specs/`
   (no `..`, no nesting, not a symlink) that exists in the base checkout; each spec is capped at 30000 bytes and
   the total at 60000. The spec lens instructions SHALL say where linked specs appear.
7. All of it is model-free and offline, adds no dependency, and takes PR text only through `env:` or data files.

## Decisions

- **Blind excepts warn, they do not block.** The data above is the reason: 22 across 13 PRs in about two months, against
  `ci.yml`'s own description of broad `except` as intentional. It is one flag to change.
- **Complexity uses ruff's defaults** (C901 over 10, PLR0912 over 12, PLR0915 over 50). Three recent PRs would have
  needed a split at 11 to 13; that is the bar the issue asked for on new code.
- **Test presence, not coverage.** The issue also proposed a diff-coverage floor with `pytest-cov` and
  `diff-cover`. That adds two dependencies and a coverage run to every PR; the file-presence floor catches the
  gap seen in practice (1 PR in 49) without them.
- **Only new specs are structure-checked**, and the accepted headings are the ones the corpus uses, so the
  bug-fix format (Problem, Fix, Verification) passes.

## Known limits

- Coverage of changed lines is not measured; a test change that does not exercise the new code satisfies the floor.
- A Python product file that is a static asset or a template is not covered by the test gate (only `.py`).
- Linked specs are the base-branch version; a spec the PR edits is in the diff instead.
- `.github/` and `pyproject.toml` are protected paths: this needs a named human reviewer.
- Not run on GitHub here: workflow edits are checked by tests that read the files, and the scripts by running them
  over synthetic and replayed data. After merge, watch the first PR that adds Python code and the first that adds
  a spec.

## Acceptance

- Measured when this was written (not re-run by a test): replaying the last 57 PRs that touched `anthill/`
  through the gate with real ruff gives the 25 findings above, all on added lines.
- A new blind `except` produces a warning on its line and a green step; a new commented-out block or complex
  function produces an error and a red one; the same `except` with `# noqa: BLE001 - why` produces nothing, and the
  main ruff run stays quiet about the comment.
- A PR changing `anthill/x.py` with no `tests/` change fails; with one, in the chore lane, from a machine author,
  or as a release cut, it passes; a docs-only, CI-only or template-only PR is not covered.
- A new spec missing any of the three sections fails with that section named; the specs added in the last 110
  merged PRs pass except one; real specs from the tree pass; an edited legacy spec is not checked.
- A linked existing spec appears in the prompt inside the fence, once; `..`, nested, missing and symlinked
  references, and anything over the caps, do not.
- Tests: `tests/test_diff_lint.py`, `tests/test_test_gate.py`, `tests/test_spec_structure_and_linked_specs.py`; the
  existing intake, trailer, machine-author, changelog-gate, workflow-hygiene, review-security and honest-status
  tests pass, and the local `gates.py` differential test still agrees.
