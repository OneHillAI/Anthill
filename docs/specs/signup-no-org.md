# Sign-up is a personal workspace; an organization is created later

## Status

Decided (reframed **Option B**, founder call 2026-07-13). Sign-up creates a **personal workspace** - a
tenant of one - and never presents it as "an organization". An organization is created later, when the
user invites people / connects a shared backend.

## Architecture decision (why not remove `org_id`)

`org_id` is the **tenant key across ~25 tables** and ~155 `_require_org` sites - a standard multi-tenant
design where a "solo" user is a **tenant of one** (the same pattern GitHub / Linear / Notion / Slack use:
you always have a personal workspace; a team/org is a workspace with >1 member). Making `org_id` nullable
(a truly org-less `User`) would re-architect the multi-tenancy - dual `org_id`-or-`user_id` scoping at
every site, plus data-migration-on-join, plus a SQLite column-nullability migration - for no real gain.
So we **keep `org_id`** as the tenant key and fix the **presentation**: a solo tenant is a personal
workspace, never called an organization. One account = one tenant; joining another org = a separate
**profile** (see `SOLO_PROJECT_ORG_SETUP.md`).

## Requirements (EARS)

- The sign-up surface SHALL collect only what a personal account needs (identity + password / SSO); it
  SHALL NOT ask the user to name an organization, choose a topology, or select a GPU backend.
- WHILE the local installation has no account, sign-up SHALL omit its Sign in link because `/login`
  redirects back to `/setup`; once an account exists, sign-up SHALL offer Sign in.
- WHEN a user completes sign-up, the system SHALL create a **personal workspace** (a tenant of one:
  `deployment_topology == "solo"`, no shared backend) with a **neutral** name ("Personal"), never one
  derived from the email domain.
- WHILE an account has no shared backend (is not a real organization), the UI SHALL show **no org chrome**:
  the workspace name SHALL NOT appear as "your organization", and org-admin surfaces stay hidden/gated.
- The system SHALL let the user **create an organization** later from the dashboard (name it, connect a
  shared backend, invite members), which turns the personal workspace into a real org.
- Sign-up SHALL NOT require email verification for a personal workspace; the org-member email-confirm
  requirement is re-homed to the **invite-accept** flow (already link-verified). Anti-lockout fallbacks
  (solo / no-SMTP / send-fail auto-activate) SHALL be preserved.

## Implemented

- `setup.html`: no topology / org-name / GPU-backend choosers; sign-up is email / name / password (+ SSO).
- `setup_post`: `topology` defaults to `solo`; a solo sign-up names its workspace **"Personal"** (only an
  explicit `topology=org` sign-up derives a name from the email domain). Org-setup params still accepted
  for the legacy/API path.
- `_nav_context`: `nav_org` is set only when the account is a real org (`planes.is_org_mode`), so a solo
  user never sees the workspace name as org chrome.
- `dashboard.html`: the solo card is a clear **"Create an organization"** entry (name + shared backend +
  invite); until then everything stays private to the user.

## Known gap fixed: sign-up dead-ended after the first account

`/setup` (GET+POST) originally redirected away the instant *any* `Organization` row existed anywhere
in the install's database - so only the very first-ever visitor could sign up, and `login.html` still
said "ask your admin to invite you" (stale copy from before this spec shipped self-serve sign-up).
A logged-out returning user, or anyone wanting a second personal account on a shared/hosted install,
hit a dead end with no way back to sign-up.

Fixed: `/setup` now only redirects a visitor who is already logged in (mirrors `login_get`'s own
guard); `login.html` links back to `/setup`. This surfaced two things that could never happen while
sign-up only ran once per install and needed fixing alongside it:
- **Org-slug collision**: every personal sign-up with no `org_name` defaults to the same "Personal"
  name (`slugify("Personal") == "personal"`), and `Organization.slug` is unique at the DB level - the
  second sign-up would hit an unhandled `IntegrityError`. `setup_post` now suffixes with `-2`, `-3`,
  ... until free.
- **Duplicate email**: a second sign-up with an already-registered email would hit `User.email`'s own
  unique constraint the same way. `setup_post` now checks first and re-renders the form with "An
  account with that email already exists. Sign in instead." (409), instead of a 500.

OAuth sign-up (`oauth_login_outcome`) is intentionally untouched: it still only self-registers the
first account and rejects any other unknown email as `not_invited` - "an org stays invite-only" is a
deliberate security boundary, not something this fix reopens. `setup.html`'s "Sign up with Google /
Microsoft" buttons are now shown only on a genuine first run (`is_first_run`), so the page never
offers a button that would silently reject a real, later visitor.

## Remaining polish (follow-up)

- Sweep the remaining "your organization" copy on member-visible pages (skills / integrations) for solo
  tone; org-admin pages are already gated.
- A dedicated create-an-organization page (name + backend + invite in one flow) rather than routing
  straight to `/settings/organization`; reuse the removed `setup.html` fields.

## Acceptance criteria

- A `POST /setup` with only `admin_email` + `admin_password` returns 302, yields
  `deployment_topology == "solo"`, and names the workspace **"Personal"** (not the email domain).
- A logged-in solo user's rendered nav contains **no** `brand-org` chrome (`nav_org` is empty until a
  shared backend is configured).
- The rendered `/setup` page has no topology or GPU-backend chooser.
- An explicit `topology=org` sign-up still records the org backend and derives the email-domain name.
