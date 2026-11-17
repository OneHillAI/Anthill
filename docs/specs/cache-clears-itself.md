# Spec: the answer cache drops answers that the wiki has outgrown

Status: implemented. Lane: `pillar:knowledge`. Knowledge roadmap: the first part of item K7 (derived data is
invalidated when what it was built on changes). Related: `docs/specs/ollama-served-embeddings.md`.

## Problem

Anthill answers a repeated question from a semantic cache (`SemanticCache`, one LanceDB table per workspace). A
row holds the question, its vector, the answer and the slugs of the pages that grounded it. Nothing ever
compared a row with the wiki it came from:

- After a page was edited, replaced or added, a repeat of an earlier question still returned the old answer.
  A question answered before the wiki knew about the topic kept returning that answer after a page about it
  arrived.
- `CacheStore.add` appended. Storing a question again left two rows for the same prompt, tied on similarity, so
  the older answer could still be the one served.

## Requirements

1. A `SemanticCache` can be given a `judge`. For each candidate row that passes the similarity threshold and the
   correctness guard, the judge returns `fresh` (serve it), `stale` (delete the row and go on) or `skip` (do not
   serve it now, keep the row). Without a judge the behaviour is unchanged.
2. The wiki judge (`anthill/wiki/cache_freshness.py`), built for the workspaces that ground the chat (the base
   wiki and any extra ones), rules as follows:
   - a row whose time cannot be read is `stale`;
   - a cited slug with no page in these workspaces is `skip` (deleted, or outside the scope chosen for this chat;
     the row may still hold elsewhere);
   - a page modified after the row was made is `stale` if the row cited it;
   - any other page modified after the row was made is `stale` when its vector is at least the retrieval grounding
     floor (`MIN_GROUNDING_SIM`) similar to the cached question, or when its vector cannot be had;
   - otherwise `fresh`.
3. The check reads one file timestamp per page. A page vector is needed only for a page that changed after the
   row was made, and it comes from the workspace's persistent page-vector index, so a changed page is embedded
   once. When nothing changed, no page is embedded.
4. `CacheStore.add` replaces the row with the same id (a hash of the prompt) instead of appending, and
   `CacheStore.delete` removes a row by id. The id is a hex digest and is checked before it goes into the filter.
5. `ask()` and the chat route give their cache the judge. The cache's table, schema and files are unchanged, so no
   existing cache is migrated or emptied.

## Known limits

- The pages that were in the model's context but not cited are not tracked per row. A change to one is caught only
  through the relevance rule in requirement 2.
- A deleted page that was in context but not cited is not noticed. Deleting pages in the app, with its cache
  clean-up, is Knowledge roadmap item K3.
- Memory items, the profile and principles also shape an answer. Changing or deleting one does not drop cached
  answers. That needs a record of what each answer depended on (a schema change) and is not done here.
- The scope picker ("personal", "team", "all") is not part of the cache key. A row citing a page outside the
  chosen scope is skipped by the missing-page rule; an answer with no cited pages is not.
- The org-wide index (`ANTHILL_ORG_URL`) is not covered.
- A row made before this change has the same time and slugs as any other, so it is judged the same way: an old
  answer whose cited page was edited since is dropped the first time it would have been served.
- Timestamps are file modification times. A page written during the seconds an answer is being generated can look
  older than the answer.

## Acceptance

- Storing the same question twice leaves one row, with the second answer.
- With an unchanged wiki a hit is served and no page is embedded to decide it.
- A changed cited page drops the answer, and the row is gone. A changed, unrelated page keeps it.
- A new page about the question drops an answer made without it. A new page about something else keeps it.
- An answer citing a missing page is not served and is kept, and it is served again when the page returns.
- Through the real chat route: an unchanged wiki is answered from the cache with no model call, an edited page is
  answered afresh, and the fresh answer is cached again (`tests/test_cache_clears_itself.py`). The first and
  last of these fail on the code before this change.
- Measured, not a requirement: the check took about 0.3 ms with 100 pages, 1.7 ms with 500 and 8 ms with 2,000.
