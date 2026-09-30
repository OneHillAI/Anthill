# Phase 6b2: compute/model/council step - capacity-based council suggestion

## Why

Phase 6b1 (PR #666, merged) added the post-signup Solo-vs-organization branch screen. This change is the
NEXT step in that same sequence: today's `/setup/model` only ever offers a single local model. Per the
founder's explicit design principle: "we should aim for the council mode that is always possible and
available" - the step should suggest a 3-member family-diverse local council when this machine's
capacity (memory, speed, AND disk - Phase 6a, PR #665, merged) allows it, falling back to the single
smartest model only when it does not.

## What already exists and is REUSED

- `sizing.recommend_by_family(mem_gb, *, kind, catalog=None, families=None) -> list[FamilyPick]` - the
  smartest model PER FAMILY that fits AND is fast enough, already ordered strongest-family-first
  (`families_by_intelligence`). This is the correct building block for "3 family-diverse local models" -
  simply take the top 3 `FamilyPick`s whose `recommended` is not `None`.
- `sizing.onprem_council_fits(member_params_b: list[float], ...) -> bool` - memory fit for N local models
  together (Phase 2, already shipped). Takes ONLY `member_params_b` positionally/keyword - it calls
  `local_hardware()` internally; it does NOT accept `mem_gb`/`kind` as parameters (a real bug an earlier
  dev-council draft for this same phase made - verified against the actual signature before writing this).
- `sizing.onprem_council_fits_on_disk(member_params_b: list[float], path=None) -> bool` - disk fit,
  same shape (Phase 6a, PR #665, merged).
- `sizing.local_hardware() -> tuple[float, str]` - `(mem_gb, kind)` probe.
- `_model_picker_view(cfg)` (`anthill/web/app.py:1306-1370`) - today's single-model view-model, to be
  EXTENDED (not replaced): its per-family fit-tier logic stays, a council suggestion is added alongside it.
- `/setup/account-type`'s `cfg.deployment_topology` (Phase 6b1) - already distinguishes Solo from
  organization; this change reads it to decide whether Local is offered as a compute option.
- `/settings/organization`'s existing VPC/on-prem provisioning UI - NOT rebuilt. Choosing a
  cloud/self-hosted-Mac-mini compute option in this step links to that existing flow rather than
  duplicating its provisioning logic inline in onboarding.

## Explicit correction of a prior mistake (do not repeat)

An earlier dev-council draft for this same capability called `suggest_regional_council("local", ...)` -
that function is for choosing a GEOGRAPHICALLY-diverse REMOTE council (region is "US" | "EU" | "China",
used for picking VPC provider regions), not local hardware capacity. Passing "local" does not crash - it
silently returns an empty, useless result (zero region matches), which is worse than a crash since a type
checker would not catch it. Use `recommend_by_family` for local family-diverse selection instead;
`suggest_regional_council` is unrelated to this task and must not be touched or reused here.

## Design

1. New `sizing.suggest_local_setup(mem_gb: float, kind: str, *, catalog=None) -> LocalSetupSuggestion`
   (dataclass with `mode: str` ["council"|"single"], `members: list[FamilyPick]`, `hw_label: str`): calls
   `recommend_by_family`, takes the top 3 picks with a non-None `recommended`, checks
   `onprem_council_fits` AND `onprem_council_fits_on_disk` on their `params_b`; if both pass, mode is
   "council" with all 3; otherwise mode is "single" with just the top pick. This is the ONE place this
   decision is made - no duplicate fallback logic elsewhere.
2. `/setup/model` (`GET`, extended): reads `cfg.deployment_topology` to decide whether "Local" is offered
   (Solo only - an organization's backend must be reachable by every future invited member, so it cannot
   live on one person's device). Shows three compute options: Local (Solo only), self-provisioned cloud
   (links to `/settings/organization`), self-hosted Mac mini (links to `/settings/organization`, same
   provisioning surface). Choosing Local calls `suggest_local_setup` and renders the council-or-single
   suggestion; choosing either of the other two redirects into the existing Settings provisioning flow -
   this step's job for those paths is routing, not rebuilding provisioning UI.
3. `POST /setup/model` (extended, minimal change): a council suggestion accepted downloads all 3 members'
   models (mirrors today's single-choice download, just for 3 tags instead of 1) OR a single suggestion
   downloads just the one, matching today's exact behavior.
4. **Decided (not left ambiguous): accepting a council suggestion ALSO writes the 3 models into
   `cfg.org_council_members`** (index 0 = lead, 1-2 = reviewers; each `provider="onprem"`,
   `lifecycle="vpc"` [the field's own default - "onprem" is a value of `provider`, not `lifecycle`,
   verified against `resolve_council_backends`], `endpoint=cfg.ollama_url`, `model=<ollama_tag>`).
   Downloading 3 models but never registering them as resolvable council members would make the whole
   suggestion pointless - chat/tasks would still only ever use the single default `ollama_model`, since
   `resolve_council_backends` only ever looks at `org_council_members`. A single-model suggestion instead
   sets `cfg.ollama_model` exactly as today (unchanged, no council registration - one model needs none).

## Explicitly out of scope

- Personalize -> wiki -> privacy sequencing after this step (a separate, later change).
- Rebuilding VPC/self-hosted-Mac-mini provisioning UI inline in onboarding - link to the existing
  `/settings/organization` flow instead.
- The deferred "explanation screen" between sign-up and setup (still deferred, per the founder's earlier
  note).
- Any UI to individually swap/remove one of the 3 auto-suggested council members at onboarding time - the
  existing `/settings/organization` Council reviewers UI already covers editing after the fact.

## Acceptance criteria

1. `sizing.suggest_local_setup` exists, uses `recommend_by_family` (never `suggest_regional_council`),
   calls `onprem_council_fits`/`onprem_council_fits_on_disk` with their REAL signatures (verified against
   `anthill/hosting/sizing.py` directly before writing code, not assumed), and is tested with BOTH the
   "3 fit" and "only 1 fits" branches exercised (mock the underlying probes, do not rely on the real
   catalog/hardware in tests).
2. `GET /setup/model` shows Local only when `cfg.deployment_topology == "solo"`; shows the council
   suggestion (3 models) when capacity allows, else the single best-fitting model - never silently
   defaulting to 1 without actually checking whether 3 fit.
3. Choosing cloud or self-hosted-Mac-mini redirects to `/settings/organization`, not a rebuilt
   provisioning form.
4. Accepting a council suggestion writes all 3 members into `cfg.org_council_members` (index 0 = lead)
   with `provider="onprem"` and an endpoint pointing at the local Ollama server, verified resolvable via
   `resolve_council_backends` in a test (not just asserted to be saved) - a council suggestion that
   downloads models but never becomes a working council is not acceptable.
5. `ruff check`, `ruff format --check`, `mypy`, full test suite pass. No em/en-dashes, no
   TODO/FIXME/XXX markers.
