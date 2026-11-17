# Spec: an answer keeps the pages it was given

Status: implemented. Lane: `pillar:knowledge`. Knowledge roadmap: the first, smallest piece of item K4 (the answer
ledger) and the persistence half of K6 (show the pages that were used). Related:
`docs/specs/152-citation-relevance.md`.

## Problem

While an answer streams, the chat page shows a "cache hit" badge and "📖 page, page" under it. `ChatMessage` has a
`cache_hit` column and a `wiki_slugs` column for exactly that, and the chat page reads both when it renders a
saved conversation. Nothing ever wrote them. After a reload every answer showed no pages and no badge, so the
person could not tell afterwards what an answer had been given or whether it came from the cache.

The label was also a bare 📖 with page names, which reads as "these are the sources" when all Anthill knows is
which pages it handed to the model.

## Requirements

1. The assistant message of a normal chat turn is saved with `cache_hit` and `wiki_slugs`: the same pages the
   stream reported as `meta.wiki_slugs`, which for a cached answer are the pages that grounded it when it was
   made. A turn that failed saves neither, and a fixed answer made by code has no pages.
2. `wiki_slugs` keeps its format, a comma-separated string of slugs (the column is 500 characters). The string is
   built from whole slugs only: a slug is never cut, and one that contains a comma is left out.
3. The saved conversation and the live stream word it the same way: "📖 Pages in context: a, b", with a tooltip
   "Anthill gave these wiki pages to the model. It does not show which of them the answer relied on." Neither
   says "cited" or "sources".
4. No migration: the columns exist. A message saved before this change shows no pages, as before.

## Known limits

- The pages are slugs. A slug carries no scope, so it is not a link, and two wikis with a page of the same name
  cannot be told apart. Linking needs the scope kept with the slug, which is a later step.
- It records what was given to the model, not what the answer used. Which pages were retrieved but filtered out
  of the list (`_filter_citations`), memory, the profile, principles and the conversation history are not
  recorded. The full ledger (item K4) records all of that.
- The list is the pages that passed the similarity filter, as before; it is not every page that was retrieved.
- If a page is later edited or deleted, the saved name stays; nothing says it changed since.
- A turn answered by an agent, a research run or an escalation to a provider saves what the normal-chat branch
  gave it, which for the first two is nothing.

## Acceptance

- Ask a question the wiki can answer, reload the conversation: the answer shows "Pages in context: <page>".
- Ask it again in a new chat: the answer is served from the cache, and after a reload it shows both "cache hit"
  and the page.
- A question that used no page shows no line. A failed turn saves no pages and no badge.
- The live stream and the saved page use the same wording, and the tooltip says the pages are not a claim about
  what the answer relied on (`tests/test_answers_remember_sources.py`). The two route tests fail on the code before
  this change.
