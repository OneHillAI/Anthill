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
2. The words of the passages go into a SQLite full-text index, `<workspace>/.cache/doc_passages.sqlite3`, beside
   the page vectors. The index is contentless: it keeps the words and a row per passage with its offsets (character
   and byte), page, heading and a CRC of the passage's bytes, and not the text. The text stays in
   `text/<name>.txt`, the one copy. It is keyed by the text's name and a SHA-256 of its content: an unchanged text
   is not split or indexed again, a changed text replaces its passages, and a text that is gone drops them.
3. `DocIndex.search(query, limit)` returns the best passages by BM25, best first, for any of the words of the
   query (the words of two or more letters or digits, at most 12). Syntax characters in a query are never
   interpreted. Case and accents do not matter. Each hit's text is read from the text file by its byte offsets and
   checked against its CRC; a hit whose file is gone or whose bytes there are not the ones indexed is left out,
   and the next sync replaces it.
4. Nothing calls a model. Indexing and searching are SQLite and string work.
5. Indexing is in the background. `ingest.keep_extracted_text` queues the kept text for one worker thread
   (`index_soon`, a queue of at most 1,000) and returns; the upload does not wait for the index, and a full queue
   is left to the startup pass.
6. At startup a background thread (`sync_all`) brings every workspace's index in line with its kept texts: it
   indexes the texts that are new or changed, which includes every text kept before the index existed, and drops
   the ones that are gone. A workspace with no kept text and no index creates no file.
7. It is a disposable performance layer and never raises: an index that cannot be opened or written, an SQLite
   without contentless full-text tables (SQLite 3.43), or a text that cannot be read is skipped; a plainly corrupt
   file is deleted and rebuilt. A text file is read as exact bytes, so a byte that is not valid UTF-8 does not
   break the offsets. `ANTHILL_INDEX_DOCS=0` switches the background work off.
8. `keep_extracted_text` writes the text as UTF-8 bytes, replacing a character UTF-8 cannot encode (a lone
   surrogate from a damaged PDF). It used `write_text`, which raised `UnicodeEncodeError` for such a character;
   that is not an `OSError`, so it escaped the best-effort handler and failed the upload. The index depends on the
   file being exact bytes, so this is fixed here.
9. Nothing uses the index for answers yet, and nothing in the chat or the pages changes.

## Measured

On a Mac, with random English-like text (`doc_index` alone, no model):

| Text | Passages | Index | Re-index, unchanged | Index file |
|---|---|---|---|---|
| 100,000 characters | 171 | 4 ms | under 1 ms | 0.1 MB |
| 1,000,000 | 1,704 | 19 ms | under 1 ms | 0.7 MB (0.73x the text) |
| 5,000,000 (the cap in #225) | 8,553 | 86 ms | about 2 ms | 4.0 MB (0.81x the text) |

Search, by BM25 over 1, 20 and 200 documents of 200,000 characters (0.2, 3.5 and 27.6 MB of index), including
reading the five hits from their files: 0.14 to 0.31 ms, 0.28 to 2.4 ms and 0.95 to 19.8 ms, for queries of one to
eight words.

The first version of this change stored a copy of every passage in the index. Measured against this one on the
same texts it was 1.7 times the size of the text instead of 0.65 times, and its search was no faster once the hits
were read from the files here (0.12 to 2.6 ms against 0.12 to 2.2 ms over 20 documents; 0.8 to 21 ms against 0.8
to 22 ms over 200). The copy bought nothing and was a second plaintext copy of every document to protect and delete,
so it was dropped.

## Known limits

- Deleting a document must remove its text, its passages here and what was cached from them (Knowledge roadmap
  item K3). Passages are read back from the text file, so a text removed first leaves no hits, not wrong ones.
  The index holds the words of every passage (as lowercase terms), so it is not free of the document's content;
  like the originals and the pages it is not encrypted.
- Ranking is by whole words. In a script written without spaces (Chinese, Japanese, Thai) a word only matches as
  part of the whole unbroken run it sits in, so those documents match poorly. Step 2 should measure the trigram
  tokenizer or embeddings for them.
- Ranking is keyword ranking only; a passage that says the same thing in other words is not found. Whether to add
  embeddings is a step 2 question, to be decided on a labelled set.
- The startup pass reads and hashes every kept text, in the background and without a model, to find what changed.
- The index needs SQLite 3.43 (September 2023) with full-text search (FTS5), for `contentless_delete`. On an older
  build it is disabled silently and answers behave as they do today. The one used for development is 3.53; the
  packaged app's has not been checked.
- Only texts kept by #225 are indexed. A document uploaded before #225 is indexed once it is uploaded again.

## Acceptance

- Every passage of a sample set (short, many paragraphs, one long paragraph, no spaces, Unicode, Windows line
  endings, headings) reads back from the text by its offsets, in order, none longer than 800 characters, with no
  non-space character lost.
- PDF pages are counted and no passage spans two; headings are carried; a non-PDF has no page numbers.
- A search finds a passage with its document and offsets; the better match ranks first; syntax characters never
  raise; accents and case do not matter.
- An unchanged text writes nothing, a changed one replaces its passages, a dropped one is gone.
- The index file does not contain the text (a distinctive phrase of a document is not in its bytes), and it is
  smaller than the text it covers.
- A hit whose text file changed or is gone is left out and returns when the file is as indexed again. Non-ASCII
  text (accents, Thai, Japanese) and invalid UTF-8 bytes read back exactly by their offsets.
- A lone surrogate in an extracted text is replaced and kept, and does not fail the upload.
- A damaged index file is rebuilt; an SQLite error, or a folder that cannot be created, returns nothing and does not
  raise.
- Sync indexes new and changed texts, drops removed ones, creates no file for a workspace with no kept text, and
  indexes a text kept before the index existed.
- Ingest queues the index and does not build it; a note, or `ANTHILL_INDEX_DOCS=0`, queues nothing
  (`tests/test_doc_index.py`).
