# Spec: Invite-only sign-up on shared servers

Status: implemented. Lane: `pillar:privacy`. Protected path touched: `scripts/` (three local launchers, see below).

## Problem

`/setup` creates a new account, and an organisation of its own, for anyone who opens it (#481). That is right
on a desktop, where the server answers only to the person at the machine. On a server that other people can
reach (a container, an appliance, an `anthill web` behind a proxy, or a desktop with Remote access on),
account creation should be a decision of the people who run it.

## Requirements

### Local or shared
- A server is **local** only when the desktop app says so. The desktop entry point sets `ANTHILL_HOST=127.0.0.1`
  and `ANTHILL_LOCAL_ONLY=1` just before `uvicorn.run`; the server is local when both are set and the host is
  a loopback address. Every other way of starting it is **shared**: `anthill web`, the container entry point
  (which records its address in `ANTHILL_HOST` and nothing else) and `uvicorn` started by hand. A loopback bind
  says nothing about who reaches the server, because a reverse proxy, `ssh -R`, `socat` or `ngrok tcp` on the
  same machine deliver remote requests to it.
- A server that is local is shared anyway while any organisation has Remote access on.
- An operator who runs `anthill web` for themselves on their own machine opts in with `ANTHILL_LOCAL_ONLY=1`
  (documented in `CONTRIBUTING.md`) and must not do so behind a proxy or tunnel. The launchers that exist for a
  person at a machine set it unless the environment already has a value (`${ANTHILL_LOCAL_ONLY-1}`): `start.sh`,
  `make alpha`, the macOS auto-start agent (re-run `make autostart` after upgrading, because the setting is
  written into the agent), `scripts/start.ps1` (which sets the variables for its own run and puts them back)
  and `scripts/test-setup.sh`. The last three are under `scripts/`, a protected path.
- A **local request** is one from the machine itself: a loopback client address, a loopback `Host`, and none of
  the headers a proxy or tunnel adds (`Forwarded`, `X-Forwarded-For`, `-Host`, `-Proto`, `-Port`, `X-Real-IP`,
  `X-Client-IP`, `True-Client-IP`, `CF-Connecting-IP`, `Via`).
- **Remaining gap, stated plainly.** A server marked local trusts a request that looks like the machine's own
  (loopback client, loopback `Host`, no forwarding header). That is the desktop app and the five launchers above.
  A proxy that adds no forwarding header (a default nginx `proxy_pass`, HAProxy without `forwardfor`, a
  TLS-terminating TCP proxy) or a TCP forward (`ssh -R`, `socat`, `ngrok tcp`) in front of one of them delivers
  remote requests that look exactly like that, and they are trusted. The README and `docs/setup.md` say not to
  do it unless Remote access is switched on first (provider "manual", which makes the server shared) or
  `ANTHILL_LOCAL_ONLY=0` is set (the launchers only; `desktop.main` always sets 1). Servers started any other way are shared and trust only the install owner.

### The install owner
- The install owner is an explicit record: `InstallSettings.owner_user_id` in a one-row table with the fixed
  primary key 1 (a new table, added by `create_all`). The very first account sets it, in the same transaction as
  the insert of that account (form and OAuth), and the recording is audited (`install.owner_recorded`). A later
  account never takes it.
- The owner counts only while it is an **active admin**. An owner who is inactive or no longer an admin is no
  owner: nothing install-wide can then be changed, and sign-up on a shared server stays closed.
- Start-up: an install from before the record gets its first active admin; a recorded owner that never became
  an active admin (an account still waiting for its verification link) is replaced by the first active admin.
  Both are audited.
- **Getting it back.** On the host, `anthill owner set EMAIL` makes an active admin the owner (and `anthill owner
  show` prints it; both take `--db` and `--profile`, find the database from `ANTHILL_DB`, then
  `ANTHILL_HOME/anthill.db`, and refuse to run against a database file that does not exist). `ANTHILL_INSTALL_OWNER=<email>`
  does the same at start-up as a one-off: an unknown email is ignored (start-up carries on with the recorded
  owner and the backfill below), and a change of owner by it is audited (`install.owner_set_by_host`) and
  logged as a warning. Both need the host, not a sign-in.
- The owner cannot be demoted or deactivated (`/users/{id}/role` and `/deactivate` refuse), and nobody but the
  owner can issue a password reset link for the owner (`/users/{id}/reset`). The Users page says why. Only the
  owner can hand the install to another active admin **of the owner's own organisation** (`POST
  /settings/install-owner`, audited), and the list shows only those admins. Someone from another organisation
  becomes owner through the host command.
- An organisation sign-up that waits for the first admin to verify the address records that account as the
  owner straight away; it takes effect when the address is verified. Until then install-wide controls are shut
  and shared-server sign-up stays closed. The verification link, not `/setup`, is how the operator gets in.

### Sign-up (`GET` and `POST /setup`)
- The very first account is always open.
- Shared server, later accounts: by invitation, unless the install owner has switched sign-up on
  (`InstallSettings.signup_open`). With users but no active owner it stays by invitation.
- Server that is local, later accounts: only from a local request; a request that carries a proxy header, or
  a public `Host`, is refused. See the remaining gap above.
- When closed, `GET /setup` shows "Sign-up is by invitation on this server. Ask an administrator to invite
  you." and no form, and `POST /setup` creates nothing and answers 403. Invitations (`/invite/{token}`) and
  OAuth (first account only) are unchanged.
- The owner's switch is a checkbox on the Remote access page, "Let anyone who can reach this server create an
  account" (`POST /settings/signup`, owner only, audited). Inviting needs the organisation's backend to be set
  up, so the card says so and points to the switch for the time before that.

### Remote access belongs to the owner
- `POST /settings/remote` needs the install owner to switch it on or change it, and `/settings/remote/stop`
  needs the owner, whether or not the request looks local. An admin of any other organisation can switch their
  own organisation's setting **off**: that sets only `remote_access_provider` to "off" and never writes the
  token or the URL. An admin of the owner's own organisation who is not the owner gets 403, because the owner's
  row is the one that keeps the install's tunnel running. The owner's off turns it off for every organisation,
  so another organisation's earlier setting cannot keep the server open. The page shows the form only to the
  owner; admins of other organisations see who controls it and, if it is on for their organisation, a button
  to switch it off.

### The rule for install-wide changes
- `install_scope_allowed`: the install owner always; anyone else only on a server that is local and from a
  local request. On a shared server only the owner is trusted. (Used by the profile and built-in skill controls
  in `docs/specs/install-owner-controls.md`.)

## Acceptance criteria

- Desktop (marked local): two sign-ups from the machine succeed; a second account through a forwarder (public
  `Host` or a proxy header) gets 403 and nothing is created.
- `anthill web` style (loopback bind, no mark), loopback client, `Host: 127.0.0.1`, no headers: the first
  sign-up succeeds and the second gets 403.
- Shared server (bind address, missing host, no mark, or Remote access): the first sign-up succeeds, the second
  gets 403 and creates nothing, and the page has no form.
- Each proxy header in the list marks a request as not local.
- The first account is the owner in the same transaction (a failure after the insert leaves neither); a later
  one is not; the first OAuth account is the owner; an unverified first admin becomes effective on `/verify`.
  Start-up gives an older install its owner, replaces a never-activated one, and honours
  `ANTHILL_INSTALL_OWNER`; `anthill owner set` works; all of it is audited.
- The owner cannot be demoted, deactivated or reset by anyone else. Only the owner can transfer, only to an
  active admin of the owner's organisation.
- A non-owner gets 403 turning Remote access on, from a local-looking request or not; an admin of another
  organisation can switch their own organisation off (only the provider changes, never a token or URL) without
  stopping a tunnel another organisation's setting keeps running; a second admin in the owner's organisation
  gets 403 and the tunnel keeps running; the owner's off clears every organisation.
- An invitation sent through `POST /users/invite` lets the guest join a closed server.
- `desktop.main` sets `ANTHILL_HOST` and `ANTHILL_LOCAL_ONLY`; `server.main` and `anthill web` set
  `ANTHILL_HOST` and not the mark; each at the moment `uvicorn.run` is called.

## Known limits

- The start-up backfill picks the lowest-id active admin across all organisations, which on an install from
  before the record is not necessarily the person who set it up. `anthill owner set` corrects it.
- Two first sign-ups at the same moment on a fresh shared server can both pass the "no account yet" check.
- The profile routes act on the profiles of the one database on the machine, shared by every organisation on
  it; that is why they are an install-owner matter (see `docs/specs/install-owner-controls.md`).
- `/backup/export` and `/backup/restore` were open to any organisation's admin; they now need the install owner
  (see `docs/specs/backup-install-owner.md`).
- The remaining gap above.

## Tests

`tests/test_invite_only_signup.py`. Existing sign-up and verification tests run as the machine's own user on a
marked desktop (`tests/conftest.py`); the Remote access fixture records its admin as the owner.
