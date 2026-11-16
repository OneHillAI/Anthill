# Spec: role and account checks on setup, org-wide settings and password reset

Status: implemented. Lane: `pillar:privacy`.

## Problem

Three handlers decided who may act from too little information:

- `POST /setup/account-type` required only a signed-in user and did not check `account_type_chosen`, so any member
  could set the account's topology and organisation name at any time. Only the GET checked the flag.
- `/forgot` decided whether to show the reset link in the page from `_is_solo_install`, which looked at the first
  organisation row on the install rather than at the account being reset.
- `POST /personalize`, `POST /personalize/council` and `POST /setup/model` write settings shared by the whole
  account (`ollama_model`, `solo_compute`, `org_council_members`, `cloud_scrub_pii`) and required only a signed-in
  user.

The scheduler also compared the stored topology to `"solo"` directly, skipping `normalize_topology`, so a legacy
`local` row was not treated as Solo.

## Requirements

1. `POST /setup/account-type` requires an admin and does nothing (redirects to `/`) once `account_type_chosen` is
   true. The first-run flow is unchanged: the account's first user is its admin and the choice is still offered
   once. Solo-to-Org conversion stays at `/personalize/organization/setup`.
2. `/forgot` shows the reset link in the page (no mail server configured) only when the install has one account in
   total. With more than one account it never does, for any address: the page is the same for a Solo account, an
   organisation member, an inactive account and an unknown email (no link, and the "ask your administrator"
   message when no mail server is set). The decision is install-wide, so it cannot depend on whether the email
   matched.
3. The shared settings above are written only by an admin. A Solo owner is the admin of their own account, and an
   account may have more than one admin. A member of an Org account who submits a changed value for the shared model, compute or PII-scrub setting gets 403 and the value
   is unchanged; `POST /setup/model` and `POST /personalize/council` return 403 for such a member. A member's
   `POST /personalize` still saves their own profile and toggles, and an absent `scrub_pii` no longer switches the
   shared setting off. The Settings page renders the PII-scrub toggle disabled for such a member.
4. The scheduler's training tick uses `normalize_topology` for the Solo test.

## Known limits

- On an install with more than one account and no mail server, nobody gets a reset link in the page, including a Solo
  owner on a shared install, who then needs an administrator or a configured mail server to reset a password.
- A member of an Org account who has not yet had the account type chosen is still sent to the account-type page by
  the dashboard and cannot submit it; the admin completes it.
- `training/model_select.py::is_solo_account` compares the stored topology to `"solo"` directly and is not changed
  here (protected path).
- A refused member `POST /personalize` returns a bare 403 after saving their own fields.

## Acceptance

- A member POST to `/setup/account-type` gets 403 and the topology, name and flag are unchanged; an admin POST
  after the choice redirects to `/` with nothing changed; a first-run admin POST still completes the choice.
- On an install with more than one account, `/forgot` shows no link for any address (Solo owner, member, unknown),
  and the pages are byte-identical; a single-account install still gets the link.
- A member POST to `/personalize` with a changed model, compute or scrub value gets 403 and the settings are
  unchanged while their profile is saved; a profile-only POST succeeds and leaves the settings alone; an admin
  (including a second admin, and a Solo owner) can still write them. Members get 403 from `/setup/model` and `/personalize/council`.
- A `local` legacy-topology row is picked up by the training tick.
- Tests: `tests/test_authz_account_and_settings.py` (model-free).
