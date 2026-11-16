# Spec: the sources under a chat answer are the pages the question is about

Status: implemented. Lane: `pillar:knowledge`. Issue: #152. Related: #150 (title and quote fidelity), #149 (de-duplication).

## Problem

Chat shows the wiki pages an answer came from, so a user can check a fact. The list was every page retrieved for
the turn: `ask` returned `[p.stem for p in pages]` and `ask_stream` passed the same list to `on_context`. A page is
retrieved when its similarity to the question is at least `MIN_GROUNDING_SIM` (0.35) and it survives MMR
diversity, and with three or four short pages in a wiki all of them pass. Nothing asked whether the answer used a
page. Asking "What did the courier say about maps?" (#152) gave a correct answer from the courier note, with the
bakery-owner interview and a second copy of the courier note under another title listed beside it. A user
checking a client fact finds two of three sources irrelevant and stops trusting the rest.

## Requirements

1. The model's grounding is unchanged: the same pages are retrieved, ranked and put in the prompt as before.
   Only the list of sources returned to the caller changes.
2. A retrieved page is listed only when its cosine similarity to the question is within `CITATION_SIM_MARGIN`
   (0.10) of the best retrieved page. The best page is always listed. Pages in a near tie are all listed.
3. Pages whose provenance tag (`read_provenance`) names the same source file are listed once, as the one with the
   highest similarity. A duplicate upload therefore cannot appear twice. Pages with no source tag are never
   grouped.
4. Listed pages keep the order in which they were retrieved.
5. The filtered list is what `ask` returns, what `ask_stream` hands to `on_context`, and what is stored with the
   cached answer, so a later cache hit shows the same sources as the turn that produced it.
6. The filter uses the similarities retrieval already computed: `_rank_by_embedding` records them in an optional
   `scores` mapping that `_relevant_pages` and `_merge_relevant` pass along, and no page is embedded or scored a
   second time to decide the citations.
7. When only one page was retrieved, or any retrieved page has no recorded score, every retrieved page is listed
   as before.

## Known limits

- The margin is a relative cosine gap, set from reasoning about small wikis, not tuned on a corpus, and not
  checked against a real embedding model here. Too small a margin hides a page that helped; too large a one
  keeps noise. It is one named constant.
- Similarity is between the question and the page's first 2,000 characters, as retrieval does. A page that
  answers from text past that point is judged on its opening.
- The list is not a record of what the model used. A page that is close in similarity but ignored by the model is
  still listed, and a page the model drew on that scores far below the best one is not.
- Duplicate detection needs the provenance source tag. Two pages with the same content but no tag, or a note that
  was re-uploaded after an edit (a different file), are not grouped.
- Retrieval itself, including the cosine floor and MMR, is untouched; a wiki where several pages are truly about
  the question still lists several.
- Pages found by the keyword fallback or Meilisearch have no recorded similarity, so they are all listed.

## Acceptance

- With a courier note, a bakery-owner interview and a retitled copy of the courier note (same source file) in the
  wiki, "What did the courier say about maps?" lists one page, from the courier note's source, while the prompt
  sent to the model still contains all three pages.
- The same holds for `ask_stream`: `on_context` receives the one slug.
- Unrelated pages are dropped, the top page is always kept, near ties are kept, and pages from one source collapse
  to one.
- Tests: `tests/test_citation_relevance.py` (model-free; embeddings are faked).
