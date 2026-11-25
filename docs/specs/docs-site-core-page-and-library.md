# Spec: the guide describes the core page, the Library, page history, the answer panel and the memory changes

Status: implemented. Lane: `pillar:knowledge`. Issue: #372. Follows #239, #280, #285, #288 and #300. Related: the specs
`core-page.md`, `remember-this.md`, `memory-archive.md`, `answer-manifest.md`, `remove-an-item.md` and the changelog
fragments of 2026-11-20 to 2026-11-25. Requested by the Knowledge Dev session for rc.2 and extended by the daily docs
check; the maintainer said yes on 2026-11-25. The guide must land before the next beta (after a beta only the changelog
and version files may change).

## Problem

39 PRs merged between the check of 2026-11-20 and 2026-11-25. Six published statements became wrong and eight changes were
not described. Everything below was read from the templates, code or specs on `main` (5b0bc8b).

Wrong now:

1. "The wiki has three tabs: Pages, Principles, Files": the tabs are **Library** and **Principles** (`_wiki_tabs.html`); the
   Library is the one list of pages and kept originals, with search, a Pages/Documents filter, an "AI-written, unchecked"
   filter, **Details**, **Edit**, **Remove**, and a **Removed** list below.
2. "Edit this page ... the earlier text isn't kept": the edit screen says "The earlier text is kept"; a changed page has
   **History** and **Restore this version**.
3. "Pause the whole auto-learning pipeline": the **Auto-remember** switch is only about remembering facts; answered chats
   are kept on the device as possible training examples whether it is on or paused (`memory.html`).
4. MCP "exposes your own wiki, cache, and memory": the cache and memory are "not available in this version" (`mcp.html`,
   `anthill/mcp/server.py`).
5. The profile "guides every response": it is saved to the core page and read only in the owner's own chats (plain chat,
   chat agent mode, a project chat on a Solo account), not in team or organisation chats, Slack, Discord, the widget,
   scheduled tasks or persistent agents (`docs/specs/core-page.md`, requirement 4).
6. The Org index layer: the shared index is not asked for answers unless an administrator turns it on
   (`anthill/wiki/ask.py`).

New:

7. The core page (private, `core-page` in the personal wiki, Profile and Preferences, about 800 tokens, never a source of
   facts, listed under an answer when given, saved to by Settings, Personality).
8. The agent's `remember` proposes one line for Preferences in Needs review (marked as written by an agent); no screen yet
   for adding a line with a preview.
9. The memory archive (a readable file and a data file under `archive/` in the personal wiki folder, written once; nothing
   deleted; automatic memory and saved answers still on; no screen yet for importing lines).
10. The page about an item (**Details**, **About this page**: origin, version, History, links between a page and its
    document, **Used in**).
11. Removal clears more (search entry, saved answers, earlier versions moved into Removed; **Source removed**).
12. The answer panel **Also given** list (rules, profile, remembered notes, number of earlier messages, web pages marked
    "From the web (not checked)", model and mode), agent-mode answers listing the pages they read, and Slack, task and
    agent runs keeping the list.
13. A failed model download says so, with **Try again** (`models.html`).

## Requirements

1. `docs/using-chat-and-knowledge.md` SHALL say the wiki has two tabs, Library and Principles, describe the Library as in
   point 1, and not mention a Files or Pages tab.
2. It SHALL describe Details and About this page including Used in; Edit this page with the earlier text kept; History
   and Restore this version; the refused stale save that keeps your text; and removal as in point 11, including the
   Removed list below the Library.
3. Its Chat section SHALL describe the Also given list and what agent, Slack and task answers keep.
4. Its Memory section SHALL describe the Auto-remember note, the core page (what, where, size, who reads it, never a
   source of facts, listed under an answer), the agent's remember proposal, and the archive file, and SHALL say that adding
   a line yourself and importing from the archive are not on a screen yet.
5. `docs/using-connectors-and-personalization.md` SHALL say MCP offers the wiki only and that the profile is saved to the
   core page and read only in the places in point 5, naming Settings, Personality ("How your AI sounds").
6. `docs/how-it-works.md` SHALL say the Org index is off unless an administrator turns it on.
7. `docs/using-models-and-compute.md` SHALL describe the failed-download message and Try again.
8. The guide SHALL NOT name routes, environment variables or operator commands, SHALL NOT claim a button or screen that
   does not exist, and SHALL say "given" and "used in", never "caused". Existing headings, page order, links between pages
   and the publish whitelist SHALL NOT change; the Memory section gains two sub-headings.

## Known limits

- The routes `/core-page/remember`, `/core-page/remember/preview` and `/core-page/memory-import` have no screen; they are
  in the specs, not in the guide. The three specs still say "Status: proposed" although the code is merged; that wording
  is not copied.
- Whether the Library lists the core page was not checked, so the guide does not say how to open it.
- `docs/SYSTEM_IMPACT_LOG.md` still mentions a Files tab; it is frozen history and is not edited.
- Not changed: the model-list changes (#264, #318 to #324, #327, #336, #345) that alter which models are offered; the guide
  names no model and the fit labels are unchanged.

## Acceptance

- Each statement matches the template, code or spec named above.
- A search of the published pages for "three tabs", "Files tab", "under Files", "isn't kept", "auto-learning pipeline",
  "wiki, cache, and memory" and "guides every response" finds nothing wrong.
- The doc tests pass and `node docs-site/scripts/collect-docs.js` copies all eight pages.
- After merge, the live pages show the new text.
