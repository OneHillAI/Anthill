# Settings: one Solo home per account (models / wiki / tuning), cleaner solo -> project -> org spine

## Status

Phase 1 of aligning the settings + menu with the built Solo / Project / Org model (one model per
account). This phase collapses the fragmented personal-settings surface into one Solo settings home and
cleans the rail. Later phases (tracked, not in this PR): move org-behaviour knobs (proactivity, wiki
auto-promote) from `/settings` into the Org hub; fix the chat-rail plane labels for one-model-per-account;
wire real Solo tuning (the self-hosted server trains).

## Problem

The General nav group had THREE overlapping personal-settings entries - Solo settings (`/personalize`),
Local model (`/models`), and Settings (`/settings`) - and `/settings` even duplicated the Solo-compute
choice already on Solo settings. That fragments "your one model per account" across three pages and a
duplicated control, and the Solo settings page used stale "Organization -> Cloud & model" naming and had
no place for connectors or tuning.

## Requirements (EARS)

- The system SHALL present ONE Solo settings home for a user's personal setup (compute, local model,
  persona, personal knowledge, connectors); `/models` and the advanced `/settings` knobs SHALL be reached
  from within it, not as their own rail items.
- The Solo-compute choice SHALL live on the Solo settings home; saving the advanced `/settings` knobs
  SHALL NOT reset it (an absent compute field is preserved, never coerced back to local).
- WHEN the account's compute is cloud, Solo settings SHALL surface Tuning (the self-hosted server trains);
  local-only compute SHALL NOT show a tuning path (a weak fallback is not tuned).
- The nav SHALL keep a clear solo -> project -> org spine (Solo settings under General, Org settings hub
  under Organization, Project settings on the project page).
- No org-scale surface SHALL regress: the Org settings hub and Project settings are unchanged in this phase.

## Acceptance criteria

- The rail shows a single `href="/personalize"` personal-settings entry and no `Local model` or
  `href="/settings"` rail items; `/models` and `/settings` pages still render and are linked from Solo
  settings.
- Posting `/settings` without a `solo_compute` field leaves `OrgSettings.solo_compute` unchanged.
- Solo settings links to `/models`, `/settings`, and `/connectors/mcp`, and shows a Tuning note only when
  `solo_compute == "cloud"`.
- The full test suite stays green.
