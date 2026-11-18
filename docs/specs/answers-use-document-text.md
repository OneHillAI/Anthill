# Spec: answers can use a document's own text

Status: implemented (step 2 of 2). Lane: `pillar:knowledge`. Issue: #248, the tracking issue for step 2 of #226 (#226
was closed by GitHub when #231, step 1, merged). Knowledge roadmap:
K1 (the source is not only a summary), K6 (what an answer was given) and K18 (raw sources searchable). Builds on
`docs/specs/keep-extracted-text.md`, `docs/specs/index-extracted-text.md` and `docs/specs/answers-remember-sources.md`.

## Problem

An upload becomes one wiki page, the model's summary, and answers were grounded only on pages. Run on a real local
model (`qwen2.5:3b`) with a three-page handbook, two defects showed up that a person cannot fix:

- the summary changed a fact: the document says flour comes from "the Brandt mill", the page said "Flurf", and the
  chat answered "Flurf";
- the summary dropped a fact: the document says the butter price rises by eight percent for a late order; the
  summary does not, and the chat answered that the information is not provided.

The document's own text was kept (#225) and indexed (#231) and not used.

## Requirements

1. For each question, `doc_context.document_passages` searches the passage index of every workspace that grounds
   the chat (the same ones the pages come from, so scope is unchanged) and keeps at most two passages
   (`MAX_PASSAGES`).
2. A passage qualifies only if it contains at least two distinct rare words of the question (`MIN_RARE`). A word is
   rare when it is in at most a quarter of the indexed passages, at least one and at most 50 (`doc_index.RARE_*`).
   Counting rare words moves less with the size of the wiki than a BM25 score did, but it is not size-proof (see
   the measurements and the known limits). A
   question with fewer than two rare words, such as a greeting, a general-knowledge question or a one-word query,
   gets no passage. Passages are ordered by the number of rare words they contain, then by BM25.
3. The passages go first in the wiki part of the reference, under a header that says they are the user's original
   words and preferred to any summary, each labelled with the document's name (without the hash of the kept copy) and,
   for a PDF, its page. A retrieved page whose provenance source is one of those documents gets a note above it:
   "[AI summary of <name>. It may contain errors: prefer the original text above.]". Memory, the profile and the
   principles stay above, as before.
4. The answer reports the documents it was given: `ask()` and `ask_stream()` add one `doc:<stored name> (page n, m)`
   entry per document, under the name its text is kept by (`report-1a2b3c4d5e6f.pdf`), to the list of sources they
   already return (`on_context`, the answer cache). The chat route separates them from the page slugs, turns each
   into the name the person uploaded (`report.pdf (page 2)`), sends `meta.documents` to the page, and saves them in the new column
   `ChatMessage.doc_sources` (one per line, at most 500 characters, whole entries). The chat shows
   "📄 Documents in context: report.pdf (page 2)" beside "Pages in context", live and after a reload, with a note
   that these are passages given to the model and not a claim about what the answer relied on. A failed turn saves
   none.
5. Every consumer of the sources list separates the documents from the pages with `split_sources`, so a `doc:` prefix
   or a hashed file name never reaches a person or a client: the terminal (`anthill ask`, `anthill chat` and its
   `/context`) prints "context: <pages>; documents: report.pdf (page 2)", and the OpenAI-compatible endpoint
   (`node_agent`) keeps `anthill.wiki_slugs` to pages, as it always held, and returns documents in
   `anthill.documents`. The web client passes on the page list the chat route already separated. The callers that
   discard the list (the MCP server, the Slack and Discord bots) are unaffected.
6. It costs one index query per workspace and no model or embedding call. A question that gets no passage has the
   same prompt, and the same calls, as before. A wiki with no kept document text is searched not at all and its
   index file is not created. It never raises.
7. `ANTHILL_DOC_CONTEXT=0` switches it off.
8. The answer cache follows the documents (`cache_freshness`). A cached answer that was given passages of a document
   is dropped when that text was changed after the answer, and is not served (and kept, as for a missing page) when
   the text is gone from the workspaces of this chat. A text that is new or changed after any cached answer and that
   contains at least `MIN_RARE` rare words of its question (the index is searched for the cached question) drops that
   answer, which is what removes an "I do not know" answer once the document that answers it is uploaded. A new text
   the index has not caught up with yet counts as one that could answer. Nothing changes for answers that cite pages
   only, and page retrieval, the scope rules and what step 1 stores are untouched.
9. `doc_index.support()` tries, in a memory database, the one statement the index needs (a contentless FTS5 table with
   `contentless_delete`, SQLite 3.43), so the answer is for the SQLite the process really runs, a packaged app's
   included. Where it fails, the startup pass logs a warning with the raw reason (it used to be debug level) and the
   Files tab says, in plain words, that document search is off and answers use the AI's summary pages only, that the
   SQLite built into this copy of Anthill is too old or lacks a feature (it needs 3.43 or newer with full-text
   search), and that updating Anthill, or a newer Python when running from source, fixes it. The raw error stays in the
   log.

## Measured

On a Mac with `qwen2.5:3b` and no network, documents ingested through the real upload code:

- **The defect.** With the exact summary the model wrote in the failing case (the changed and the dropped fact), the
  four questions of the handbook were answered correctly 6 times in 12 tries without this change and 12 in 12 with
  it. Without it the chat said "Flurf" and "the information is not provided" every time.
- **Where summaries were good.** With four short documents and the summaries the model wrote on that run, 14 of 16
  questions were counted correct without and 15 of 16 with. Every answer counted as a miss, in both runs, was
  correct but worded "8%" or "3%" where my checker looked for "eight percent" or "3 percent". So on that run the
  summaries already had the facts and the change made no difference to correctness, and no answer got worse.
- **Time.** First word of the answer, over the 16 questions a document answers: median 1.15 s without, 1.70 s with;
  mean 1.12 s and 1.50 s; whole answer mean 1.56 s and 2.04 s. The two passages add about 400 tokens to read. For four
  questions no document answers: 0.22 s both ways. The search itself takes 0.14 to 0.73 ms per question with one
  document, up to 0.56 ms with 20 and up to 3.9 ms with 200 documents of 200,000 characters.
- **The gate.** On a hand-written set of four documents, 16 questions a document answers and 10 that none does
  (`tests/test_document_passage_gate.py`): the passage with the answer is among the two chosen for 16 of 16 (with
  plain keyword order it was first for 15 of 16, and second for the other one); none of the 10 gets a passage. Plain
  keyword ranking returns a passage for some of the 10.
- **A bigger wiki.** The four documents among 46, 196 and 496 generated ones, with English function words and some of
  the unrelated questions' own words scattered through them (three draws each, the same result every time): the answer
  is among the two chosen for 16 of 16 questions at 50 documents and 15 of 16 at 200 and 500; 2 of the 10 unrelated
  questions got a passage at 50 documents and none at 200 and 500; a search takes 0.7 to 2.2 ms. An earlier set of
  generated documents with no English function words gave 2 of 10 at every size, because "is" and "at" looked rare in
  it, which English text does not do. A BM25 floor did not survive the change of size (the same question scored 2.4
  in a one-document wiki and 6.9 in a four-document one). These wikis are generated, not real: how the gate behaves on
  real wikis is not measured.
- **The cache check.** For one cached answer older than 504 kept texts, the worst case, ruling on it takes 2.6 ms, and a
  lookup rules on at most the few candidate rows it found, not on every cached answer.

## Known limits

- The gate and the choice of two passages were measured on a small set written for this and on generated wikis of up
  to 500 documents, not on real wikis. What counts as rare (a quarter of the passages, at most 50) and the two words
  are starting points. A stray passage costs about half a second and can distract a small model.
- With many near-duplicate documents (50 and 200 copies of the same four with every name and number changed) the 16
  questions still found their passage for 12 and then 10: a question made of words that every copy contains cannot be
  told apart, so it gets no passage and is answered as before. No wrong passage was returned for the ten unrelated
  questions in that case. The gate fails towards today's behaviour, not towards a wrong passage.
- Keyword matching: a question that shares fewer than two rare words with the passage, or says the same thing in
  other words, gets none and is answered as before. Scripts written without spaces (Chinese, Japanese, Thai) match
  poorly (see the index spec).
- Putting the original first and labelling the summary raises the chance that the model uses it; it does not force
  it. A small model can still prefer a summary that disagrees.
- A document uploaded before #225 has no kept text and is not searched until it is uploaded again.
- The page link under the answer names the document; it is not yet a link, and the summary page that was also in
  the context is still listed as a page.
- The index needs SQLite 3.43 (see the index spec); without it nothing changes from today, and the Files tab and the
  log say so. The installed Anthill 1.1.1 sidecar (a PyInstaller binary) carries its own SQLite, with FTS5 and the
  `contentless_delete` code compiled in and a source id dated 2024-01-30, which reads as SQLite 3.45.1; the installed
  Beta 1.1.0-rc.2 has the identical module. That was read from the files, nothing was run, and the version number
  itself was not printed. The next release build is not checked: the build pins only `setup-python` to "3.11". A
  self-check beside `--selfcheck-cache` would settle it for each release build.

## Acceptance

- The passage that answers a question is first in the prompt, labelled with the document and page; the summary page
  of that document carries the note; with it off, or with no kept text, or for an unrelated question, the prompt and
  the number of embedding calls are exactly those of before.
- A fact the summary never had is found when no page matches at all.
- Only the workspaces that ground the chat are searched.
- The chat saves and shows the documents, live and after a reload, and not for a failed turn.
- A cached answer that used a document is dropped when its text changes, not served when it is gone and served again
  when it is back; an "I do not know" answer is dropped when the document that answers it arrives, and kept when a
  document about something else does; a text the index has not reached yet drops it.
- A document in a wiki outside the chat's scope is not used; one blended in is.
- The terminal and the OpenAI-compatible endpoint show documents under the name uploaded, never as `doc:` plus the
  stored name (`tests/test_document_sources_reach_every_consumer.py`).
- A database made before the `doc_sources` column opens, gets the column at startup, and its old answers read and show
  no documents.
- Where this SQLite cannot run the index, `support()` says why, the startup pass warns at warning level and queues
  nothing, and the Files tab shows the notice (`tests/test_answers_use_document_text.py`).
