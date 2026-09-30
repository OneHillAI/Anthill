# Spec: owner-scoped resource access (personal resources are private per user)

Status: accepted. Lane: `pillar:privacy`.

## Invariant

Within one organization (one install), a user's **personal** resources - conversations, folders,
tasks, agents, files, saved snippets, and personal memory - are private to that user. Another member
of the same org must not be able to read, modify, or delete them by id. Knowledge only moves between
users through the explicit promotion / wiki-review gate.

This is distinct from, and weaker than, the **cross-install** boundary: each downloaded install (and
each device profile) has its own data directory plus its own JWT signing secret and at-rest encryption
key, so a session or ciphertext from one install is cryptographically useless on another. The invariant
here is the *within-install, member-to-member* boundary.

## Enforcement rule

Every fetch that returns or mutates a personal resource must filter by the **owner**
(`user_id == int(session["sub"])`), not by `org_id` alone. `org_id` scoping stops cross-org access but
not cross-user access inside an org. An admin may act only where an admin capability is intended (e.g.
`/memory/{mid}/promote`); a plain member never reaches another member's private resource.

## Fixed endpoints (were org-scoped, now owner-scoped) - the #597 IDOR class

Each was fetched by `org_id` only, letting any member read/delete/modify another member's data by
guessing the sequential integer id:

- `POST /memory/{mid}/wiki` - fetch now requires `item.user_id == uid` (or admin), matching
  `/memory/{mid}/delete`. (Was: copied another member's personal memory text into the caller's wiki.)
- `POST /snippets/{id}/wiki` - fetch now requires `Snippet.user_id == uid`, matching
  `/snippets/{id}/edit`. (Was: copied another member's snippet content out.)
- `POST /snippets/{id}/delete` - same owner filter. (Was: destructive cross-owner delete.)
- `POST /chat/{conv_id}/thumbs` - the message is now scoped to the caller's own conversation (joined on
  `Conversation.user_id == uid` and the `conv_id` path), not fetched by global message id.

Already correctly owner-scoped (verified, unchanged): chat read/rename/delete/pin/folder/stream,
folders, tasks (`_task_for_*`), agents (`_agent_for_*`), files (`_files_owner`), and the other
memory/snippet routes.

## Tests

`tests/test_idor_owner_scope.py`: for each fixed endpoint, a second org member is blocked (404 or no
state change) while the legitimate owner still succeeds.
