# Spec: the pages under a chat answer are links

Status: implemented. Lane: `pillar:knowledge`. Builds on `answers-remember-sources.md`.

## Problem

Under an answer, "Pages in context: hollis-bakery-operations-handbook" named the wiki pages Anthill gave the
model, but only as text. To look at one the person had to leave the chat, open Knowledge, and find the page in
the right wiki (personal, project or organisation).

## Fix

1. Each page name under an answer is a link that opens that page in a new tab. This is the same live while the
   answer streams and after a reload.
2. The link is `/wiki/open/<slug>`. The answer stores only the slug, so the server looks for it in the person's
   own wiki, then the wikis of the projects they are in, then the organisation's (not for a Solo account), and
   redirects to `/wiki/page/<slug>?scope=...`. Only wikis the person can already read are searched.
3. A page that was deleted or renamed since the answer gets a plain "not found" (404), not a wrong page.
4. The wording, the tooltip ("It does not show which of them the answer relied on.") and what is stored are
   unchanged. Documents in context are not linked.

## Acceptance

- Under a reloaded answer, "cabinet" is a link to `/wiki/open/cabinet` that opens in a new tab.
- That address redirects to the page in the wiki that holds it, and returns 404 when no wiki has it.
- The live stream builds the same links, with the page name escaped.
