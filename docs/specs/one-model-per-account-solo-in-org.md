# One model per account: a Solo chat in an org account runs on the org model (Finding A)

## Status

Decided (founder). Resolves the long-standing "Finding A": in an org account, what model does a Solo chat
run on? Answer: **the org model** - one model per account; the tier decides *sharing*, not the model.

## Principle

The account's tier/compute is a single choice; a chat's tier (Solo / Project / Org) decides **who it is
shared with**, not which model runs it:

- **Solo account:** the model runs on your **VPC** if you set one up, else **local**.
- **Org account:** **everything** runs on the **org model** - a Solo chat too. It is just **private**
  (your own wiki, not shared, not trained); a Project chat is shared with the project wiki; an Org chat is
  shared with the org wiki. Same model underneath (like Claude: one model; a Solo chat is just private).
- The **local model is the fallback** (offline), never a second concurrent primary.

## Requirements (EARS)

- WHEN a conversation is in the Solo plane AND the account is an organization (a shared org backend is
  configured), the system SHALL run it on the **org model** with the user's **personal (private) wiki**
  (`wiki_scope = personal`), and SHALL mark the run **ephemeral** - nothing retained org-side, trained into
  the org model, or visible to anyone else.
- The system SHALL fall back to the local model only when the org endpoint is unreachable (the offline
  `prefer_local` choice, or no endpoint).
- A Solo account's own **VPC** compute (`solo_compute = cloud`) SHALL take precedence and is NOT ephemeral
  (it is your own cloud, not a shared org model).
- The Org plane SHALL keep excluding personal context (the org wiki, shared).
- The old **`personal_mode_on_org_model`** opt-in SHALL be **retired** (this behaviour is now the default).
  The `User` column is kept for back-compat but no longer written or read for routing.

## Implemented

- `plane_routing.plane_inference`: removed the `personal_mode_org` param; added the org-account Solo branch
  (org model + personal wiki + ephemeral), after the solo-cloud branch, before the local fallback.
- `app.py` chat route: dropped the `personal_mode_org` computation + pass.
- `personalize.html`: replaced the "use the org model for my personal chats" toggle with a short note
  explaining the default; the `/personalize` POST no longer writes the flag.
- Chat rail: "Solo (local)" -> **"Solo (private)"**, the conversation suffix likewise, and "+ Org model"
  -> **"+ Org chat"** (the model is the same; the button starts a *shared* org chat).

## Follow-up

- Fuller chat-rail reframe (group by project; private/shared affordances) - separate pass.

## Acceptance criteria

- `plane_inference("solo", <org-account cfg>)` returns the org endpoint, `wiki_scope == "personal"`,
  `use_personal_context is True`, `ephemeral is True`.
- `solo_compute="cloud"` still returns the VPC endpoint, `ephemeral is False`.
- `prefer_local=True` in an org account returns the local model.
- A Solo account with no backend returns the local model, not ephemeral.
- The chat rail shows "Solo (private)" (not "Solo (local)").
