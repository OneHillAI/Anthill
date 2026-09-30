# Spec: Surface the PII-scrub coverage + a one-click privacy pack

Status: implemented (CLI + backend). Lane: `pillar:privacy`. Issue: #540.

## Problem

The cloud PII scrub (`anthill/hybrid/scrub.py`) has two layers: an always-on regex baseline (structured
identifiers - email, phone, SSN, cards, keys) and an optional Microsoft Presidio NER pass that adds
free-text **names and locations** and government IDs. Presidio is an opt-in extra, and the packaged
desktop app does not bundle it. So on a base install, names and locations **egress in cleartext**, and
nothing tells the admin - the toggle reads as "PII scrubbing is on" with no hint it means
structured-only. A transparency/posture gap, not a code defect (the scrub works as designed).

## Policy

- **Never silent.** `scrub_coverage()` returns a one-line description of what the scrub covers right now
  (structured-only, or "plus names and locations"), driven by `presidio_available()`. It is shown on the
  live egress-consent surface - the CLI `--cloud` per-use consent prompt - so the user sees exactly what
  is and isn't redacted before approving a send.
- **One-click install.** `anthill privacy-pack` installs Presidio + a small spaCy model at runtime and
  re-probes, so name/location redaction can be turned on without a manual pip dance. It only works on a
  source/server install; a frozen (packaged) desktop app can't pip-install into its bundle, so
  `privacy_pack.can_install()` is False there and the command says so instead of failing opaquely.
- **Lighter model.** `_get_analyzer()` now prefers `en_core_web_sm` (~12 MB) over Presidio's default
  `en_core_web_lg` (~560 MB), falling back to the default if a different model is already installed. The
  installer fetches `en_core_web_sm`.
- **Fail-safe unchanged.** Presidio only ever ADDS redactions; without it the regex baseline still runs,
  so the boundary always works.

## Web surface

The Settings page shows a "Cloud privacy" card with `scrub_coverage()` and, when name/location redaction
isn't installed and `privacy_pack.can_install()` is True, a one-click **Install privacy pack** button.
It posts to `POST /privacy-pack/install` (admin, audit-logged), which runs `privacy_pack.install()` in a
background thread; the page polls `GET /privacy-pack/status` and updates when it's ready. A packaged app
that can't install shows the limitation instead of a button. (The web cloud-escalation consent gate is
still not wired - cloud escalation is fail-closed on the web path - so the CLI `--cloud` prompt remains
the live per-send surface; the card is where the web admin sees and closes the gap.)

## Out of scope here (follow-up)

- **Bundling in the desktop build** (so names/locations are scrubbed out of the box in the packaged app)
  is a separate size/build decision.

## Acceptance criteria

- `scrub_coverage()` states the structured-only limitation when Presidio is absent and includes names +
  locations when present.
- The CLI cloud-consent prompt prints the coverage line before asking to escalate.
- `privacy_pack.can_install()` is False in a frozen app; `install()` is a no-op there and otherwise runs
  `pip install presidio-analyzer` + `spacy download en_core_web_sm`, then resets the analyzer cache and
  reports whether name/location redaction is now available.
- `anthill privacy-pack [--status]` reports state, installs when possible, and explains the packaged-app
  limitation otherwise.
- Covered by `tests/test_privacy_pack.py`.
