# Spec: Install-wide controls belong to the install owner

Status: implemented. Lane: `pillar:privacy`. Depends on `docs/specs/invite-only-signup.md`, which defines the
install owner, the shared-server rule and `install_scope_allowed`.

## Problem

Device profiles (separate data folders on this machine) and the built-in skills belong to the whole install,
not to one organisation. Creating, renaming or deleting a profile needed only a sign-in, and any
organisation's admin could delete a built-in skill. On a desktop that is fine. On a server shared by several
organisations it should be the install owner's decision.

## Requirements

- The rule is `install_scope_allowed` from the sign-up spec: the install owner always; anyone else only on a
  server that is local and only from a local request. A server is local only when the desktop app marked it
  (`ANTHILL_LOCAL_ONLY=1`, see the sign-up spec); `anthill web`, the container and a hand-started `uvicorn` are
  shared, and so is any server with Remote access on. On a shared server only the owner is trusted, because a
  forwarder on the same machine (a plain proxy, `ssh -R`, `socat`, `ngrok tcp`) makes a remote request look
  local.
- Creating, renaming and deleting a profile (`POST /profiles`, `/profiles/{id}/edit`, `/profiles/{id}/delete`)
  follow the rule. Anyone else gets 403 and nothing changes.
- Deleting a built-in skill (`POST /skills/{slug}/delete` with scope `builtin`) needs an admin who passes the
  rule. Skills of an organisation, a project or a person are unchanged.
- The pages show only what the viewer can use: the profiles page and the account profile page show the forms
  (create, rename, delete, open path) only to someone who passes the rule, otherwise the profile names and a
  line saying who manages them; the Skills page shows Delete on a built-in skill only to someone who passes
  the rule.
- On a desktop with Remote access off, every request is local and nothing changes there. Once Remote access is
  on the server is shared and only the owner has these rights. A plain TCP forward to a desktop that adds no
  header is the remaining gap described in the sign-up spec.

## Acceptance criteria

- Install owner over the network: can create, rename and delete a profile and delete a built-in skill.
- Another organisation's admin and a member over the network: 403 on each, the profile folder and the skill
  are untouched.
- A local request on a server that is local (a marked desktop), by any signed-in user: allowed.
- With Remote access on, with no bind address recorded, or with no desktop mark (`anthill web` style), a loopback client with a loopback `Host` and no
  forwarding headers (what a plain proxy or `ssh -L` produces) who is not the owner: 403 on all of them.
- A tunnel to a local port is not local: public `Host` or forwarding header gives 403 for a non-owner.
- An owner who is no longer an active admin has no install-wide rights.
- The Skills page and both profile pages hide the controls from someone who cannot use them.

## Tests

`tests/test_install_owner_controls.py`; `tests/test_profiles_page.py` runs as the machine's own user.
