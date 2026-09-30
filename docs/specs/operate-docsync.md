# Spec: operate layer in CI - the documentation agent, post-merge

Status: proposed
Lane: `pillar:platform`
Relates to: [`.github/workflows/asdd-docsync.yml`](../../.github/workflows/asdd-docsync.yml),
[`.github/asdd/operate/docsync.sh`](../../.github/asdd/operate/docsync.sh),
[`recipes/documentation.yaml`](../../recipes/documentation.yaml),
the review gate ([`.github/workflows/pr-review.yml`](../../.github/workflows/pr-review.yml)).

## 1. Introduction

The govern gates run an agent automatically today, but only the reviewer: it runs on every PR and posts a
comment. The other roster agents are run by hand. This wires the first **operate** agent to run
automatically and post a PR-side update: the **documentation agent**, which drafts the doc, impact-log,
changelog, and knowledge-base updates a merged change needs.

It runs **post-merge** (`on: push: main`), and that choice is a security requirement, not a convenience.

### 1.1 Why post-merge, and why not the tester on an open PR

The reviewer is safe to run on untrusted PR content because it **executes nothing**: it is a pure model
call whose output is data, in a read-only job, with the write scope isolated in a separate publish
workflow. A tool-using operate agent (Goose with a shell) is different in kind. Run one automatically on
an **untrusted** PR with the model key in the environment, and a prompt-injection in the diff can steer
the agent's shell into exfiltrating that key. The shell plus a secret plus untrusted input is an
injection surface the reviewer does not have.

So the operate-agent-in-CI rule is: an **execution-free** agent (the reviewer) may run on untrusted input;
a **tool-using** agent may run automatically only on **trusted** input, or inside a network-egress-blocked,
secret-isolated sandbox. The documentation agent post-merge takes the trusted-input path: a human has
already reviewed and merged the change, so giving the agent a shell is safe, and a single workflow is
fine. Automating the **tester on an open PR** is deferred until the sandbox is built.

## 2. Requirements

- **R1** WHEN a change lands on `main`, THE SYSTEM SHALL run the documentation agent against that change
  and post its proposed doc updates as a comment on the merged PR (or the commit, if none), advisory only.
- **R2** THE agent SHALL propose; it SHALL NOT merge, open a PR, or edit code or protected paths.
- **R3** WHEN the model runtime is not wired (no `ASDD_MODEL_URL` / `ASDD_MODEL` / `ASDD_RUNTIME_TOKEN`) or
  Goose is absent, THE SYSTEM SHALL post a dry-run preview naming what it would do, and SHALL NOT fail the
  push. (Same fail-soft posture as the review gate's dry-run.)
- **R4** THE documentation agent SHALL run only on trusted (post-merge) input; a tool-using agent SHALL
  NOT be wired to run automatically on untrusted (pre-merge) content without a sandbox.

## 3. Design

- `recipes/documentation.yaml` gains a `prompt:` field so it runs headless (a recipe with only
  `instructions:` cannot; `--recipe` does not combine with `-t`).
- `.github/asdd/operate/docsync.sh` runs the recipe on Goose's built-in `openai` provider, pointed at the
  OpenAI-compatible endpoint via env derived from the same `ASDD_MODEL_URL` the reviewer uses, so no new
  secret or variable is needed. It writes the agent's proposal to a file, or a dry-run preview.
- `.github/workflows/asdd-docsync.yml` triggers on `push: main`, installs Goose best-effort, runs the
  script, and posts the report on the merged PR. Write scope lives here; the input is trusted, so no
  read-only/publish split is required (unlike the reviewer).

## 4. Tasks

- [ ] `recipes/documentation.yaml`: add `prompt:` (R1).
- [ ] `.github/asdd/operate/docsync.sh`: live-or-dry-run runner (R1, R3).
- [ ] `.github/workflows/asdd-docsync.yml`: post-merge trigger + post (R1, R2, R4).
- [ ] `docs/SYSTEM_IMPACT_LOG.md` entry.

## 5. Out of scope

- **The tester on an open PR.** Needs a network-egress-blocked, secret-isolated sandbox (R4). Deferred;
  the security constraint is handed to the framework as part of the reusable operate-agent-in-CI pattern.
- **The full operate kit** (all recipes + `cli/` gates) committed here. Only the documentation recipe is
  needed for this workflow; the reusable kit belongs in Goose-as-ASDD.
- **First live run verification.** The live Goose-in-CI path (install + provider env) is exercised only
  once the model is wired; until then the workflow dry-runs.
