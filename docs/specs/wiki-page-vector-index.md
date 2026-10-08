# Spec: embed each wiki page once, not on every question

Status: implemented. Lane: `pillar:knowledge`.
Relates to: `anthill/wiki/ask.py` (`_rank_by_embedding`, `_relevant_pages`, `_merge_relevant`), `anthill/wiki/page_index.py`, `anthill/cache/page_vectors.py`, `anthill/cache/embedder.py`.

## 1. Problem

Before Anthill answers, it picks the wiki pages to ground the answer on. `_rank_by_embedding` does this by
embedding the question and then embedding every candidate page (the first 2000 characters after the
frontmatter), one call to the local embedding model per page. Nothing was kept between questions, so every
question paid for the whole wiki again, and `ask()` and `ask_stream()` both do it before the first word
of the answer can start.

Measured on this Mac with the real `bge-m3` model on the local Ollama, a synthetic workspace of 60 pages of
2000 characters each, one question, three runs: about 10.0 seconds per question. The cost grows with the
wiki, so a bigger wiki meant a slower first word for every question, and an unchanged page was embedded
again and again for the same vector.

## 2. Requirements

- R1. The system MUST keep one vector per wiki page on disk, per workspace, next to the semantic cache
  (`<workspace>/.cache/page_vectors.sqlite3`), and MUST NOT add a dependency.
- R2. A stored vector MUST be keyed by the page path relative to the workspace, a hash of exactly the text
  that is embedded (the first 2000 characters after `strip_frontmatter`), and the embedding model name
  (`embedder.MODEL_NAME`). A vector is used only when all three match, so an edited page or a changed model
  embeds again and a stale vector is never served.
- R3. `_rank_by_embedding` MUST take page vectors from the index and embed only the pages that miss (new or
  changed), then store them. Its ranking MUST stay exactly what it was for the same vectors: scores, order,
  `MIN_GROUNDING_SIM` filtering, top-k and MMR selection are unchanged.
- R4. Rows for pages that no longer exist MUST be dropped when the whole wiki of a workspace is ranked. A
  ranking over only some pages (the blended re-rank across personal, team and org wikis) MUST NOT drop rows
  for pages it was not asked about.
- R5. An index that is corrupt, unreadable, unwritable or locked MUST NOT change what the user sees: the
  pages are embedded directly, as before. A file that is plainly corrupt is deleted and rebuilt. If
  embedding fails part way, the vectors already made are kept and the embedder's error reaches the caller
  as it did before (the callers already fall back to keyword search).
- R6. It MUST be safe from several threads: each call uses its own short-lived connection and one write
  transaction, and a busy database is waited for briefly and then skipped.
- R7. No answer content, prompt, threshold, `k` or semantic answer cache changes. The slim app with no
  embedder keeps its existing degradation: the question is embedded first, and when that fails the index is
  never touched.
- R8. The index MUST be built before a question is asked, not at the first question. When the app starts, a
  background pass MUST embed the pages that every workspace on the install lacks (organisation wiki, the
  legacy single-node personal wiki, each per-user and per-team wiki). When a page is saved through
  `Workspace.write_page`, it MUST be queued to one background worker that embeds it, without delaying the save.
  A workspace with no pages MUST cost nothing: no embedding call and no index file. The background work is best
  effort: it MUST NOT raise, MUST NOT let one unreadable page stop the rest, and can be switched off with
  `ANTHILL_WARM_PAGE_INDEX=0`. When the embedding model is not ready yet (a slow boot, or its first download)
  the start-up pass waits and retries for a bounded time (about thirteen minutes), and a saved page retries for a
  few minutes, instead of giving up. Once one saved page has used up its waits, later pages check once without
  waiting until the model is seen ready again, and the queue of saved pages is bounded (1000), so an install that
  never gets the model neither sleeps through every page nor grows without limit. A page skipped as unreadable
  keeps its row. Embedded pages are written to the index in batches of 20 as the work goes,
  so a warm-up that is interrupted (the app quits) keeps what it did. Warming and ranking MUST use the same text and the same keys, so a page
  warmed in the background is a hit for the next question.

## 3. Design

`anthill/cache/page_vectors.py` holds `PageVectorIndex(cache_dir)` with one call, `vectors(items, embed,
model, complete=False)`, where `items` are `(relative path, embedded text)` pairs. One SQLite table
(`page_vectors_v1`: path, model, content_hash, dim, vec) holds the rows; the vector is the raw float32
bytes. A lookup is one `SELECT ... WHERE path IN (...)` per 400 pages. The misses are embedded outside any
database lock and written in `BEGIN IMMEDIATE` transactions of 20, with the rows of deleted pages dropped in the last one.

`anthill/wiki/page_index.py` builds the index ahead of time. `warm_all()` runs once on a daemon thread a few
seconds after startup and calls `warm_workspace(root)` for each workspace root; `Workspace.write_page` calls
`warm_page_soon(root, path)`, which puts the page on a queue served by a single daemon worker. `embed_text(path)`
is the one definition of the embedded text, used by both warming and ranking. A question that arrives before
the warm-up has reached a page, or a page that was copied into the wiki folder by other means, is still
embedded by the question itself, so a missed page is only slower, never wrong.

`_rank_by_embedding` gains two optional arguments, `roots` (the workspace roots the pages belong to) and
`whole_wiki` (the pages are every page of the one workspace in `roots`). Without `roots` it behaves exactly
as before. `_relevant_pages` passes its workspace and `whole_wiki=True`; `_merge_relevant` passes every
workspace root. A page under no root is embedded directly and never stored.

The index file is not encrypted, like the semantic cache beside it. It holds embeddings of text that is
already on disk in the same workspace. It is a disposable cache, not a source of truth: it sits under `.cache`
inside the workspace, and backups tar the whole wiki tree, so it is backed up and restored with the wiki. A
restored index whose hashes no longer match its pages is simply embedded again, and a backup taken in the middle
of a write can at worst pair a page with an older vector that the next edit replaces. Leaving `.cache` out of the
backup is a follow-up.

## 4. Out of scope

- Other places that embed pages per call (the agent's wiki search tool, the near-duplicate check in the
  wiki agent). They can use the same index later.
- Changing the embedding model, the 2000 character window, the thresholds or `k`.
- Sharing one index across workspaces, or across machines.
- A setting or a button for the index. It builds itself and is safe to delete.

## 5. Acceptance criteria

- AC1. Asking a question over N pages embeds N pages the first time and 0 pages for the next question.
- AC2. Editing one page makes the next question embed exactly that page. Changing only the frontmatter, or
  only text past the first 2000 characters, embeds nothing.
- AC3. Deleting a page removes its row on the next question over the whole wiki. A blended ranking over a
  few pages leaves the other rows alone.
- AC4. A changed embedding model name re-embeds every page once and replaces the old rows.
- AC5. A garbage index file, a directory in its place, an unusable `.cache`, and a locked database each give
  the same ranking as before; the garbage file is rebuilt.
- AC6. For the same vectors, the ranking equals the old uncached implementation, cold and warm, for several
  questions and values of `k`, including a question with no related page.
- AC7. Eight threads asking at once on a cold index all get the correct ranking and leave one complete index.
- AC8. Through `ask()` and `ask_stream()`, later questions embed no page.
- AC9. With the real `bge-m3` model, the same question over 60 pages of 2000 characters takes about 10 seconds
  without the index and on the first question with a cold index, and a few milliseconds on later questions (2 ms
  when the index is already open in the process, 8 ms for the first question right after the background warm-up,
  which also opens the index and reads the 60 pages for the first time).
- AC10. Two workspaces keep separate indexes, even when a page has the same name in both.
- AC11. After the warm-up has run, the first question over the wiki embeds no page. A workspace with no pages,
  or no wiki folder, makes no embedding call and creates no index file.
- AC12. Saving a page queues exactly that page, and the worker step embeds it once. With the warm-up switched
  off nothing is queued. A failing or missing embedding model never raises to the caller.
- AC13. With the real `bge-m3` model: warming 60 pages in the background takes about 10 seconds and the first
  question after it takes under 10 milliseconds; saving a page returns in under 50 milliseconds and the page is
  in the index a fraction of a second later; a 3-page wiki warms in under half a second; no wiki costs nothing.
- AC14. The start-up pass waits for the model and stops after its last wait; a saved page waits a shorter time; one
  unreadable page is skipped; progress is written in batches; app start-up starts the warm-up thread when it is
  enabled and not otherwise; the worker loop embeds what it is given; a deleted page's row is gone after a warm-up.
