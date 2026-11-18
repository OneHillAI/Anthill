# Spec: five published statements that no longer match the app

Status: implemented. Lane: `pillar:platform`. Issue: #239. Related: #212, #201, #182, #216, #192. The app-side wording bug behind item 2 is #241.

## Problem

docs.anthill.run publishes eight pages from `docs/` (the whitelist in `docs-site/scripts/collect-docs.js`). The daily
docs check of 2026-11-17 compared them with `main` and found five statements that describe something the app does not do:

1. `docs/setup.md` said first launch asks for an org name, an email and a password, then offers "Just evaluating
   (solo)" or "Set up my organisation". The app asks for email, name (optional) and a password of at least 12
   characters (`setup.html`), then offers "Just me" or "Or set up an organisation" with an optional name
   (`setup_account_type.html`).
2. `docs/USING_ANTHILL.md` said an organisation is created "from the dashboard". The Solo dashboard has no such
   control. The only place is Settings, on the Organisation tab, in the "Set up an organisation" card (admin only, a
   required name, a two-click confirm, no way back; route `/personalize/organization/setup`).
3. `docs/setup.md`, `docs/using-connectors-and-personalization.md` and `docs/how-it-works.md` listed Google Drive as
   a tool to connect. Since #212 its catalog entry is tier `community`, searches Drive by file name only, cannot read
   or import documents, and is out of the import pickers.
4. `docs/using-chat-and-knowledge.md` and `docs/how-it-works.md` said every upload becomes a summary page. Since #201
   a `.md` or `.txt` of at most 1,500 characters is filed exactly as written, with no model call; since #182 a longer
   note keeps its own first heading as the title and its quoted passages word for word.
5. `docs/how-it-works.md` said a close-enough cache match returns the earlier answer. Since #216 a cached answer is
   dropped when a page it cited changed or went missing, or when a page about the question was added or changed later.

## Requirements

1. `docs/setup.md` SHALL describe first launch as email, optional name and a password of 12 or more characters, then a
   choice of "Just me" or "Set up an organisation" (name optional), and SHALL say that switching from solo later is
   done in Settings, on the Organisation tab, and cannot be undone. It SHALL NOT mention "Just evaluating" or ask for
   an org name on the first form.
2. `docs/USING_ANTHILL.md` SHALL say an organisation is created in Settings, on the Organisation tab, with "Set up an
   organisation", and SHALL keep the one-way statement.
3. No published page SHALL list Google Drive as a tool to connect. `docs/using-connectors-and-personalization.md`
   SHALL say in one sentence that it is a community connector that only searches file names and cannot read or import
   documents yet.
4. `docs/using-chat-and-knowledge.md` and `docs/how-it-works.md` SHALL say that a short plain-text note (`.md` or
   `.txt`, up to 1,500 characters) is filed as written, and `docs/using-chat-and-knowledge.md` SHALL say a longer one
   keeps its own first heading and quoted passages.
5. `docs/how-it-works.md` SHALL say a saved answer is dropped when a page it cited changes or goes missing, or when a
   page about the question is added or changed later.
6. Page headings, page order, links between pages and the publish whitelist SHALL NOT change. No claim SHALL be added
   that the code does not back; the 200 pages, 25 MB and 10 minute upload limits quoted in a changelog fragment are
   unverified and are left out.

## Known limits

- The first-run page still says "later from the dashboard" (`setup.html`). That is an app text bug, tracked in #241.
- Other behaviour added this week (duplicate-upload choices, the extra upload statuses, the "text read from it" link,
  download progress and cancel, backup details) is not documented here. It is a separate pass.

## Acceptance

- Each of the five statements matches the template, catalog entry or spec named above.
- The docs-site build succeeds (`npm ci && npm run build` in `docs-site/`) and the five pages keep their headings
  and links.
- A search of the five pages for "Just evaluating", "from the dashboard" and "Drive" finds only the Drive sentence on
  the connectors page.
- After merge, the live pages show the new text.
