# Spec: Intake enforces the Agent trailer, the conventions check sees the diff, changelogs are gated before merge

Status: implemented. Lane: `pillar:platform`. Issue: #175.

## Problem

Three parts of the contribution contract were declared but not checked where they could stop a bad change:

1. `AGENTS.md` and `.asdd.yml` say every agent-authored commit carries `Agent: <name> (automated, instructed-by:
   <human>)`, but intake checked only DCO. Of the last 60 merged PRs (#115 to #210), eleven by the bot between #117 and #130 have
   bot commits without the trailer; none after #130 does. It was kept by habit, not by a gate.
2. `asdd-conventions.yml` called `conventions-check.py` without `--diff`. Without a diff the checker cannot see
   added lines, so a change that adds an en dash is reported CONFORMING (verified: the same input is caught with
   `--diff` and passes without it).
3. A changelog fragment was required before merge only for `pillar:feature` PRs. For other lanes the post-merge
   documentation agent proposed one after the merge, as paste-ready text for a human. In the last 60 merged PRs,
   of the 39 that changed `anthill/` outside the chore lane and release cuts, one (#121) shipped no fragment.

## Requirements

1. Intake SHALL read the `agents` list from the BASE commit's `.asdd.yml` (through `config-list.sh`, so a PR
   cannot edit its own list) and the author name and email of each non-merge commit, and:
   a. fail when a commit whose author name or email contains a listed identity (case-insensitive) lacks
      `Agent: <name> (automated, instructed-by: <human>)`;
   b. fail when the PR ticks the AI-agent box and no commit carries that trailer.
   A human commit in a mixed PR is not asked for a trailer. Without the author files or an agents list the rule
   is inert, so older callers are unchanged. Merge commits are excluded, as for DCO. The result JSON records
   `agent_trailer_ok`.
2. `asdd-conventions.yml` SHALL write the PR diff and pass it to the checker with `--diff`.
3. `pr-validation.yml` SHALL fail a PR that has no valid `changelog.d/<id>.<category>.md` fragment when it is a
   `pillar:feature` PR (as before) OR it changes product code (`anthill/`) and its lane is not `chore` and its
   author is not a machine author. The "product code changed" rule SHALL be the documentation agent's own
   `needs_changelog()`, loaded from `operate/docsync-render.py` by `changelog-gate.py`, so the two cannot drift: a
   release cut, and test-only, docs-only and CI-only changes, need no fragment.
4. The failure message SHALL say what to add and how to avoid it when nothing user-visible changed (the chore
   lane).
5. The PR title SHALL reach the gate through `env:`, never expanded into the shell, and the machine-author list
   SHALL come from the base branch's `.asdd.yml`.

## Decisions

- **The conventions job stays report-only.** The issue proposed removing `continue-on-error` once clean. It is not
  clean: the checker's own changelog rule wants a fragment on every PR outside the chore lane, including
  test-only and CI-only PRs, which the policy does not (this PR's siblings #205 to #208 are examples). Making it
  fail would put a red mark on most platform PRs for a rule nobody agreed to. The enforced rule is requirement 3,
  scoped to product code; the conventions report now also sees added lines.
- **The trailer is attribution, not authority.** A commit author name is forgeable by anyone who can push, so
  rule (a) catches an honest omission, not a determined one. That is the same standing as DCO.
- **Rule (b) asks for one trailer, not all.** The template says each agent commit carries it, and a PR can mix
  human and agent commits; rule (a) covers the configured agents commit by commit.

## Known limits

- A listed agent identity is matched as a substring of the commit author's name or email.
- The AI-agent box is recognised by a ticked line mentioning "AI" or "agent".
- A change to `anthill/` that has no user-visible effect must use the `chore` lane to skip the fragment; the lane
  is chosen by a person, as it is for every lane rule.
- Not run on GitHub here: the workflow edits are checked by tests that read the files, and the gates by running
  the real scripts over synthetic and replayed data. After merge, watch the first PR that changes `anthill/`.

## Acceptance

- Replaying real PRs through the new intake script: #210, #208, #204, #203 (bot) and #202, #173 (a human
  contributor) pass; #117, #121, #128 and #130 (the bot before it used the trailer) fail with the trailer problem.
- A bot commit with no trailer, a non-canonical trailer, and a ticked AI box with no trailer anywhere each fail;
  a human commit in a mixed PR, a human PR, an inert call, and a machine author's PR each pass.
- A product-code change with no fragment fails the changelog gate; with a fragment, in the chore lane, as a
  release cut, as a machine author, or touching only tests, docs or CI, it passes; a `pillar:feature` PR still
  needs a fragment. The gate agrees with `needs_changelog()` on every path set tested.
- With `--diff` the conventions checker catches an added en dash; without it, it does not.
- Tests: `tests/test_asdd_agent_trailer.py`, `tests/test_changelog_gate.py`; the existing intake, DCO,
  machine-author, owner-override and workflow-hygiene tests pass, and the local `gates.py` differential test
  still agrees.
