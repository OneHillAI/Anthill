# Spec: an index of the kept document texts

Status: implemented (step 1 of 2). Lane: `pillar:knowledge`. Issue: #226. Knowledge roadmap: items K1 (the source
layer) and K18 (raw sources searchable). Builds on `docs/specs/keep-extracted-text.md`. Relates to
`docs/specs/wiki-page-vector-index.md` and `docs/specs/page-passages.md`.

## Problem

#225 keeps the text extracted from a document as `text/<original's name>.txt`. Nothing can search it: retrieval
reads only the wiki pages, which are summaries. Step 2 of #226 will let a question use the document's own text.
That needs an index to search, built without slowing chat or the upload.

## Requirements

1. `anthill/wiki/doc_index.py` splits a kept text into passages of at most 800 characters (`PASSAGE_CHARS`), packed
   from whole paragraphs. A paragraph longer than a passage is cut at a line end, a sentence end or a space when one
   lies in the second half of the piece, else mid-word. Every passage records its character offsets
   (`text[start:stop]` is the passage), its number, the Markdown heading above it and, for a PDF, the page it is on,
   counted from the `---` page-break lines the reader writes (those lines are not text, and a passage never spans two
   pages). Windows line endings are handled.
2. The passages go into a SQLite full-text index, `<workspace>/.cache/doc_passages.sqlite3`, beside the page
   vectors. It holds the passage text as well as its position, so a search needs no file reads. It is keyed by the
   text's name and a SHA-256 of its content: an unchanged text is not split or indexed again, a changed text
   replaces its passages, and a text that is gone drops them.
3. `DocIndex.search(query, limit)` returns the best passages by BM25, best first, for any of the words of the
   query (the words of two or more letters or digits, at most 12). Syntax characters in a query are never
   interpreted. Case and accents do not matter.
4. Nothing calls a model. Indexing and searching are SQLite and string work.
5. Indexing is in the background. `ingest.keep_extracted_text` queues the kept text for one worker thread
   (`index_soon`, a queue of at most 1,000) and returns; the upload does not wait for the index, and a full queue
   is left to the startup pass.
6. At startup a background thread (`sync_all`) brings every workspace's index in line with its kept texts: it
   indexes the texts that are new or changed, which includes every text kept before the index existed, and drops
   the ones that are gone. A workspace with no kept text and no index creates no file.
7. It is a disposable performance layer and never raises: an index that cannot be opened or written, an SQLite
   without full-text search, or a text that cannot be read is skipped; a plainly corrupt file is deleted and
   rebuilt. `ANTHILL_INDEX_DOCS=0` switches the background work off.
8. Nothing uses the index for answers yet, and nothing in the chat or the pages changes.

## Measured

On a Mac, with random English-like text (`doc_index` alone, no model):

| Text | Passages | Split | Index | Re-index, unchanged | Index file |
|---|---|---|---|---|---|
| 100,000 characters | 171 | under 1 ms | 4 ms | under 1 ms | 0.2 MB |
| 1,000,000 | 1,704 | 4 ms | 16 ms | a few ms | 1.8 MB |
| 5,000,000 (the cap in #225) | 8,553 | 21 ms | 83 ms | about 1 ms | 9.3 MB |

The index file is about twice the size of the text it covers, because it holds a copy of the passages. Search, by
BM25 over 1, 20 and 200 documents of 200,000 characters (349, 6,859 and 68,465 passages; 0.4, 7.5 and 69 MB):
0.1 to 0.2 ms, 0.2 to 1.9 ms and 0.6 to 17.8 ms for queries of one to eight words.

## Known limits

- It stores a second copy of every passage (about the size of the text again) so that a search needs no file
  reads. Deleting a document must remove its text, its passages here and what was cached from them (Knowledge
  roadmap item K3). Like the originals and the pages it is not encrypted.
- Ranking is by whole words. In a script written without spaces (Chinese, Japanese, Thai) a word only matches as
  part of the whole unbroken run it sits in, so those documents match poorly. Step 2 should measure the trigram
  tokenizer or embeddings for them.
- Ranking is keyword ranking only; a passage that says the same thing in other words is not found. Whether to add
  embeddings is a step 2 question, to be decided on a labelled set.
- The startup pass reads and hashes every kept text, in the background and without a model, to find what changed.
- SQLite builds without full-text search (FTS5) disable the index silently. The one used for development has it;
  the packaged app's has not been checked.
- Only texts kept by #225 are indexed. A document uploaded before #225 is indexed once it is uploaded again.

## Acceptance

- Every passage of a sample set (short, many paragraphs, one long paragraph, no spaces, Unicode, Windows line
  endings, headings) reads back from the text by its offsets, in order, none longer than 800 characters, with no
  non-space character lost.
- PDF pages are counted and no passage spans two; headings are carried; a non-PDF has no page numbers.
- A search finds a passage with its document and offsets; the better match ranks first; syntax characters never
  raise; accents and case do not matter.
- An unchanged text writes nothing, a changed one replaces its passages, a dropped one is gone.
- A damaged index file is rebuilt; an SQLite error, or a folder that cannot be created, returns nothing and does not
  raise.
- Sync indexes new and changed texts, drops removed ones, creates no file for a workspace with no kept text, and
  indexes a text kept before the index existed.
- Ingest queues the index and does not build it; a note, or `ANTHILL_INDEX_DOCS=0`, queues nothing
  (`tests/test_doc_index.py`).
