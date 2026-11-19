# Spec: the guide describes removing a document or a page, and agent-proposed pages waiting for review

Status: implemented. Lane: `pillar:knowledge`. Issue: #300. Follows #239, #280, #285 and #288. Related: #287, #295, #298.

## Problem

Two merged PRs changed what a person sees and neither touched the published pages:

1. #287 added removing a document or a page with a way back for 30 days: a **Remove** link on each document under Files
   and **Remove this page** on a page (shown to someone who can edit that wiki); a screen that says what goes and what
   stays; a **Removed** list with the days left, **Restore** and **Delete now**; deletion after 30 days
   (`RETENTION_DAYS = 30` in `anthill/wiki/removal.py`); and the limit that a delete cannot reach exports, backups or
   what a model was trained on (`wiki.html`, `wiki_page.html`, `wiki_remove.html`).
2. #295 made a page an agent proposes wait in the review queue of the wiki it belongs to, marked as written by an
   agent, instead of being written at once; an agent with no person to review its pages cannot write
   (`docs/specs/agent-wiki-proposals.md`). `docs/using-automation.md` only said that writing to a shared wiki does not
   happen silently.

(#291 and #298 changed the chat answer block and page editing and updated the guide in their own PRs; they need no
change here.)

## Requirements

1. `docs/using-chat-and-knowledge.md` SHALL say where the Remove links are, that removal works in the wikis where a
   person can edit, what goes and what stays when a document or a page is removed, that answers stop using it at once,
   the Removed list with the days left, Restore and Delete now, the 30 days, and what a delete cannot reach.
2. It SHALL say that **Needs review** also holds pages an agent proposed, marked as written by an agent.
3. `docs/using-automation.md`, Agents, SHALL say that an agent never writes to a wiki directly, where its page waits,
   that nothing changes until a person approves it, that it does not replace an existing page on its own, and that an
   agent with no person to review cannot write.
4. Every statement SHALL be one the templates, constants or the spec named above show. Headings, page order, links
   between pages and the publish whitelist SHALL NOT change.

## Known limits

- The item records and `anthill knowledge inventory` are operator tools and are not described.
- The Solo reader's route to the Backup page is a separate app issue (#301); the sentence in `docs/setup.md` stays as it
  is until that lands.

## Acceptance

- Each statement matches the template, constant or spec named above.
- The doc tests pass and `node docs-site/scripts/collect-docs.js` copies all eight pages.
- After merge, the live pages show the new text.
