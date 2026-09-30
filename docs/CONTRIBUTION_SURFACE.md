# Public contribution surface - architecture and website hand-off

How Anthill turns anyone's idea into a merged change, built on the **ASDD** framework (Agentic
Spec-Driven Development). This doc is the contract another session builds the **public website board**
against. The framework is fixed; the product on top is ours to build.

Normative framework reference: the ASDD repo, `OneHillAI/ASDD` (`STANDARD.md`, `agents/runtime.md`). Everything
marked **guardrail** below is inherited from there - do not re-derive it, inherit it.

## Status

| Phase | What | State |
|---|---|---|
| P0 | Spec object + intake agent + Contribute page | **built** (`anthill/contribute/`, `ContributionProposal`) |
| P1 | In-app chat channel (suggestion -> intake) | **built** (`/chat/suggest`) |
| P1+ | Help-chat channel (features/bugs/questions -> intake) | **built** (`/help/ask`, `/help/file`) |
| P2 | Public website board + social proposing identity | **this hand-off** |
| P3 | Relevance triage queue | **built** (`/contribute/{id}/triage` + `/decide`) |
| P4a | Mint a GitHub issue from an accepted proposal | **built** (`/contribute/{id}/mint`) |
| P4b | Developer agent re-derives a PR from the spec -> human-DCO merge + attribution | **outline below** |

## The one rule that divides product from framework

If another company adopting ASDD could do it completely differently and still conform, it is ours to
build (login providers, a website board, minting an issue). If not, it is framework (a code-authoring
contribution needs a DCO-capable identity; untrusted input is data). Build the former; inherit the
latter.

## Architecture: the central instance publishes, the website reads

```
in-app channels ─┐
 (chat, help,    ├─► central Anthill instance ──► intake agent ──► ContributionProposal
  Contribute)    │      (public submit API)          (drafts spec)        (spec object)
website board ───┘                                                            │
                                                                              ▼
                                            accepted proposals published as data to a
                                            folder in the PUBLIC repo (e.g. contributions/)
                                                                              │
                                                                              ▼
                                            the website renders that folder statically
                                            (no GitHub account required to read or to submit)
```

- The **central instance** (the project's own always-on Anthill - the "A1" node) owns the queue. Every
  channel converges on **one intake agent** that emits a `ContributionProposal`.
- On acceptance (P3), the proposal is **published as data** to a folder in the public repo. The website
  is a **thin static reader** of that folder - it needs no database and no GitHub login to display the
  board.
- **Submitting never requires a GitHub account.** Proposers use a social identity (see below); GitHub is
  only for the developers who later take a speced issue.

This keeps the website simple and the trust boundary clean: the public side only ever sees the
validated, published spec object, never the internal wiki or model.

## The data contract: the spec object

`ContributionProposal` (`anthill/web/db.py`) is the channel-agnostic spec object. The fields a published
proposal exposes to the website:

| Field | Meaning |
|---|---|
| `id` | stable identifier |
| `kind` | `feature` \| `bug` \| `improvement` |
| `title` | short summary line |
| `spec` | the intake-agent-drafted spec (Problem / Proposed solution / Acceptance criteria), markdown |
| `priority` | `low` \| `medium` \| `high` |
| `status` | `submitted` -> `triaged` -> `accepted` \| `parked` -> `minted` -> `building` -> `merged` |
| `proposer_provider` | `x` \| `linkedin` \| `github` \| `inapp` |
| `proposer_handle` | attribution shown on the board and the shipped change |
| `agent_drafted` | disclosure: the spec was written by the intake agent |
| `reference_code` | attached code, **data only** (see guardrails); not published verbatim as a diff |

**Published-folder format (to define when P3 lands):** one JSON file per accepted proposal under
`contributions/`, carrying the fields above (minus internal ids/PII). Keep it a stable, versioned schema
(`contribution/v1`) so the website can render it without calling the app.

## Public submit API (to build in P2)

A single public endpoint the website form and any bring-your-own-agent submission POST to. It converges
on the same intake path as the in-app channels (reuse `_make_contribution` / `distil_proposal`).

- **Input:** `idea` (free text), optional `kind`, optional `reference_code`, proposer identity token.
- **Output:** the created proposal id + drafted spec.
- **Auth:** the proposing identity below - attributable, never anonymous, never GitHub-gated.
- **guardrail** rate-limited per window (STANDARD 3.6); untrusted input is data (3.1); read-only toward
  wiki/model; every action audited (1.3).

## Identity: two tiers

- **Proposing identity** (attributable) - for feature/bug/improvement suggestions. Start with **X**,
  then **LinkedIn**. Grants: submit a spec (+ optional reference code), get public attribution. Register
  an OAuth app per provider (owner-supplied client id/secret); Anthill already has the Google/Microsoft
  OAuth plumbing (`oauth_login_outcome`) to model these on.
- **Authoring identity** (DCO-capable) - **GitHub**, for the developers who take a listed, speced issue,
  open a PR, and sign the DCO. **guardrail** a code-authoring contribution needs a DCO-capable identity
  (STANDARD 2.2); this is framework, not a product choice.

## Guardrails inherited from ASDD (do not re-implement, do not violate)

Most are already satisfied by Anthill's existing membrane - the website must not break them.

- **The board is untrusted.** Nothing on it reaches the wiki or model except a validated spec object.
- **Submitted code is reference, never a diff.** The developer agent (P4) re-derives from `spec`;
  attached code is never merged verbatim. Already enforced by the intake path.
- **Two review roles stay separate.** The contributor-facing reviewer suggests; it is not the merge gate.
- **Model heterogeneity.** Developer != reviewer != verifier models. Anthill's cross-check verifier
  (`anthill/verify`, different-family) already provides the anti-rubber-stamp pass.
- **A human owns every protected merge and signs the DCO.** No proposal, however good, merges without a
  human. Already enforced by branch protection + the ASDD CI pipeline.

## What the website session builds

1. The **board UI** - reads the published `contributions/` folder, lists proposals by kind/status with
   attribution and the drafted spec. Static, no auth to read.
2. The **submit flow** - a form (idea + kind + optional reference code) behind **social login** (X, then
   LinkedIn), POSTing to the public submit API. No GitHub account required.
3. **Attribution** surfaced on the board and (P4) on the shipped change.

## P4b: the developer agent (outline - the largest remaining piece)

P4a hands an accepted proposal to GitHub as an **issue** (`/contribute/{id}/mint`: spec + attribution +
reference-code-as-data + disclosure; owner-configured `ANTHILL_CONTRIB_REPO` + token; a graceful no-op
until configured). P4b is what turns that issue into a merged change - and it is a **full autonomous
coding capability**, not a single route, so it is scoped here rather than built inline.

- **What it does:** picks up a minted issue, **re-derives the implementation from the spec** (never
  pasting the proposer's reference code - that stays a hint, STANDARD 3.9), works in an isolated
  checkout, runs the tests, opens a **PR** crediting the proposer as co-author, and hands off to the
  existing pipeline.
- **What it needs (beyond this repo today):** GitHub **write/push** credentials (a token or app with
  `contents:write` + `pull_requests:write`) and a place to run the coding loop. This is the ASDD
  developer agent (issue -> plan -> build -> PR).
- **What is already done for it:** once a PR exists, the **ASDD CI pipeline** already runs the
  review lenses + the different-family cross-check and enforces the human-DCO merge gate (branch
  protection + `CODEOWNERS`). P4b only has to produce a sound PR; the gate is built.
- **Model heterogeneity:** the developer agent's model MUST differ from the reviewer/verifier (RR.3);
  the triage step (P3) already runs on a different family, and the CI cross-check enforces it at review.

## Before this goes public - checklist

Everything built so far is **in-app and internal**: the help ("?") button, the Contribute page, and the
chat suggestion all funnel into the intake -> triage -> accept -> mint-issue pipeline, but only for
**logged-in users**, and the repo is **private**. None of it is reachable by, or connected to, a public
audience yet. These items gate the public flip (they sit on top of the existing owner-only Launch gate:
history scrub + repo visibility flip):

- [ ] **Repo is public.** Minted issues (P4a) and publishing accepted proposals to a `contributions/`
      folder (P2) both need the repo public so external proposers/contributors can see them. Until the
      flip, minted issues are private. (Owner-only: scrub history, then flip visibility LAST.)
- [ ] **The public front door exists (P2).** The website board + public submit API + social login (X,
      then LinkedIn) are handed off, not built. Today only authenticated users can submit; the public,
      GitHub-less path does not exist yet. Needs the owner's X/LinkedIn OAuth apps.
- [ ] **P4a is configured on the central instance.** Set `ANTHILL_CONTRIB_REPO` + a token with
      `issues:write`, and confirm issues land on the intended public repo. Until set, minting is a no-op.
- [ ] **Public documentation exists and is wired in.** There is no public docs site today; the help chat
      grounds on the in-repo guides (`docs/how-it-works.md`, `docs/setup.md`) and `USING_ANTHILL.md`
      lives in the (private) repo. Decide where public docs live, publish them, and point the public
      board/help at them.
- [ ] **The central instance ("A1") is stood up.** The always-on Anthill instance that runs the public
      submit API + intake and publishes accepted proposals to the repo folder.
- [ ] **Abuse controls on the public submit path.** Rate-limit the public endpoint (STANDARD 3.6) and
      add spam/moderation on the board (it is an untrusted zone).
- [ ] **External-contributor governance is visible + enforced.** `CONTRIBUTING.md` / DCO instructions /
      `CODE_OF_CONDUCT.md` on the public repo, and branch protection + `CODEOWNERS` so the human-DCO
      merge gate applies to outside PRs (the ASDD CI already does the review/cross-check).
- [ ] **Attribution consent.** Social handles become public on the board and on issues - add a consent
      notice at submit time.
- [ ] **(Optional for v1) P4b developer agent.** Accepted issues can be human-built at launch; the
      autonomous developer agent (issue -> re-derived PR) is the full loop and needs push credentials.

## Acceptance criteria (for the public surface)

- All channels (chat, help, Contribute page, website board, bring-your-own-agent) create a spec object
  via the one intake agent; none writes to the repo directly.
- A social-login proposal publishes to the board with attribution and never exposes the internal wiki or
  model.
- A GitHub-less proposal can travel end-to-end to a merged, correctly-attributed PR with a human DCO
  signature (P4).
- Reference code is stored and shown as data; no path merges it as an authored diff.
- The intake / triage agents run on models distinct from the developer, and every board and issue action
  is disclosed as agent-produced and lands in the audit trail.
