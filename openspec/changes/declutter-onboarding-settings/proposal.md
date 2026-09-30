# Declutter the onboarding/settings surface

## Why

The founder tested the just-shipped onboarding + Settings > Organization flow live and found it a
genuine mess: the "Choose your AI" step is fine, but choosing Cloud or Mac-mini dumps the user into an
843-line, jargon-heavy provisioning console (VRAM math, "full precision," RunPod pool ids, raw AWS IAM
policy blocks) that a normal user should never see by default. The onboarding steps also render as
bare, chrome-less screens with no sidebar, so signing up doesn't feel like "being in the app." And
there's no consistent way for a user to know "you haven't finished setup" once they leave the wizard -
the one place that shows a setup CTA (dashboard) is admin-and-org-only, so solo users and non-admin
members get nothing.

This is a **scoped decluttering pass**, not a rewrite: hide/collapse what a normal user doesn't need by
default, fix two real gaps found during verification, and leave a clean surface for a later, dedicated
UI/UX pass. Verified directly against the real code (two Explore passes + a Plan pass + direct
spot-checks of the highest-risk claims - not assumed).

**Explicitly out of scope** (deferred to a later, dedicated UI/UX pass): consolidating the three
near-homonymous settings pages (`/settings/general` "Organization", `/settings/org` hub,
`/settings/organization` "Cloud & model"); Wiki/Training tab content redesign; any copy/visual polish
beyond removing the named jargon; any change to `ProvisionSpec`, `sizing.py`, or provisioning/backend
logic; server-side enforcement of the wizard order on every route (the nav lock is a UI deterrent only,
matching today's reality that direct URL entry already bypasses the existing redirect gates); actually
building a `startup_script_id` fix for DataCrunch/Verda or evaluating Koyeb/Mistral Compute as a future
provisioner (both real, separately-scoped follow-ups identified below, not built here).

## Real bugs found during verification (fixed as part of this change)

1. **`model_picker_post`'s cloud/mac_mini branch never sets `local_model_chosen`** (or calls
   `db.commit()`) before redirecting to `/settings/organization`. A solo user picking Cloud/Mac-mini
   gets bounced back to `/setup/model` by the dashboard's own gate (`if solo and cfg and not
   cfg.local_model_chosen: return RedirectResponse("/setup/model", ...)`) forever, since nothing ever
   marks the step done.
2. **The dashboard's "Set up your instance" CTA is admin-and-org-only** (`if user.get("role") ==
   "admin":` wraps the whole activation checklist; `if not solo:` further gates the compute-setup step
   inside it) - a solo user or a non-admin org member gets no CTA at all today.
3. **`chat.html`'s org-backend-unreachable banner has no actual link**, just plain text ("Connect it
   under Settings -> Organization, or use a Solo chat.") set via `banner.textContent`.

## Provider list reality check (folded in after direct research, not left as a loose thread)

Given live-testing PR #627/#636 only works on Lambda today, the provider dropdown showing 11 options as
if they were equally real is itself part of the mess. Checked against the code
(`_LIVE_PROVIDERS = {"runpod", "lambda"}` in `anthill/web/provision_run.py`) and current research:

- **Only On-prem, Lambda, and RunPod actually work end-to-end.** DataCrunch/Verda has a live
  provisioner (spins up a real VM) but cannot serve a model yet - its own docstring says the
  create-instance API "does not document a startup-script/user-data field." OVHcloud, Scaleway, AWS,
  GCP, Azure, IBM are pure planner stubs (`ProvisionerNotReady`) - a plan preview and nothing else.
- **That DataCrunch docstring claim is stale.** Verda's current API docs show `POST /v1/instances`
  (the `DeployInstancePublicDto` schema) has a documented `startup_script_id` field, created via a
  separate `POST /v1/startup-scripts` resource. The bootstrap gap looks genuinely closeable - a real,
  separately-scoped follow-up, not attempted here (this is new provisioner engineering, not a UI
  declutter).
- **RunPod already has real EU data centers** (Romania, Czech Republic, France, Netherlands, Sweden,
  Iceland) plus GDPR/HIPAA compliance as of Feb 2026 - usable for EU-region live testing today with no
  new engineering, though it's serverless/single-worker (not wired for the Lambda-specific
  tensor-parallel work).
- **Koyeb is not gone** - acquired by Mistral AI (Feb 2026), folding into Mistral Compute, explicitly
  repositioning toward "Europe's sovereign full-stack AI cloud." A candidate worth evaluating before
  investing further in DataCrunch specifically - a research task, not built here.

**Change**: the provider `<select>` shows only **On-prem, Lambda, RunPod** by default; every
non-functional provider (DataCrunch/Verda, OVHcloud, Scaleway, AWS, GCP, Azure, IBM) moves under
"Advanced settings" with a one-line note ("not fully wired up yet - shows a plan preview only"). This
fixes the trap of picking DataCrunch today and getting a VM that boots but never serves anything.

## What's new

1. **Onboarding chrome**: `setup_account_type.html` and `model_picker.html` convert from standalone
   `<!DOCTYPE html>` documents to `{% extends "base.html" %}` (matching `dashboard.html`), so the
   sidebar is visible from step 2 onward (both already run behind `Depends(_require_user)` - the user
   is already logged in). A new `nav_locked` context flag (set by `setup_account_type_get`/
   `model_picker_get`) adds a `nav-locked` class to `<nav>` in `_sidebar.html`, deterring (not
   server-side enforcing) navigating away mid-wizard, via one new CSS rule.
2. **"Choose your AI" fixes**: rename "Advanced: pick a model manually" to "Advanced settings" (the
   local branch already has the right default/advanced shape - just relabel it). Fix bug #1 above.
3. **`settings_organization.html` default vs. Advanced settings**: same route, same `settings_org_post`
   handler, same `Form(...)` fields, same `_derive_council_member_selection` - zero backend/validation
   changes, purely `<details>`-wrapping existing markup. Stays default: provider select (On-prem/Lambda/
   RunPod only), GPU-size select (bare, no VRAM-math paragraph), model select + gated-model help, the
   one minimum credential per provider (Lambda: SSH key name; generic cloud: API key), the plan-preview
   card, Save/Provision. Moves behind Advanced: every explanatory jargon paragraph named above, warm
   workers, quantization toggle, Lambda instance-type/region, HF token field, Council reviewers, "Review
   scheduled tasks & agent runs," AWS's checklist/extra fields/IAM policy block, the non-default
   providers, and the "connect a server you already run" escape hatch. Intro paragraph shortened (the
   one place to trim wording, not just relocate, since the founder named it directly).
4. **A reusable "not set up yet" banner** (`_compute_banner.html`, new partial) taking a pre-computed
   `compute_ready: bool` / `setup_href: str | None` - renders a real link when set, plain "ask an admin"
   text when `None` (a non-admin can't reach the admin-only setup page). Wired into `chat.html` (fixes
   bug #3 - the existing banner gets a real link) and `wiki.html` (new: `cfg`/`solo` added to
   `_wiki_ctx`, banner rendered near the top).
5. **Dashboard CTA for every account type** (fixes bug #2): the compute-readiness check moves outside
   `if user.get("role") == "admin":`, computed for every user and branched on `solo` (solo: reuses
   `local_model_chosen`/`solo_compute`; org: reuses `planes.org_available(cfg)`). Non-admin org members
   get `href=None`/"Ask an admin" text instead of a dead-end link. `dashboard.html`'s activation
   checklist render moves outside the admin-only wrapper (the rest of that section stays admin-gated);
   its `<a href>` guards against a falsy `href`.

## Acceptance criteria

1. Fresh solo signup: sidebar (locked) renders on steps 2-3; choosing Cloud no longer loops back to
   `/setup/model` on the next dashboard visit.
2. From the model picker, choosing "Self-provisioned cloud" lands on the restructured
   `/settings/organization` showing only the minimal default fields; "Advanced settings" reveals
   everything else; Save/Provision still round-trips correctly (`settings_org_post` behavior unchanged).
3. A non-admin org member sees a dashboard CTA with no dead-end link; `chat.html`'s org-backend banner
   shows a real working link for the admin and text-only (no link) for the member.
4. `wiki.html` renders the banner correctly for both an unset-up solo and an unset-up org account.
5. The provider dropdown shows only On-prem/Lambda/RunPod by default, the rest reachable (with a "not
   fully wired up yet" note) under Advanced settings.
6. `ruff check`, `ruff format --check`, `mypy`, full test suite pass. No em/en-dashes, no
   TODO/FIXME/XXX markers. `tests/test_org_provisioning.py::test_get_renders_provider_picker_for_admin`
   (asserts every `PROVIDER_KEYS` value has a rendered `<option>`) still passes - moving providers into
   a collapsed `<details>` keeps them in the DOM, just visually hidden.
