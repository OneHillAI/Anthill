# How ASDD runs on Anthill, in one page

Who this is for: maintainers, contributors and the agents that work in this repo. It describes what is running
today, checked on 2026-10-04. The standard itself lives in [OneHillAI/ASDD](https://github.com/OneHillAI/ASDD);
the step-by-step install record is [`asdd-goose-adoption.md`](asdd-goose-adoption.md); the model choices are in
[`specs/asdd-model-roster.md`](specs/asdd-model-roster.md); how a release reaches users is
[`releasing.md`](releasing.md).

## The idea

Anthill is built by AI agents under human direction. Agents write, review, test and document; **a human
merges**. Every change goes through the same gates whoever or whatever wrote it, and an agent counts as set
up only once it has been shown doing its job on a real change (ASDD standard, OP.5).

## The path of a change

1. **Build.** A contributor (or their own coding agent, or the maintainer's) opens a PR with the four items
   of the contract: sign-off, one lane label, the authorship disclosure, tests. A non-chore PR also carries or
   names a spec in `docs/specs/`.
2. **On the PR.** Intake checks the contract: disclosure, a sign-off on every authored commit (merge commits are
   not counted), exactly one lane label, and the spec. The review reads the diff through four lenses and
   recommends; it retries a bad model reply instead of giving up. CI runs lint and the tests on Python 3.10 to
   3.13. A change to the ASDD setup also runs the runtime check, which pings every agent's model.
3. **Staying current.** The merge ruleset will not merge a branch that is behind `main`. A workflow brings the
   bot's open PR branches up to date after every merge, so nobody clicks "Update branch" and waits for CI again.
4. **Merge.** A human approves and merges. Protected paths (`.github/**`, `scripts/**` and others) need a named
   human reviewer. No agent merges, approves or force-pushes.
5. **After the merge**, two agents run on the merged commit and comment on the merged PR: the test agent runs
   the whole suite and records PASS or FAIL as the `asdd/test` status, and the documentation agent proposes the
   docs the change needs. Both only advise.
6. **Release.** Merged work reaches users only through a release. A new stable release starts as a pre-release;
   after it is checked, a release owner makes it the latest release by hand. The beta lane (Cut Beta, Promote
   Beta) is built but has not been used yet. See [`releasing.md`](releasing.md).

## The agents

| Agent | Model | When it runs | What it does | State |
|---|---|---|---|---|
| Developer | the contributor's own (maintainer: Claude) | while building | writes the change; optional council of Opus, Gemini and GPT on Runware | bring-your-own, not provisioned |
| Intake | none (deterministic) | every PR | disclosure, DCO, exactly one lane label, spec | required check |
| Reviewer (code, security, spec, quality) | gemma-4-31B-it on Infercom | every PR | advisory recommendation, the `asdd/review` status and one comment | live, advisory |
| Test runner | gpt-oss-120b on Infercom | after every merge to main | runs the full suite, records the `asdd/test` status | proven on a real merge (2026-10-03) |
| Test author | gpt-oss-120b on Infercom | on demand, while a change is built | proposes the missing tests | installed, not yet run on a real change |
| Documentation | gemma-4-31B-it on Infercom | after every merge to main | proposes the impact-log entry, changelog line and untrue doc sentences as paste-ready blocks | first real run on PR 67 (2026-10-04): well formed; its one wrong suggestion (a fragment for a release cut) is fixed |
| Interaction | gemma-4-31B-it on Infercom | on a chat surface | answers from the wiki, routes ideas into intake | not connected |
| Merge-reviewer and impact reviewer | to be set | before a merge | one independent go or no-go note | not installed |

The test runner, test author and documentation agent each use a different model family from the developer
where the roster allows, and the reviewer differs from the test agents. The roster is in `.asdd.yml`
(`models:`).

## What blocks a merge and what only advises

- **Required** (the branch ruleset): `lint`, the four Python test runs, and `intake`; the branch must be up to
  date with `main`; protected paths also need the code owner's review.
- **Advisory:** the review (its `asdd/review` status and comment), the runtime check, the test agent, the
  documentation agent, and the report-only invariants and conventions checks.
- The review is a skeptic by design, but only a real finding fails the status: a security block, or a concern
  from the code or spec lens. A design-only objection from its adversarial pass stays in the comment and the
  status stays green with a description that names it. Read the comment, fix what is fair, and a human decides.
  Keep PRs to one purpose; a mixed PR draws more objections.

## Rules every agent follows

- Never merge, approve, enable auto-merge or force-push. Tag or publish a release only on the maintainer's
  explicit order.
- Open a PR only after the maintainer has said yes to that PR.
- Push and open PRs as the **OneHill-Dev-Agent** GitHub App, never a personal account.
- Sign off every authored commit and add the `Agent:` trailer. No co-author line for an AI.
- Run the checks CI runs (lint, format, the tests, the intake rules) before pushing, and confirm the push landed.
- An agent with a shell runs only on merged code, never on an open PR (`cli/operate-guard.py` enforces it).

## Settings (names only; values are never committed)

- Variables: `ASDD_MODEL`, and `ASDD_MODEL_URL` as the **full** chat-completions URL.
- Secret: `ASDD_RUNTIME_TOKEN`.
- Per role, optionally: `ASDD_MODEL_URL__<ROLE>` and `ASDD_RUNTIME_TOKEN__<ROLE>` (for example `__DOCUMENTATION`,
  `__TEST_RUNNER`). The model name comes from the roster; `cli/resolve-model.sh` resolves model, endpoint
  and key together.
- Developer council: one pair, `ASDD_MODEL_URL__COUNCIL` and `ASDD_RUNTIME_TOKEN__COUNCIL`, used by the
  runtime check for every council member. The local runner reads per-member names.
- The bot App: `ANTHILL_BOT_APP_ID` and `ANTHILL_BOT_APP_KEY` (repository secrets), used to keep PR branches
  current and, in Promote Beta, to create the stable tag.

## Reading the signals

- `NO AI REVIEW RAN`, `NO TEST AGENT RAN`, `NO DOCUMENTATION AGENT RAN`: that agent did not run (no model
  wired, or Goose missing). It is not a result. Run the **ASDD runtime check**, which shows every agent as
  LIVE or not (it also runs daily).
- `No usable AI review`: the model was reached but its answer could not be used, even after retries. A human
  reviews that PR.
- `did not run to completion`: the agent started and left nothing usable. The report shows the exit code and
  the last lines, with the key removed.

## Running an agent by hand

Actions, then **ASDD test**, **ASDD docsync** or **ASDD runtime check**, then **Run workflow**. The first two
take a commit SHA that must be on `main`.

## Not done yet

- Merge-reviewer and impact reviewer, and the interaction agent (it needs a chosen surface).
- The test author has not run on a real change.
- Cut Beta and Promote Beta have never been run on a real release; they have only been tested.
