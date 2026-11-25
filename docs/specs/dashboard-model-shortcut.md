# Spec: the dashboard's model shortcut says what it does

Status: implemented. Lane: `pillar:platform`.

## Problem

The Quick actions card of the dashboard (administrators) had a solid green button, the loudest thing on the page,
labelled "Update your model" (or "Update your council"). It always showed and only opened the model settings, so it
looked like an update was waiting when there was none.

## Fix

1. The row says "Change your model" (or "Change your council" when the account has more than one model), which is
   what it does: it opens the model settings, as before (`/personalize#model`).
2. It is an ordinary quiet row like "View metrics": no solid fill. The filled style is removed, because nothing else
   used it, and a solid button reads as something waiting to be done.
3. Nothing else changes: who sees the card (administrators), where the row leads, and the other rows.

## Acceptance

- The dashboard says "Change your model", never "Update your model".
- The row has no filled style; a council says "Change your council".
- A member who is not an administrator does not see the card.
- Looked at on a running copy at desktop widths.
