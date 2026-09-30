# Declutter onboarding and Organization settings

Status: implemented. Lane: `pillar:platform`. Owner: platform.
Relates to: `docs/specs/model-onboarding-and-sovereignty.md` (the larger, still-`proposed` onboarding
redesign - this spec is a smaller, immediately-actionable slice of the same problem, not a substitute
for it; it does not implement that spec's two-primary-path/percent-of-frontier requirements).

## Problem

The founder tested the shipped onboarding + Settings > Organization flow and found it a genuine mess:
choosing Cloud or Mac-mini in the "Choose your AI" step dumped the user into an 843-line, jargon-heavy
provisioning console (VRAM math, "full precision", raw AWS IAM policy blocks) that a normal user
should never see by default. Onboarding steps 2-3 rendered as bare, chrome-less pages with no sidebar,
so signing up didn't feel like being inside the app. And there was no consistent way for a user to
know "you haven't finished setup" once they left the wizard - the one place that showed a setup CTA
(the dashboard) was admin-and-org-only, so solo users and non-admin members got nothing at all.

Live-testing this pass also surfaced two real, user-facing bugs (see "Bugs found and fixed" below),
one of them severe and pre-existing on `main` independent of this change.

## Requirements

- THE SYSTEM SHALL render onboarding steps 2-3 (`setup_account_type.html`, `model_picker.html`) inside
  the normal app chrome (sidebar visible), not as standalone documents.
- THE SYSTEM SHALL visually deter (not enforce) navigating away from an unfinished wizard: sidebar
  links dim and become unclickable (`nav-locked`) while a wizard step is showing.
- THE SYSTEM SHALL show, by default, only the minimum needed to connect a working org model server:
  provider, GPU size, model, and one credential. THE SYSTEM SHALL move everything else (provider
  explainer, warm workers, the 4-bit toggle, Lambda instance/region, Hugging Face token, Council
  reviewers, scheduled-task review) behind a collapsed "Advanced settings" section that auto-opens if
  any of that content is already configured.
- THE SYSTEM SHALL show, by default, only providers that are live end to end today (on-prem, Lambda,
  RunPod) in the provider picker. Every other provider SHALL remain selectable under Advanced settings,
  annotated inline ("not fully wired up yet, plan preview only") so picking one cannot be mistaken for
  a fully working choice.
- THE SYSTEM SHALL show a "finish setup" call to action for every account type that has not finished
  compute setup - solo users and non-admin organization members included - not only an org admin.
  A user who cannot reach the admin-only settings page (a non-admin member) SHALL see "ask an admin"
  text instead of a link into a page that would 403.
- THE SYSTEM SHALL surface the same "compute isn't set up yet" notice, with the same readiness
  definition, on Chat and Wiki as on the dashboard (one shared helper, not three independent checks
  that could disagree).

## Bugs found and fixed while implementing this pass

- **Onboarding redirect loop.** `model_picker_post`'s `compute in ("cloud", "mac_mini")` branch never
  recorded the choice (`local_model_chosen`, `solo_compute`), and the wizard's own "Self-provisioned
  cloud" / "Self-hosted Mac mini" tiles were plain `<a href>` links that bypassed that branch entirely
  - together, a solo account picking either option could get bounced back to the picker forever on its
  next dashboard visit. Fixed on both ends: the tiles now POST through the same route the "local" path
  already used, and that route now records the choice.
- **"Save changes" on Settings > Organization could silently do nothing.** The Hugging Face token
  field, each Council reviewer's Provision/Tear down buttons, and the scheduled-task review toggle were
  each their own `<form>`, nested inside the page's main settings form. Nested forms are invalid HTML;
  a browser's parser handles this by detaching everything after the first nested form's closing tag
  from the outer form, including the real "Save changes" button - so Save could stop submitting the
  provider/model/GPU choice entirely, with no error and no visible sign anything was wrong.
  **Reproducible on `main` independent of this change** (the Council review-toggle form was already
  nested before this PR). Fixed by posting those three actions through a small shared JS helper
  (`_settingsFormPost`) instead of a nested form.

## Explicitly out of scope (deferred to a later, dedicated UI/UX pass)

- Reworking the provider list down to `docs/specs/model-onboarding-and-sovereignty.md`'s two-primary-
  path shape, or its percent-of-frontier / dev-stage capability presentation - this pass keeps the
  existing tier-based provider picker and the existing model-list presentation.
- Consolidating the three near-homonymous settings pages (`/settings/general`, `/settings/org`,
  `/settings/organization`).
- Wiki/Training tab content redesign.
- Any change to `ProvisionSpec`, `sizing.py`, or provisioning/backend logic - zero `Form(...)` field
  renames on `settings_org_post`, zero validation changes.
- Server-side enforcement of the wizard order on every route (the nav lock is a UI deterrent only,
  matching the pre-existing reality that direct URL entry already bypasses the redirect gates).

## Acceptance criteria

- A fresh solo signup can complete onboarding with the sidebar visible and locked on steps 2-3, and
  choosing Cloud or Mac-mini no longer loops back to the picker on the next dashboard visit.
- Settings > Organization's default view shows only provider/GPU/model/one-credential/Save; Advanced
  settings holds everything else and auto-opens when already configured; Save actually persists.
- A non-admin org member and a solo user each see a real "finish setup" prompt (dashboard, chat, wiki)
  appropriate to their role, with no link into a 403.
- `test_get_renders_provider_picker_for_admin` still passes unmodified; full local suite is green.
