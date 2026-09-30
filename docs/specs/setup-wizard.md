# Setup wizard (first run)

## Problem

First launch must turn "the server is running" into "I can start using my private AI" for a
non-engineer, with no Terminal. The earlier outline (`docs/setup-wizard-outline.md`, now superseded)
described a nine-step, organization-first wizard: create an org, pick a deployment topology, wire a GPU
backend, invite a team. That flow predates decisions that have since shipped or been corrected:

- **Signup is a personal workspace, not an organization** (`docs/specs/signup-no-org.md`, #481,
  unchanged by this update). A new account is a tenant of one ("Personal"); the sign-up SURFACE itself
  still never asks for an organization name, topology, or backend.
- **Solo and organization accounts have identical model/council access** (Phase 6, corrected 2026-07-30;
  supersedes this doc's earlier "one model per account, nothing to configure beyond it" framing). The
  earlier version of this spec concluded there was "nothing to configure beyond the one account model" and
  so folded organization choice entirely into Settings, after sign-up. That conflated two different things:
  who can access the model/council (nobody but you, until you invite people) and whether the model/council
  itself differs by account type (it does not, and never should have needed to). Only the FORMER remains a
  deferrable Settings concern; account type itself is a first-run decision, immediately after creating an
  account.

## Requirements

The wizard is now guided in stages, shown with a progress indicator:

1. **Create your account.** Email, name (optional), password (min 12 chars), or an SSO button
   (Google/Microsoft) when configured. This commits the first admin + a "Personal" workspace
   (the existing `POST /setup`). No organization name, topology, or backend is asked for here - unchanged.
2. **Solo or organization** (`GET/POST /setup/account-type`, new). Solo is the prominent, default choice;
   setting up an organization is a lighter-weight secondary option that additionally collects an org name.
   Both choices get IDENTICAL model/council access - this step is only about whether anyone else can be
   invited later, never about compute/backend. Shown exactly once per fresh account (gated on
   `OrgSettings.account_type_chosen`, mirroring `local_model_chosen`'s own gate-until-chosen pattern); an
   existing account is never re-shown this screen.
3. **Choose your AI** (`GET/POST /setup/model`, extended). Three compute options: Local (Solo only - an
   organization's backend must be reachable by every future invited member, so it cannot live on one
   person's device), self-provisioned cloud, self-hosted Mac mini (the latter two link into the existing
   `/settings/organization` provisioning flow rather than duplicating it here). Choosing Local checks this
   machine's real capacity (memory, speed, and disk - `sizing.suggest_local_setup`) and suggests a
   3-member family-diverse local council when it fits, falling back to the single smartest model
   otherwise - council is the reached-for default, never a silent single-model default without actually
   checking whether 3 fit. Accepting a council registers all 3 members in
   `OrgSettings.org_council_members` (verified resolvable via `resolve_council_backends`, not just saved);
   accepting a single suggestion behaves exactly as before (sets `ollama_model`, no council entry). Manual
   per-family picking remains available under an "Advanced" affordance.

After step 3 the user lands on the dashboard. An organization account still sees the existing "Get your
org running" activation checklist pointing at Settings for its backend - unchanged by this update.

**Still explicitly out of scope for setup** (reachable later from Settings, none required to start): GPU/
cloud backend choice beyond the two link-outs above, privacy toggles (hybrid fallback, proactivity, wiki
auto-promotion), and inviting teammates. Personalize/wiki/privacy sequencing after step 3 remains a
separate, later change.

**Cross-cutting:** one decision per screen; sensible defaults pre-filled; every screen skippable to a safe
default (Solo, if the account-type screen is abandoned mid-flow, since it is the pre-filled default choice
on submit); inline validation; single-column and large tap targets for the PWA. The flow is resumable by
construction: no organization exists until step 1 commits, and `account_type_chosen`/`local_model_chosen`
independently gate re-entry to steps 2/3, so nothing is lost by leaving mid-flow and returning.

## Acceptance criteria

- `GET /setup` renders step 1 of a three-step indicator for a logged-out visitor; sign-up availability
  and first-run sign-in navigation follow `docs/specs/signup-no-org.md`.
- The step indicator marks step 1 current on `/setup`, step 2 current on `/setup/account-type`, and step 3
  current on `/setup/model`; each renders earlier steps as done.
- `/setup` collects only account fields; it never asks for an org name, topology, or backend on the
  sign-up surface (unchanged).
- `/setup/account-type` offers Solo (prominent/default) and organization (secondary, with an org-name
  field); is shown exactly once per fresh account; an existing account visiting it directly is redirected
  to `/`.
- `/setup/model` offers Local only when `deployment_topology == "solo"`; suggests a council of 3 when
  capacity allows (memory, speed, and disk all checked), else the single best-fitting model; an accepted
  council resolves via `resolve_council_backends` to exactly 3 members; choosing cloud/Mac-mini redirects
  to `/settings/organization` rather than rendering a duplicate provisioning form.
- The indicator is single-column and legible on a ~380px viewport.
- Nothing in first-run configures a backend, privacy toggle, or team invite; account type itself now IS
  configured in first-run.

## Build mapping

- `anthill/web/templates/_setup_steps.html` renders the indicator from a `setup_step` (1, 2, or 3) passed
  by the route; included by `setup.html` (1), `setup_account_type.html` (2), and `model_picker.html` (3).
- `OrgSettings.account_type_chosen` (new column, defaults to True everywhere - existing accounts, and any
  OrgSettings row constructed without going through sign-up) so nothing is retroactively or accidentally
  routed through this screen; only `setup_post`'s fresh `OrgSettings()` explicitly sets it False, which is
  what actually routes a genuine new sign-up through `/setup/account-type`. Gates it exactly as
  `local_model_chosen` gates `/setup/model`; the dashboard route (`GET /`) checks it before the existing
  `local_model_chosen` check.
- The organization NAME and Solo/org choice are set on `/setup/account-type`; the backend/invite flows
  remain in Settings (`/settings/organization`, `/settings/automation`, `/users`, `/teams`), unchanged by
  this update.
