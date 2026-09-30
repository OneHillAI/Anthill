# Spec: revoke a granted escalation consent, and stop it leaking across providers

Status: implemented
Lane: `pillar:model`
Relates to: `docs/specs/expert-tier-compound-compute-and-escalation.md` (the attachment +
`escalation_consented` this operates on), `docs/specs/manual-escalation-always-offered.md` (the
"Always" click in chat.html this closes the loop on), `docs/specs/ask-provider-now.md` (the other
place "Always" can be granted from).

## 1. Introduction

A live chat session surfaced four UX questions about the escalation-offer feature; this spec answers
question #2: **once someone clicks "Always" for an inference provider, how do they turn it back off?**

Auditing `cfg.escalation_consented` (OrgSettings) turned up two gaps, not one:

1. **No revoke path at all.** The flag is set to `True` from two places -
   `/chat/{id}/escalate-confirm` and `/chat/{id}/ask-provider-now`, both only when the human just clicked
   "Always" on a real, named disclosure ("this leaves your device, sent to {provider}"). Nothing in
   Settings ever read or wrote it, so the only way back to "ask me each time" was clearing the whole
   provider attachment in `_apply_escalation_attachment` - which also throws away the saved API key.
2. **Cross-provider consent leak.** `_apply_escalation_attachment` never touched
   `escalation_consented` when `escalation_provider` changed (or was cleared). An account that had
   clicked "Always" for Berget, then later switched the attachment to Groq, kept firing Groq
   automatically with no consent ever given for Groq specifically - the flag doesn't name a provider, so
   switching providers silently carried an old disclosure's consent onto a new one it was never granted
   for.

## 2. Requirements

### R1 - Settings can turn a granted consent back off
- WHERE `cfg.escalation_consented` is `True`, THE SYSTEM SHALL show a "Turn off" control in Settings ->
  Model, next to the ask/automated mode picker.
- WHEN that control is used, THE SYSTEM SHALL clear `escalation_consented` immediately (its own request,
  not bundled into the surrounding Save button), leaving the provider attachment and API key untouched.

### R2 - Settings can never grant consent, only revoke it
- THE SYSTEM SHALL NOT expose any Settings field or route parameter that sets `escalation_consented` to
  `True`. Granting it stays exclusive to the chat-runtime "Always" click, which is the only place the
  actual third-party disclosure is shown right before it takes effect.

### R3 - Consent does not carry across a provider change
- WHEN `escalation_provider` changes to a different provider, THE SYSTEM SHALL reset
  `escalation_consented` to `False` - the new provider has never had its own disclosure consented to.
- WHEN `escalation_provider` is cleared (the account opts out of the attachment entirely), THE SYSTEM
  SHALL also reset `escalation_consented` to `False`.
- WHEN the same provider is simply re-saved (e.g. only the ask/automated mode changes), THE SYSTEM SHALL
  leave `escalation_consented` unchanged - R3 targets an actual provider change, not every settings save.

## 3. Design

- **`anthill/web/app.py`'s `_apply_escalation_attachment`**: the blank-provider branch now also sets
  `cfg.escalation_consented = False`; the attach branch compares the OLD `cfg.escalation_provider`
  against the new value *before* overwriting it, and resets consent only when they differ (R3).
- **New route `POST /personalize/escalation-consent-revoke`**: `_require_user` + `_cfg`, sets
  `escalation_consented = False`, commits, audit-logs `personalize.escalation_consent_revoked`, returns
  `{"ok": true}`. No form fields - there is nothing to grant, only to clear (R1, R2).
- **`_inference_provider.html`**: a `cc-consent-status` row, sibling to the existing `cc-esc-mode-wrap`
  mode picker (same `{% if not in_setup %}` guard - this isn't a first-run decision either), rendered
  hidden unless `escalation_consented` is true for the currently-connected provider. Its "Turn off"
  button calls `ccRevokeConsent()`, a plain `fetch` POST (mirrors `personalize.html`'s existing
  `trainNow()`/`soloCouncil()` pattern) that hides the row on success without a full page reload.
  `ccPickEscalationProv` hides the row whenever the selected card isn't the already-connected one (a
  provider switch pending save), and `ccClearEscalation` hides it unconditionally - both optimistic,
  matching R3's server-side reset that a subsequent save will apply.
- **`personalize_get`**: passes `escalation_consented` into the template context alongside the existing
  `escalation_provider`/`escalation_mode`.

## 4. Tasks

- [x] `_apply_escalation_attachment` resets consent on a provider change or clear (R3).
- [x] `POST /personalize/escalation-consent-revoke` (R1, R2).
- [x] `_inference_provider.html`: status row + revoke control, wired into the existing pick/clear/
  hydrate lifecycle.
- [x] Tests: switching providers resets consent; clearing resets consent; re-saving the same provider
  does not; the revoke route clears consent and cannot be made to set it; Settings shows/hides the
  control based on consent state.
- [x] `docs/SYSTEM_IMPACT_LOG.md` entry (this PR).

## 5. Out of scope

- **#4's "reload to rate/save" button** and **a difficulty-based (rather than time-based) highlight for
  the provider offer** - separate, already-deferred UX follow-ups from the same live session; not
  addressed here.
- **Per-provider consent storage** (e.g. remembering "Always" independently for Berget AND Groq at the
  same time) - `escalation_consented` stays a single account-wide flag, reset on any provider change;
  only one provider is ever attached at a time today, so there is nothing to store per-provider yet.
