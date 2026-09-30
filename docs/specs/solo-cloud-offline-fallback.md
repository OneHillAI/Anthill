# Solo-cloud offline fallback: prompt, do not silently downgrade

## Problem

A Solo account chooses its compute once (local or cloud/VPC) - "one model per account". A Solo-cloud
account runs its chats, tasks, and agents on the VPC model, but its wiki is **always local** (the VPC
provides only inference). So when the VPC is unreachable, the local model can serve the same wiki at lower
quality - a seamless fallback. Today the chat UI treats every Solo chat as "local, never gated", and the
router silently falls through to the local model only when no endpoint is configured. For a Solo-cloud
account whose configured VPC is momentarily unreachable, that means a silent model downgrade mid-use (or a
failed call), with no signal to the user. Switching to a weaker model without asking is pointless surprise.

## Requirements (EARS)

- The system SHALL keep a Solo account's wiki local regardless of whether its model is local or on a VPC.
- WHEN a Solo-cloud account's VPC model endpoint is unreachable, the system SHALL prompt the user to either
  use the local model now or wait for reconnection, and SHALL NOT silently downgrade to the local model.
- WHEN the user chooses "use my local model now", the system SHALL run the Solo turns on the on-device
  model with the same (local) personal wiki, and SHALL mark nothing ephemeral.
- Once chosen, the local fallback SHALL persist for subsequent turns until the VPC endpoint is reachable
  again (so the user is not re-prompted every turn while offline); WHEN the VPC becomes reachable the
  system SHALL return to the VPC model and clear the temporary local choice automatically.
- The system SHALL ignore the local-fallback choice for Org and Team runs (there is no local option at org
  scale; offline, the Org plane simply waits).
- The local-fallback choice is a RUNTIME fallback only: it SHALL NOT change the account's configured
  compute (a Solo-cloud account stays Solo-cloud, and reverts to the VPC model on reconnect).

## Acceptance criteria

- `plane_inference(..., prefer_local=True)` on a Solo-cloud config returns the local model + `wiki_scope`
  "personal", even when a validated VPC endpoint is present; it also overrides the personal-mode org borrow.
- `plane_inference("org", ..., prefer_local=True)` still returns the org endpoint (prefer_local ignored).
- The chat surface, for a Solo-cloud conversation whose backend probe is not "ready", shows a prompt with a
  "use your local model now" action and otherwise holds sending; a local-only Solo chat is never gated.
- After the user picks local, `use_local=1` is sent on each turn until a "ready" probe, at which point the
  UI clears the choice and returns to the VPC model.
- `use_local=1` on the chat stream routes that turn through `prefer_local`.
