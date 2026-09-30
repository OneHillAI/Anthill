# Chat's knowledge-scope options must match the account, not a fixed static list

## Status

Fixed.

## Problem

Chat's Options panel has a "which wikis to draw on" scope selector
(`<select id="opt-scope">` in `chat.html`) that always rendered all four options - "All knowledge",
"Personal only", "My teams", "Org wiki" - regardless of the account's actual shape:

- "Org wiki" implies an org backend exists. On a Solo account (no org backend ever configured -
  `planes.is_org_mode` false) there is no org wiki at all; showing the option anyway is misleading.
- "My teams" is a generic plural label shown even when the user belongs to exactly one team, where
  naming it directly ("Rocket Launch") is clearer than a vague plural, and shown even when the user
  belongs to zero teams, where the option means nothing at all.

Reported live: a Solo account with no teams still saw all four options, including "Org wiki".

## Fix

`chat_conv` (`GET /chat/{conv_id}`, the only route rendering `chat.html`) now computes:

- `is_org_mode`: `planes.is_org_mode(cfg)` - the same helper `teams_list` already uses to distinguish
  Solo framing from Org framing.
- `my_teams`: the user's active teams (`Team` rows for `user_team_ids(db, uid)`), ordered by
  creation.

`chat.html`'s scope `<select>` renders "Org wiki" only when `is_org_mode` is true, and the team option
only when `my_teams` is non-empty - labeled with the team's real name when there is exactly one,
falling back to the generic "My teams" when there are two or more (no single name to point at).

The backend's actual scope semantics are unchanged: `wiki_scope=team` already meant "every team the
user belongs to" (`user_team_ids` loop in the `/chat/{conv_id}/stream` handler), not one specific
team - this is a presentation fix, not a behavior change to what gets read.

## Acceptance criteria

- Solo account, no teams: only "All knowledge" and "Personal only" render.
- Solo account, exactly one team: the team option shows that team's real name, not "My teams".
- Any account, two or more teams: the team option falls back to "My teams".
- An account with `org_backend_status` ever configured (validated/planned/provisioning/provisioned/
  error - anything but empty/"unconfigured"): "Org wiki" renders.
- A Solo account with a team but no org: the team option renders, "Org wiki" does not.

## Out of scope

- Changing what `wiki_scope=team` actually reads (still every team, not a per-team selector) - a
  real per-team picker is a separate, larger feature if ever wanted.
- The Settings -> This device / setup compute-chooser surfaces - this only touches Chat's own
  Options panel.

## Tests

`tests/test_chat_knowledge_scope_options.py`: solo+no-teams shows only the two universal options;
one team names it directly; two teams falls back to the generic label; an org-mode account shows
"Org wiki"; a solo account with a team but no org shows the team option without org wiki.
