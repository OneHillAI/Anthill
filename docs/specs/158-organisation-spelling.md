# Spec: one spelling of "organisation" in everything a person reads

Status: implemented. Lane: `pillar:feature`. Issue: #158. Related: #188 (Solo wording).

## Problem

#158 reported that the product switched between "organization" (setup, projects, Users, dashboard) and
"organisation" (Settings, navigation). The project style is British (`conventions.style.spelling: british` in
`.asdd.yml`), and #188 already fixed the Solo wording; this finishes the spelling half. About 100 visible
occurrences used the US form against about 45 of the British one.

## Requirements

1. Text a person reads says "organisation", "organisations" and "organise/organised/organising", in these places:
   the Jinja templates (visible text, titles, placeholders, aria-labels, JS strings), the PWA manifest, the user
   guides under `docs/` (Slack bot, using Anthill, how it works, setup, use or build on, chat and knowledge,
   models and compute), and the strings Python sends to a person: email bodies, Slack and Discord replies,
   CLI help, MCP tool descriptions, error and notice messages, the "Organisation" principles label shown on the
   wiki page, and the default name "My organisation" given to an org created without one.
2. The Solo upgrade pointer on the Projects pages reads "Set up an organisation" (the tests that pin it move with
   it).
3. Nothing that is not prose changes: routes and URLs (`/settings/organization...`, `/personalize/organization/setup`),
   template and function names, the `Organization` model and `organizations` table, form fields, JSON keys, config
   keys, and comments and docstrings.
4. `tests/test_organisation_spelling.py` fails if a shipped template, the manifest or one of the guides above
   contains the US spelling outside comments and the route and template-name identifiers listed in 3.

## Known limits

- Scope is the "organi[sz]" family only. Other `-ize` words ("authorize", "customize", "summarize") are left as
  they are to keep the change reviewable.
- Not changed, because they are model prompts or protected paths and need a human decision: the prompt text in
  `anthill/research.py`, `anthill/search/web.py` (pinned by `tests/test_web_source_labels.py`),
  `anthill/agent/executor.py`, `anthill/contribute/triage.py`, the `_HELP_SYSTEM` prompt in `anthill/web/app.py`,
  everything under `anthill/wiki/` and the Settings messages in `anthill/training/backends/endpoint.py`. The
  model may echo the US spelling in a reply until those are changed.
- The intent matcher in `anthill/agent/intent.py` only recognises "use the organization model"; it should also
  accept the British spelling.
- Existing organisations keep the name they already have, including a stored "My organization".
- Top-level project documents (`README`, `AGENTS.md`, `ARCHITECTURE.md`, `SECURITY.md`), `docs/RELEASE_SIGNING.md`
  (Apple's own term), the system impact log and other specs are not touched.
- The guard test covers templates, the manifest and the listed guides, not Python strings; those were changed by
  hand and are covered only by the existing page and message tests.

## Acceptance

- No template, the manifest or the listed guides contains "organization" in prose (guard test).
- The setup screen, Projects, Users, dashboard, wiki titles and sidebar render "Organisation" and the existing
  page tests assert the new wording.
- Routes, form fields and the database are unchanged: every `/settings/organization` URL still resolves.
- Tests: `tests/test_organisation_spelling.py`, plus the existing page tests updated to the new spelling (model-free).
