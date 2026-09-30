# Autonomous merge for safe-lane PRs

## Problem

Every change to `main`, however small, currently needs the founder to watch CI and click merge. That
does not scale and it is not where a founder's review adds value: a `chore` or `feature` PR that passes
the full test matrix and touches nothing sensitive does not need a human to press the button. But
loosening the merge gate must not weaken the protections that matter (privacy, security, crypto,
transport, governance, release tooling), and it must stay safe once the repository is public and
external contributors appear.

## Policy

A pull request is auto-merged (GitHub native auto-merge is enabled on it, and GitHub completes the
merge once all gates are green) if and only if ALL of the following hold:

- authored by an account on a hardcoded allowlist, today exactly `@welsbach` (every agent session
  commits through the founder's token, so agent PRs are authored by `@welsbach`);
- from a branch in this repository, never a fork;
- not a draft;
- carries exactly one lane label, and that lane is one of
  `chore`, `pillar:platform`, `pillar:feature`, `pillar:knowledge`, `pillar:model`;
- does NOT carry `pillar:privacy` or `security`.

Everything else stays with a human: privacy/security-lane PRs, unlabelled or multi-lane PRs, fork PRs,
and any PR that touches a `CODEOWNERS`-protected path (enforced independently by branch protection, see
below). A `pillar:platform` PR that touches a protected path is therefore auto-merge-eligible by lane
but still held until a code owner reviews it.

## Safety model

The workflow only ENABLES auto-merge; it never merges. Branch protection on `main` is the real gate,
so a bug in the policy cannot merge anything that:

- fails a required status check (the CI test matrix, lint, intake), or
- touches a path listed in `.github/CODEOWNERS` without the required code-owner review.

Additional properties:

- The workflow uses `pull_request_target` but never checks out or executes PR head code; it only calls
  the GitHub API. Untrusted PR content cannot run anything.
- The author allowlist lives in the workflow file, so changing who can auto-merge is itself a
  `.github/` change that a code owner must review. It is not a silent repository setting.
- The merge is authenticated by a short-lived GitHub App installation token minted per run, not a
  standing personal access token.
- With no `ANTHILL_BOT_APP_ID` secret configured the workflow is a clean no-op, so it can land before
  the App is installed and activate only once the App exists.

## Required one-time configuration (repository admin)

1. Branch protection on `main`: keep the CI test matrix, `lint` and `intake` as required checks; remove
   the report-only `invariants` check from the required set (it uses `paths-ignore`, so on a docs-only
   PR it never reports and the PR deadlocks). Switch the global "require 1 review" to required
   approvals 0 plus "Require review from Code Owners", so review is demanded on protected paths only.
2. Reuse the OneHill-Dev-Agent GitHub App (id 4414006, already installed org-wide on OneHillAI for
   agent-authored PRs; see `AGENTS.md`, "Agent identity") rather than installing a second App. Add
   `ANTHILL_BOT_APP_ID=4414006` and `ANTHILL_BOT_APP_KEY` (the App's private key) as repository secrets.
   `ALLOWED_AUTHORS` in the workflow still lists only `welsbach`, so this does not by itself make
   agent-authored PRs auto-merge eligible; widening it to the App's bot login is a separate decision.

## Acceptance criteria

- A `@welsbach`-authored PR labelled `chore` (or a safe `pillar:*` lane), touching no CODEOWNERS path,
  merges automatically once required checks pass, with no human interaction.
- A PR labelled `pillar:privacy` or `security`, a fork PR, an unlabelled PR, or a multi-lane PR is
  NEVER auto-merged.
- A PR touching a CODEOWNERS-protected path is NOT merged until a code owner approves, even when its
  lane is otherwise eligible.
- With the App secrets absent, no PR is auto-merged and the workflow reports a dormant no-op.

## Public-repository note

When the repository opens up, external contributors PR from forks under their own accounts, so the fork
and author gates exclude them: external PRs are never auto-merged and always take a maintainer merge.
Before the public flip, decide whether fork PRs must additionally carry a formal review (a GitHub
ruleset scoped to fork PRs); this policy leaves fork PRs entirely alone.
